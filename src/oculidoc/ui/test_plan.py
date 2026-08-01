"""Clinical task order, persisted current plans, and the desktop plan editor."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from oculidoc.config import Settings
from oculidoc.domain import Patient
from oculidoc.lan_control import utc_now_text
from oculidoc.task_configs import (
    TaskConfigConflict,
    TaskConfigStore,
    task_config_from_dict,
    task_config_to_dict,
)
from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionSetupDialog,
)
from oculidoc.tasks.gaze_games import GazeGameConfig, GazeGameSetupDialog
from oculidoc.tasks.image_choice import ImageChoiceConfig, ImageChoiceSetupDialog
from oculidoc.tasks.instruction_fixation import (
    InstructionFixationConfig,
    InstructionFixationSetupDialog,
)
from oculidoc.tasks.multiple_choice import (
    MultipleChoiceConfig,
    MultipleChoiceSetupDialog,
)
from oculidoc.tasks.screen_keyboard import (
    ScreenKeyboardConfig,
    ScreenKeyboardSetupDialog,
)
from oculidoc.tasks.tracking_ball import (
    TrackingBallConfig,
    TrackingBallSetupDialog,
)
from oculidoc.tasks.visual_preference import (
    VisualPreferenceConfig,
    VisualPreferenceSetupDialog,
)


class BinaryAxisOrder(StrEnum):
    HORIZONTAL_FIRST = "horizontal_first"
    VERTICAL_FIRST = "vertical_first"


@dataclass(frozen=True, slots=True)
class ClinicalTaskDefinition:
    step_id: str
    clinical_number: str
    module_id: str
    title: str
    selected_by_default: bool
    estimated_minutes: int
    game_mode: str | None = None


CLINICAL_TASK_ORDER: tuple[ClinicalTaskDefinition, ...] = (
    ClinicalTaskDefinition(
        "eye_observation",
        "0",
        "eye_observation",
        "眼动采集与复核",
        False,
        3,
    ),
    ClinicalTaskDefinition(
        "visual_preference",
        "1",
        "visual_preference",
        "视觉偏好",
        True,
        4,
    ),
    ClinicalTaskDefinition(
        "tracking_ball",
        "2",
        "tracking_ball",
        "追踪球",
        True,
        3,
    ),
    ClinicalTaskDefinition(
        "gaze_games:garden",
        "3a",
        "gaze_games",
        "眼动游戏·点亮花园",
        True,
        2,
        "garden",
    ),
    ClinicalTaskDefinition(
        "gaze_games:treasure_hunt",
        "3b",
        "gaze_games",
        "眼动游戏·视觉寻宝",
        True,
        4,
        "treasure_hunt",
    ),
    ClinicalTaskDefinition(
        "gaze_games:starlight_route",
        "3c",
        "gaze_games",
        "眼动游戏·星光航线",
        True,
        3,
        "starlight_route",
    ),
    ClinicalTaskDefinition(
        "instruction_fixation",
        "4",
        "instruction_fixation",
        "随指令注视",
        True,
        3,
    ),
    ClinicalTaskDefinition(
        "image_choice",
        "5",
        "image_choice",
        "语音图片选择",
        True,
        3,
    ),
    ClinicalTaskDefinition(
        "binary_horizontal",
        "6",
        "binary_horizontal",
        "左右二分问答",
        True,
        3,
    ),
    ClinicalTaskDefinition(
        "binary_vertical",
        "7",
        "binary_vertical",
        "上下二分问答",
        True,
        3,
    ),
    ClinicalTaskDefinition(
        "multiple_choice",
        "8",
        "multiple_choice",
        "多选项问答",
        True,
        4,
    ),
    ClinicalTaskDefinition(
        "screen_keyboard",
        "9",
        "screen_keyboard",
        "屏幕打字",
        True,
        5,
    ),
)
_DEFINITIONS_BY_ID = {definition.step_id: definition for definition in CLINICAL_TASK_ORDER}


def clinical_task_order(
    axis_order: BinaryAxisOrder | str = BinaryAxisOrder.HORIZONTAL_FIRST,
) -> tuple[ClinicalTaskDefinition, ...]:
    """Return the fixed order, with the one permitted binary-axis exception."""
    selected = BinaryAxisOrder(axis_order)
    if selected is BinaryAxisOrder.HORIZONTAL_FIRST:
        return CLINICAL_TASK_ORDER

    ordered = list(CLINICAL_TASK_ORDER)
    horizontal = next(
        index
        for index, definition in enumerate(ordered)
        if definition.step_id == "binary_horizontal"
    )
    vertical = next(
        index for index, definition in enumerate(ordered) if definition.step_id == "binary_vertical"
    )
    ordered[horizontal], ordered[vertical] = ordered[vertical], ordered[horizontal]
    return tuple(ordered)


def default_rest_after_step_ids(
    axis_order: BinaryAxisOrder | str = BinaryAxisOrder.HORIZONTAL_FIRST,
) -> tuple[str, str]:
    ordered = clinical_task_order(axis_order)
    binary_steps = tuple(
        definition.step_id
        for definition in ordered
        if definition.step_id in {"binary_horizontal", "binary_vertical"}
    )
    return ("gaze_games:starlight_route", binary_steps[-1])


class TestPlanStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        return self in {
            TestPlanStepStatus.COMPLETED,
            TestPlanStepStatus.ABORTED,
            TestPlanStepStatus.FAILED,
            TestPlanStepStatus.SKIPPED,
        }


SKIP_REASON_PRESETS = frozenset(
    {
        "patient_fatigue",
        "care_interruption",
        "device_unavailable",
        "sensory_or_oculomotor_limit",
        "clinically_not_applicable",
        "other",
    }
)


@dataclass(frozen=True, slots=True)
class TestPlanStep:
    step_id: str
    module_id: str
    selected: bool
    config_revision: int
    block_id: str = ""
    game_mode: str | None = None
    status: TestPlanStepStatus = TestPlanStepStatus.PENDING
    session_id: str | None = None
    retry_count: int = 0
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        definition = _DEFINITIONS_BY_ID.get(self.step_id)
        if definition is None:
            raise ValueError(f"Unsupported clinical test step: {self.step_id}")
        block_id = self.block_id.strip() or self.step_id
        object.__setattr__(self, "block_id", block_id)
        if self.module_id != definition.module_id or self.game_mode != definition.game_mode:
            raise ValueError("Test-plan step identity does not match the clinical order.")
        if not isinstance(self.selected, bool):
            raise TypeError("selected must be a boolean.")
        if (
            isinstance(self.config_revision, bool)
            or not isinstance(self.config_revision, int)
            or self.config_revision < 0
        ):
            raise ValueError("config_revision must be a non-negative integer.")
        if (
            isinstance(self.retry_count, bool)
            or not isinstance(self.retry_count, int)
            or self.retry_count < 0
        ):
            raise ValueError("retry_count must be a non-negative integer.")
        object.__setattr__(self, "status", TestPlanStepStatus(self.status))

        session_id = self.session_id.strip() if self.session_id is not None else None
        skip_reason = self.skip_reason.strip() if self.skip_reason is not None else None
        object.__setattr__(self, "session_id", session_id or None)
        object.__setattr__(self, "skip_reason", skip_reason or None)

        if (
            self.status
            in {
                TestPlanStepStatus.RUNNING,
                TestPlanStepStatus.COMPLETED,
                TestPlanStepStatus.ABORTED,
                TestPlanStepStatus.FAILED,
            }
            and self.session_id is None
        ):
            raise ValueError(f"{self.status.value} steps require a session_id.")
        if (
            self.status
            in {
                TestPlanStepStatus.PENDING,
                TestPlanStepStatus.SKIPPED,
            }
            and self.session_id is not None
        ):
            raise ValueError(f"{self.status.value} steps cannot retain a session_id.")
        if self.status is TestPlanStepStatus.SKIPPED and self.skip_reason is None:
            raise ValueError("Skipped steps require a reason.")
        if self.status is not TestPlanStepStatus.SKIPPED and self.skip_reason is not None:
            raise ValueError("Only skipped steps can contain a skip reason.")

    def start(self, session_id: str) -> TestPlanStep:
        if not self.selected:
            raise ValueError("An unselected test-plan step cannot start.")
        if self.status is not TestPlanStepStatus.PENDING:
            raise ValueError("Only pending test-plan steps can start.")
        return replace(
            self,
            status=TestPlanStepStatus.RUNNING,
            session_id=session_id,
        )

    def finish(self, status: TestPlanStepStatus | str) -> TestPlanStep:
        next_status = TestPlanStepStatus(status)
        if self.status is not TestPlanStepStatus.RUNNING:
            raise ValueError("Only running test-plan steps can finish.")
        if next_status not in {
            TestPlanStepStatus.COMPLETED,
            TestPlanStepStatus.ABORTED,
            TestPlanStepStatus.FAILED,
        }:
            raise ValueError("A running step must finish as completed, aborted, or failed.")
        return replace(self, status=next_status)

    def skip(self, reason: str) -> TestPlanStep:
        normalized_reason = reason.strip()
        if not self.selected:
            raise ValueError("An unselected test-plan step does not need to be skipped.")
        if self.status is not TestPlanStepStatus.PENDING:
            raise ValueError("Only pending test-plan steps can be skipped.")
        if not 1 <= len(normalized_reason) <= 200:
            raise ValueError("A skip reason must contain 1 to 200 characters.")
        return replace(
            self,
            status=TestPlanStepStatus.SKIPPED,
            skip_reason=normalized_reason,
        )

    def undo_skip(self) -> TestPlanStep:
        if self.status is not TestPlanStepStatus.SKIPPED:
            raise ValueError("Only skipped steps can return to pending.")
        return replace(
            self,
            status=TestPlanStepStatus.PENDING,
            skip_reason=None,
        )

    def prepare_retry(self) -> TestPlanStep:
        if self.status not in {
            TestPlanStepStatus.ABORTED,
            TestPlanStepStatus.FAILED,
        }:
            raise ValueError("Only aborted or failed steps can be retried.")
        return replace(
            self,
            status=TestPlanStepStatus.PENDING,
            session_id=None,
            retry_count=self.retry_count + 1,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "step_id": self.step_id,
            "module_id": self.module_id,
            "game_mode": self.game_mode,
            "selected": self.selected,
            "config_revision": self.config_revision,
            "status": self.status.value,
            "session_id": self.session_id,
            "retry_count": self.retry_count,
            "skip_reason": self.skip_reason,
        }

    @classmethod
    def from_dict(cls, value: object) -> TestPlanStep:
        if not isinstance(value, dict):
            raise TypeError("A test-plan step must be an object.")
        return cls(
            block_id=str(value.get("block_id") or value["step_id"]),
            step_id=str(value["step_id"]),
            module_id=str(value["module_id"]),
            game_mode=(str(value["game_mode"]) if value.get("game_mode") is not None else None),
            selected=value["selected"],  # type: ignore[arg-type]
            config_revision=value["config_revision"],  # type: ignore[arg-type]
            status=TestPlanStepStatus(str(value["status"])),
            session_id=(str(value["session_id"]) if value.get("session_id") is not None else None),
            retry_count=value["retry_count"],  # type: ignore[arg-type]
            skip_reason=(
                str(value["skip_reason"]) if value.get("skip_reason") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class TestPlan:
    plan_id: str
    patient_id: str
    created_at_utc: str
    updated_at_utc: str
    steps: tuple[TestPlanStep, ...]
    rest_after_step_ids: tuple[str, ...]
    axis_order: BinaryAxisOrder = BinaryAxisOrder.HORIZONTAL_FIRST
    revision: int = 0

    def __post_init__(self) -> None:
        plan_id = self.plan_id.strip()
        patient_id = self.patient_id.strip()
        if not plan_id or not patient_id:
            raise ValueError("plan_id and patient_id cannot be empty.")
        if not self.created_at_utc.strip() or not self.updated_at_utc.strip():
            raise ValueError("Test-plan timestamps cannot be empty.")
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "patient_id", patient_id)
        object.__setattr__(self, "axis_order", BinaryAxisOrder(self.axis_order))
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ValueError("revision must be a non-negative integer.")
        if not self.steps:
            raise ValueError("A test plan must contain at least one task block.")
        block_ids = tuple(step.block_id for step in self.steps)
        if len(set(block_ids)) != len(block_ids):
            raise ValueError("Test-plan block IDs must be unique.")
        rest_ids = tuple(str(value).strip() for value in self.rest_after_step_ids)
        if len(set(rest_ids)) != len(rest_ids):
            raise ValueError("Rest points cannot contain duplicates.")
        if any(block_id not in set(block_ids) for block_id in rest_ids):
            raise ValueError("Rest points must follow task blocks in this plan.")
        object.__setattr__(self, "rest_after_step_ids", rest_ids)

    @classmethod
    def default(
        cls,
        patient_id: str,
        *,
        config_revisions: Mapping[str, int] | None = None,
        axis_order: BinaryAxisOrder | str = BinaryAxisOrder.HORIZONTAL_FIRST,
    ) -> TestPlan:
        selected_axis_order = BinaryAxisOrder(axis_order)
        revisions = dict(config_revisions or {})
        now = utc_now_text()
        steps = tuple(
            TestPlanStep(
                block_id=definition.step_id,
                step_id=definition.step_id,
                module_id=definition.module_id,
                game_mode=definition.game_mode,
                selected=definition.selected_by_default,
                config_revision=revisions.get(definition.module_id, 0),
            )
            for definition in clinical_task_order(selected_axis_order)
        )
        return cls(
            plan_id=uuid4().hex,
            patient_id=patient_id.strip(),
            created_at_utc=now,
            updated_at_utc=now,
            steps=steps,
            rest_after_step_ids=default_rest_after_step_ids(selected_axis_order),
            axis_order=selected_axis_order,
        )

    @property
    def selected_step_count(self) -> int:
        return sum(step.selected for step in self.steps)

    @property
    def terminal_step_count(self) -> int:
        return sum(step.selected and step.status.is_terminal for step in self.steps)

    @property
    def progress(self) -> tuple[int, int]:
        return (self.terminal_step_count, self.selected_step_count)

    @property
    def next_pending_step(self) -> TestPlanStep | None:
        return next(
            (
                step
                for step in self.steps
                if step.selected and step.status is TestPlanStepStatus.PENDING
            ),
            None,
        )

    def replace_step(self, updated: TestPlanStep) -> TestPlan:
        if updated.step_id not in _DEFINITIONS_BY_ID:
            raise ValueError("Unknown test-plan step.")
        if updated.block_id not in {step.block_id for step in self.steps}:
            raise ValueError("Unknown test-plan block.")
        return replace(
            self,
            updated_at_utc=utc_now_text(),
            steps=tuple(
                updated if step.block_id == updated.block_id else step for step in self.steps
            ),
        )

    def with_step_selected(self, block_id: str, selected: bool) -> TestPlan:
        """Include or exclude one pending step without changing its identity."""
        current = next(
            (step for step in self.steps if step.block_id == block_id),
            None,
        )
        if current is None:
            raise ValueError("Unknown test-plan step.")
        if current.selected == bool(selected):
            return self
        if current.status is not TestPlanStepStatus.PENDING:
            raise ValueError("A started test-plan step cannot be included or excluded.")
        return self.replace_step(replace(current, selected=bool(selected)))

    def with_rest_after_step_ids(self, step_ids: tuple[str, ...]) -> TestPlan:
        """Return the plan with explicitly selected non-session rest points."""
        return replace(
            self,
            updated_at_utc=utc_now_text(),
            rest_after_step_ids=step_ids,
        )

    def with_config_revisions(self, revisions: Mapping[str, int]) -> TestPlan:
        """Refresh pending-step snapshots from the shared task configuration."""
        return replace(
            self,
            updated_at_utc=utc_now_text(),
            steps=tuple(
                replace(
                    step,
                    config_revision=revisions.get(
                        step.module_id,
                        step.config_revision,
                    ),
                )
                if step.status is TestPlanStepStatus.PENDING
                else step
                for step in self.steps
            ),
        )

    def with_axis_order(self, axis_order: BinaryAxisOrder | str) -> TestPlan:
        selected = BinaryAxisOrder(axis_order)
        if selected is self.axis_order:
            return self
        binary_ids = {"binary_horizontal", "binary_vertical"}
        if any(
            step.step_id in binary_ids and step.status is not TestPlanStepStatus.PENDING
            for step in self.steps
        ):
            raise ValueError("Binary-axis order cannot change after either step starts.")
        reordered = list(self.steps)
        horizontal = next(
            (index for index, step in enumerate(reordered) if step.step_id == "binary_horizontal"),
            None,
        )
        vertical = next(
            (index for index, step in enumerate(reordered) if step.step_id == "binary_vertical"),
            None,
        )
        desired_horizontal_first = selected is BinaryAxisOrder.HORIZONTAL_FIRST
        if (
            horizontal is not None
            and vertical is not None
            and (horizontal < vertical) != desired_horizontal_first
        ):
            reordered[horizontal], reordered[vertical] = reordered[vertical], reordered[horizontal]
        old_defaults = default_rest_after_step_ids(self.axis_order)
        rest_ids = (
            default_rest_after_step_ids(selected)
            if self.rest_after_step_ids == old_defaults
            else self.rest_after_step_ids
        )
        return replace(
            self,
            updated_at_utc=utc_now_text(),
            axis_order=selected,
            steps=tuple(reordered),
            rest_after_step_ids=rest_ids,
        )

    def with_step_position(self, block_id: str, position: int) -> TestPlan:
        """Move one step before a plan starts, preserving all step state and settings."""
        if not 0 <= position < len(self.steps):
            raise ValueError("Test-plan position is outside the available range.")
        if any(step.status is not TestPlanStepStatus.PENDING for step in self.steps):
            raise ValueError("Test-plan order cannot change after the plan starts.")
        reordered = list(self.steps)
        current = next(
            (index for index, step in enumerate(reordered) if step.block_id == block_id),
            None,
        )
        if current is None:
            raise ValueError("Unknown test-plan step.")
        if current == position:
            return self
        moved = reordered.pop(current)
        reordered.insert(position, moved)
        horizontal = next(
            (index for index, step in enumerate(reordered) if step.step_id == "binary_horizontal"),
            None,
        )
        vertical = next(
            (index for index, step in enumerate(reordered) if step.step_id == "binary_vertical"),
            None,
        )
        axis_order = self.axis_order
        if horizontal is not None and vertical is not None:
            axis_order = (
                BinaryAxisOrder.HORIZONTAL_FIRST
                if horizontal < vertical
                else BinaryAxisOrder.VERTICAL_FIRST
            )
        return replace(
            self,
            updated_at_utc=utc_now_text(),
            steps=tuple(reordered),
            axis_order=axis_order,
        )

    def with_default_order(self) -> TestPlan:
        """Restore one fresh block for each default clinical task."""
        if any(step.status is not TestPlanStepStatus.PENDING for step in self.steps):
            raise ValueError("Test-plan order cannot change after the plan starts.")
        restored = TestPlan.default(
            self.patient_id,
            config_revisions={step.module_id: step.config_revision for step in self.steps},
        )
        return replace(
            restored,
            plan_id=self.plan_id,
            created_at_utc=self.created_at_utc,
            revision=self.revision,
        )

    def delete_block(self, block_id: str) -> TestPlan:
        """Delete one pending block while no task is running."""
        self._require_structure_editable()
        if len(self.steps) == 1:
            raise ValueError("本次测试至少保留一个任务块。")
        current = next(
            (step for step in self.steps if step.block_id == block_id),
            None,
        )
        if current is None:
            raise ValueError("Unknown test-plan block.")
        if current.status is not TestPlanStepStatus.PENDING:
            raise ValueError("只能删除尚未开始的任务块；已有结果的任务块需保留。")
        return replace(
            self,
            updated_at_utc=utc_now_text(),
            steps=tuple(step for step in self.steps if step.block_id != block_id),
            rest_after_step_ids=tuple(
                item for item in self.rest_after_step_ids if item != block_id
            ),
        )

    def copy_block(self, block_id: str) -> TestPlan:
        """Copy one block below itself as a fresh pending task."""
        self._require_structure_editable()
        copied = next(
            (step for step in self.steps if step.block_id == block_id),
            None,
        )
        if copied is None:
            raise ValueError("Unknown test-plan block.")
        duplicate = replace(
            copied,
            block_id=uuid4().hex,
            selected=(copied.selected if copied.status is TestPlanStepStatus.PENDING else True),
            status=TestPlanStepStatus.PENDING,
            session_id=None,
            retry_count=0,
            skip_reason=None,
        )
        steps = list(self.steps)
        steps.insert(steps.index(copied) + 1, duplicate)
        return replace(
            self,
            updated_at_utc=utc_now_text(),
            steps=tuple(steps),
        )

    def insert_block_after(
        self,
        block_id: str,
        step_id: str,
        *,
        config_revision: int,
    ) -> TestPlan:
        """Insert a chosen task type immediately below one selected block."""
        self._require_structure_editable()
        definition = _DEFINITIONS_BY_ID.get(step_id)
        if definition is None:
            raise ValueError("Unknown clinical task type.")
        current = next(
            (step for step in self.steps if step.block_id == block_id),
            None,
        )
        if current is None:
            raise ValueError("Unknown test-plan block.")
        inserted = TestPlanStep(
            block_id=uuid4().hex,
            step_id=definition.step_id,
            module_id=definition.module_id,
            game_mode=definition.game_mode,
            selected=True,
            config_revision=config_revision,
        )
        steps = list(self.steps)
        steps.insert(steps.index(current) + 1, inserted)
        return replace(
            self,
            updated_at_utc=utc_now_text(),
            steps=tuple(steps),
        )

    def _require_structure_editable(self) -> None:
        if any(step.status is TestPlanStepStatus.RUNNING for step in self.steps):
            raise ValueError("任务运行中不能删除、复制或插入任务块。")

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "patient_id": self.patient_id,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "revision": self.revision,
            "axis_order": self.axis_order.value,
            "rest_after_step_ids": list(self.rest_after_step_ids),
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, value: object) -> TestPlan:
        if not isinstance(value, dict):
            raise TypeError("A test plan must be an object.")
        raw_steps = value.get("steps")
        raw_rest = value.get("rest_after_step_ids")
        if not isinstance(raw_steps, list) or not isinstance(raw_rest, list):
            raise TypeError("Test plan steps and rest points must be lists.")
        return cls(
            plan_id=str(value["plan_id"]),
            patient_id=str(value["patient_id"]),
            created_at_utc=str(value["created_at_utc"]),
            updated_at_utc=str(value["updated_at_utc"]),
            revision=value["revision"],  # type: ignore[arg-type]
            axis_order=BinaryAxisOrder(str(value["axis_order"])),
            rest_after_step_ids=tuple(str(item) for item in raw_rest),
            steps=tuple(TestPlanStep.from_dict(item) for item in raw_steps),
        )


class TestPlanConflict(RuntimeError):
    """The persisted plan changed after the caller loaded it."""

    def __init__(self, current: TestPlan | None) -> None:
        super().__init__("Test plan revision conflict.")
        self.current = current


class TestPlanStore:
    """Keep one recoverable current plan per patient in a single atomic JSON file."""

    schema_version = "1.0"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    @classmethod
    def for_settings(cls, settings: Settings) -> TestPlanStore:
        return cls(settings.data_dir / "runtime" / "current_test_plans.json")

    def load(self, patient_id: str) -> TestPlan | None:
        normalized_patient_id = patient_id.strip()
        if not normalized_patient_id:
            raise ValueError("patient_id cannot be empty.")
        value = self._load_document()["plans"].get(normalized_patient_id)
        if value is None:
            return None
        plan = TestPlan.from_dict(value)
        if plan.patient_id != normalized_patient_id:
            raise ValueError("Stored test-plan patient does not match its key.")
        return plan

    def save(self, plan: TestPlan, *, expected_revision: int) -> TestPlan:
        document = self._load_document()
        stored = document["plans"].get(plan.patient_id)
        current = TestPlan.from_dict(stored) if stored is not None else None
        current_revision = current.revision if current is not None else 0
        if int(expected_revision) != current_revision:
            raise TestPlanConflict(current)
        updated = replace(
            plan,
            revision=current_revision + 1,
            updated_at_utc=utc_now_text(),
        )
        document["plans"][plan.patient_id] = updated.to_dict()
        self._write(document)
        return updated

    def close(self, patient_id: str, *, expected_revision: int) -> None:
        patient_id = patient_id.strip()
        if not patient_id:
            raise ValueError("patient_id cannot be empty.")
        document = self._load_document()
        stored = document["plans"].get(patient_id)
        current = TestPlan.from_dict(stored) if stored is not None else None
        if current is None or current.revision != int(expected_revision):
            raise TestPlanConflict(current)
        del document["plans"][patient_id]
        self._write(document)

    def _load_document(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": self.schema_version, "plans": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Current test-plan file is invalid: {self.path}") from error
        if not isinstance(payload, dict):
            raise ValueError("Current test-plan root must be an object.")
        if payload.get("schema_version") != self.schema_version:
            raise ValueError("Unsupported current test-plan schema.")
        plans = payload.get("plans")
        if not isinstance(plans, dict):
            raise ValueError("Current test-plan plans must be an object.")
        return {"schema_version": self.schema_version, "plans": dict(plans)}

    def _write(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)


_STATUS_LABELS = {
    TestPlanStepStatus.PENDING: "待进行",
    TestPlanStepStatus.RUNNING: "进行中",
    TestPlanStepStatus.COMPLETED: "已完成",
    TestPlanStepStatus.ABORTED: "已取消",
    TestPlanStepStatus.FAILED: "失败",
    TestPlanStepStatus.SKIPPED: "已跳过",
}
_STATUS_COLORS = {
    TestPlanStepStatus.PENDING: "#5a7184",
    TestPlanStepStatus.RUNNING: "#1565c0",
    TestPlanStepStatus.COMPLETED: "#176b36",
    TestPlanStepStatus.ABORTED: "#8a5a00",
    TestPlanStepStatus.FAILED: "#b42318",
    TestPlanStepStatus.SKIPPED: "#6b7280",
}
_SKIP_REASON_LABELS = {
    "patient_fatigue": "患者疲劳",
    "care_interruption": "护理中断",
    "device_unavailable": "设备条件不满足",
    "sensory_or_oculomotor_limit": "视听/眼肌限制",
    "clinically_not_applicable": "临床不适用",
    "other": "其他",
}


class TestPlanDialog(QDialog):
    """Edit one patient's current plan without copying task settings or results."""

    def __init__(
        self,
        settings: Settings,
        patient: Patient,
        plan: TestPlan,
        task_config_store: TaskConfigStore,
        *,
        gaze_status_text: str,
        recent_completion_by_module: Mapping[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if plan.patient_id != str(patient.patient_id):
            raise ValueError("Test plan belongs to a different patient.")

        self.settings = settings
        self.patient = patient
        self.plan = plan
        self.saved_plan: TestPlan | None = None
        self.task_config_store = task_config_store
        self.recent_completion_by_module = dict(recent_completion_by_module or {})
        self._selection_boxes: dict[str, QCheckBox] = {}
        self._rest_boxes: dict[str, QCheckBox] = {}
        self._row_widgets: dict[str, QWidget] = {}
        self._block_buttons: dict[str, QRadioButton] = {}
        self._block_button_group = QButtonGroup(self)
        self._block_button_group.setExclusive(True)
        self._selected_block_id = plan.steps[0].block_id
        self._rows_layout: QVBoxLayout | None = None
        self._rebuilding = False

        self.setObjectName("testPlanDialog")
        self.setWindowTitle("编排本次测试")
        self.resize(980, 760)

        title = QLabel("编排本次测试")
        title.setStyleSheet("font-size:24px;font-weight:800;color:#17324d;")
        patient_label = QLabel(
            f"当前患者：{patient.display_label}　"
            f"计划日期：{plan.created_at_utc[:10]}　"
            f"{gaze_status_text}"
        )
        patient_label.setWordWrap(True)
        patient_label.setStyleSheet("color:#5a7184;")

        self.axis_combo = QComboBox()
        self.axis_combo.setObjectName("testPlanAxisOrderCombo")
        self.axis_combo.addItem(
            "左右二分优先（默认）",
            BinaryAxisOrder.HORIZONTAL_FIRST.value,
        )
        self.axis_combo.addItem(
            "上下二分优先",
            BinaryAxisOrder.VERTICAL_FIRST.value,
        )
        self.axis_combo.setCurrentIndex(self.axis_combo.findData(plan.axis_order.value))
        self.axis_combo.currentIndexChanged.connect(self._change_axis_order)

        axis_row = QHBoxLayout()
        axis_row.addWidget(QLabel("方向顺序"))
        axis_row.addWidget(self.axis_combo)
        axis_row.addStretch(1)

        self.delete_block_button = QPushButton("删除所选块")
        self.delete_block_button.setObjectName("testPlanDeleteBlockButton")
        self.delete_block_button.clicked.connect(self._delete_selected_block)
        self.copy_block_button = QPushButton("复制所选块")
        self.copy_block_button.setObjectName("testPlanCopyBlockButton")
        self.copy_block_button.clicked.connect(self._copy_selected_block)
        self.insert_block_button = QPushButton("在下方插入任务块")
        self.insert_block_button.setObjectName("testPlanInsertBlockButton")
        self.insert_block_button.clicked.connect(self._insert_block_below)

        block_actions = QHBoxLayout()
        block_actions.addWidget(QLabel("先选定任务块，再操作："))
        block_actions.addWidget(self.delete_block_button)
        block_actions.addWidget(self.copy_block_button)
        block_actions.addWidget(self.insert_block_button)
        block_actions.addStretch(1)

        self.rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self.rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.rows_widget)

        return_button = QPushButton("返回患者工作台")
        return_button.setObjectName("testPlanReturnButton")
        return_button.clicked.connect(self.reject)
        restore_button = QPushButton("恢复默认编排")
        restore_button.setObjectName("testPlanRestoreOrderButton")
        restore_button.clicked.connect(self._restore_default_order)
        save_button = QPushButton("保存编排")
        save_button.setObjectName("testPlanSaveButton")
        save_button.setStyleSheet("background:#1565c0;color:white;font-weight:700;")
        save_button.clicked.connect(self._save)

        actions = QHBoxLayout()
        actions.addWidget(return_button)
        actions.addStretch(1)
        actions.addWidget(restore_button)
        actions.addWidget(save_button)

        root = QVBoxLayout(self)
        root.addWidget(title)
        root.addWidget(patient_label)
        root.addLayout(axis_row)
        root.addLayout(block_actions)
        root.addWidget(scroll, 1)
        root.addLayout(actions)

        self._rebuild_rows()

    def _clear_rows(self) -> None:
        if self._rows_layout is None:
            return
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild_rows(self) -> None:
        if self._rows_layout is None:
            return
        self._rebuilding = True
        for button in self._block_button_group.buttons():
            self._block_button_group.removeButton(button)
        self._clear_rows()
        self._selection_boxes.clear()
        self._rest_boxes.clear()
        self._row_widgets.clear()
        self._block_buttons.clear()

        block_ids = {step.block_id for step in self.plan.steps}
        if self._selected_block_id not in block_ids:
            self._selected_block_id = self.plan.steps[0].block_id
        for index, step in enumerate(self.plan.steps):
            definition = _DEFINITIONS_BY_ID[step.step_id]
            row = QWidget()
            row.setObjectName(f"testPlanStep_{step.block_id}")
            row.setProperty("selectedBlock", step.block_id == self._selected_block_id)
            row.setStyleSheet(
                "QWidget { background:white;border:1px solid #d9e3ec;"
                "border-radius:9px; } QLabel, QCheckBox { border:none; }"
                'QWidget[selectedBlock="true"] { background:#eaf4ff;'
                "border:3px solid #1565c0; }"
                "QPushButton { background:#f8fbfe;color:#17324d;"
                "border:1px solid #bfd3e4;border-radius:7px;padding:4px 8px; }"
                "QPushButton:hover { background:#e7f2fb;border-color:#76a9cf; }"
                "QPushButton:pressed { background:#d4e8f7; }"
                "QCheckBox::indicator { width:20px;height:20px;background:white;"
                "border:2px solid #8194a5;border-radius:4px; }"
                "QCheckBox::indicator:checked { background:#1565c0;"
                "border:2px solid #0d4f9c; }"
                "QCheckBox::indicator:disabled { background:#e8edf2;"
                "border-color:#b8c4ce; }"
                "QRadioButton { background:transparent;border:none;"
                "color:#17324d;font-weight:700; }"
                "QRadioButton::indicator { width:20px;height:20px;background:white;"
                "border:2px solid #8194a5;border-radius:11px; }"
                "QRadioButton::indicator:checked { background:#1565c0;"
                "border:3px solid #9fcaf0; }"
            )
            self._row_widgets[step.block_id] = row
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(12, 9, 12, 9)
            row_layout.setSpacing(5)
            layout = QHBoxLayout()

            block_choice = QRadioButton(
                "已选定" if step.block_id == self._selected_block_id else "选定"
            )
            block_choice.setObjectName(f"testPlanBlockChoice_{step.block_id}")
            block_choice.setMinimumWidth(78)
            block_choice.setChecked(step.block_id == self._selected_block_id)
            block_choice.toggled.connect(partial(self._select_block, step.block_id))
            self._block_button_group.addButton(block_choice)
            self._block_buttons[step.block_id] = block_choice

            selected = QCheckBox()
            selected.setObjectName(f"testPlanSelected_{step.block_id}")
            selected.setChecked(step.selected)
            selected.setText("✓ 本次执行" if step.selected else "本次不做")
            selected.setEnabled(step.status is TestPlanStepStatus.PENDING)
            selected.setToolTip("勾选表示本次执行；取消勾选表示本次不做。")
            selected.toggled.connect(partial(self._toggle_selected, step.block_id))
            self._selection_boxes[step.block_id] = selected

            optional = "（可选，默认不勾选）" if definition.step_id == "eye_observation" else ""
            title = QLabel(
                f"第 {index + 1} 块 · {definition.clinical_number}　"
                f"{definition.title}{optional}\n"
                f"预计 {definition.estimated_minutes} 分钟"
            )
            title.setMinimumWidth(235)
            title.setStyleSheet("font-weight:700;color:#17324d;")

            recent = self.recent_completion_by_module.get(
                definition.module_id,
                "暂无完成记录",
            )
            config_text = (
                "无独立任务设置"
                if definition.module_id == "eye_observation"
                else f"设置版本 {step.config_revision}"
            )
            summary = QLabel(f"{config_text}\n最近完成：{recent}")
            summary.setObjectName(f"testPlanSummary_{step.block_id}")
            summary.setMinimumWidth(190)
            summary.setStyleSheet("color:#5a7184;")

            status = QLabel(_STATUS_LABELS[step.status])
            status.setObjectName(f"testPlanStatus_{step.block_id}")
            status.setStyleSheet(f"color:{_STATUS_COLORS[step.status]};font-weight:700;")

            rest = QCheckBox("此项后休息")
            rest.setObjectName(f"testPlanRest_{step.block_id}")
            rest.setChecked(step.block_id in self.plan.rest_after_step_ids)
            rest.setEnabled(step.selected)
            rest.toggled.connect(partial(self._toggle_rest, step.block_id))
            self._rest_boxes[step.block_id] = rest

            settings_button = QPushButton("调整设置")
            settings_button.setObjectName(f"testPlanSettings_{step.block_id}")
            settings_button.setEnabled(
                definition.module_id != "eye_observation"
                and step.status is not TestPlanStepStatus.RUNNING
            )
            settings_button.clicked.connect(partial(self._edit_task_settings, definition))

            state_button = QPushButton()
            state_button.setObjectName(f"testPlanStateAction_{step.block_id}")
            if step.status is TestPlanStepStatus.SKIPPED:
                state_button.setText("撤销跳过")
                state_button.clicked.connect(partial(self._undo_skip, step.block_id))
            elif step.status in {
                TestPlanStepStatus.ABORTED,
                TestPlanStepStatus.FAILED,
            }:
                state_button.setText("准备重试")
                state_button.clicked.connect(partial(self._prepare_retry, step.block_id))
            else:
                state_button.setText("记录原因后跳过")
                state_button.setEnabled(step.selected and step.status is TestPlanStepStatus.PENDING)
                state_button.clicked.connect(partial(self._skip_step, step.block_id))

            layout.addWidget(block_choice)
            layout.addWidget(selected)
            layout.addWidget(title)
            layout.addWidget(summary, 1)
            layout.addWidget(status)
            layout.addWidget(rest)
            layout.addWidget(settings_button)
            layout.addWidget(state_button)
            row_layout.addLayout(layout)
            self._rows_layout.addWidget(row)

        self._rows_layout.addStretch(1)
        self._rebuilding = False
        self._refresh_block_action_state()

    def _toggle_selected(self, block_id: str, selected: bool) -> None:
        if self._rebuilding:
            return
        try:
            self.plan = self.plan.with_step_selected(block_id, selected)
        except ValueError as error:
            QMessageBox.warning(self, "无法修改本步骤", str(error))
            self._rebuild_rows()
            return
        self._selection_boxes[block_id].setText("✓ 本次执行" if selected else "本次不做")
        self._rest_boxes[block_id].setEnabled(selected)
        if not selected:
            self._rest_boxes[block_id].setChecked(False)

    def _change_axis_order(self) -> None:
        if self._rebuilding:
            return
        try:
            self.plan = self.plan.with_axis_order(
                BinaryAxisOrder(str(self.axis_combo.currentData()))
            )
        except ValueError as error:
            QMessageBox.warning(self, "无法调整方向顺序", str(error))
            self._rebuilding = True
            self.axis_combo.setCurrentIndex(self.axis_combo.findData(self.plan.axis_order.value))
            self._rebuilding = False
            return
        self._rebuild_rows()

    def _select_block(self, block_id: str, checked: bool) -> None:
        if self._rebuilding or not checked:
            return
        self._selected_block_id = block_id
        for current_id, row in self._row_widgets.items():
            row.setProperty("selectedBlock", current_id == block_id)
            row.style().unpolish(row)
            row.style().polish(row)
        for current_id, button in self._block_buttons.items():
            button.setText("已选定" if current_id == block_id else "选定")
        self._refresh_block_action_state()

    def _refresh_block_action_state(self) -> None:
        current = next(
            (step for step in self.plan.steps if step.block_id == self._selected_block_id),
            None,
        )
        running = any(step.status is TestPlanStepStatus.RUNNING for step in self.plan.steps)
        can_add = current is not None and not running
        can_delete = (
            can_add
            and current is not None
            and current.status is TestPlanStepStatus.PENDING
            and len(self.plan.steps) > 1
        )
        self.delete_block_button.setEnabled(can_delete)
        self.copy_block_button.setEnabled(can_add)
        self.insert_block_button.setEnabled(can_add)

        if running:
            reason = "任务运行中，结束或取消当前任务后再调整编排。"
            self.delete_block_button.setToolTip(reason)
            self.copy_block_button.setToolTip(reason)
            self.insert_block_button.setToolTip(reason)
        else:
            self.copy_block_button.setToolTip("复制为一个新的待进行任务块。")
            self.insert_block_button.setToolTip("在当前选定块下方加入新的待进行任务块。")
            self.delete_block_button.setToolTip(
                "删除当前尚未开始的任务块。"
                if can_delete
                else "已有结果的任务块不可删除；可复制为新的待进行任务块。"
            )

    def _toggle_rest(self, block_id: str, checked: bool) -> None:
        if self._rebuilding:
            return
        rest_ids = list(self.plan.rest_after_step_ids)
        if checked and block_id not in rest_ids:
            rest_ids.append(block_id)
        elif not checked and block_id in rest_ids:
            rest_ids.remove(block_id)
        self.plan = self.plan.with_rest_after_step_ids(tuple(rest_ids))

    def _delete_selected_block(self, checked: bool = False) -> None:
        del checked
        try:
            original_index = next(
                index
                for index, step in enumerate(self.plan.steps)
                if step.block_id == self._selected_block_id
            )
            self.plan = self.plan.delete_block(self._selected_block_id)
        except (StopIteration, ValueError) as error:
            QMessageBox.warning(self, "无法删除任务块", str(error))
            return
        self._selected_block_id = self.plan.steps[
            min(original_index, len(self.plan.steps) - 1)
        ].block_id
        self._rebuild_rows()

    def _copy_selected_block(self, checked: bool = False) -> None:
        del checked
        try:
            original_index = next(
                index
                for index, step in enumerate(self.plan.steps)
                if step.block_id == self._selected_block_id
            )
            self.plan = self.plan.copy_block(self._selected_block_id)
        except (StopIteration, ValueError) as error:
            QMessageBox.warning(self, "无法复制任务块", str(error))
            return
        self._selected_block_id = self.plan.steps[original_index + 1].block_id
        self._rebuild_rows()

    def _insert_block_below(self, checked: bool = False) -> None:
        del checked
        choices = [
            f"{definition.clinical_number} · {definition.title}"
            for definition in CLINICAL_TASK_ORDER
        ]
        choice, accepted = QInputDialog.getItem(
            self,
            "在下方插入任务块",
            "选择要插入的任务功能：",
            choices,
            editable=False,
        )
        if not accepted:
            return
        definition = CLINICAL_TASK_ORDER[choices.index(choice)]
        revision = (
            0
            if definition.module_id == "eye_observation"
            else self.task_config_store.load(
                definition.module_id,
                patient_id=str(self.patient.patient_id),
            ).revision
        )
        try:
            original_index = next(
                index
                for index, step in enumerate(self.plan.steps)
                if step.block_id == self._selected_block_id
            )
            self.plan = self.plan.insert_block_after(
                self._selected_block_id,
                definition.step_id,
                config_revision=revision,
            )
        except (StopIteration, ValueError) as error:
            QMessageBox.warning(self, "无法插入任务块", str(error))
            return
        self._selected_block_id = self.plan.steps[original_index + 1].block_id
        self._rebuild_rows()

    def _restore_default_order(self, checked: bool = False) -> None:
        del checked
        try:
            self.plan = self.plan.with_default_order()
        except ValueError as error:
            QMessageBox.warning(self, "无法恢复默认顺序", str(error))
            return
        self._rebuilding = True
        self.axis_combo.setCurrentIndex(
            self.axis_combo.findData(BinaryAxisOrder.HORIZONTAL_FIRST.value)
        )
        self._selected_block_id = self.plan.steps[0].block_id
        self._rebuilding = False
        self._rebuild_rows()

    def _skip_step(self, block_id: str, checked: bool = False) -> None:
        del checked
        choices = list(_SKIP_REASON_LABELS.values())
        choice, accepted = QInputDialog.getItem(
            self,
            "记录跳过原因",
            "跳过不等于失败或阴性结果。请选择原因：",
            choices,
            editable=False,
        )
        if not accepted:
            return
        reason = next(key for key, label in _SKIP_REASON_LABELS.items() if label == choice)
        if reason == "other":
            detail, accepted = QInputDialog.getText(
                self,
                "其他原因",
                "请输入 1–200 字原因：",
            )
            if not accepted:
                return
            reason = detail.strip()
        step = next(item for item in self.plan.steps if item.block_id == block_id)
        try:
            self.plan = self.plan.replace_step(step.skip(reason))
        except ValueError as error:
            QMessageBox.warning(self, "无法跳过本步骤", str(error))
            return
        self._rebuild_rows()

    def _undo_skip(self, block_id: str, checked: bool = False) -> None:
        del checked
        step = next(item for item in self.plan.steps if item.block_id == block_id)
        self.plan = self.plan.replace_step(step.undo_skip())
        self._rebuild_rows()

    def _prepare_retry(self, block_id: str, checked: bool = False) -> None:
        del checked
        step = next(item for item in self.plan.steps if item.block_id == block_id)
        self.plan = self.plan.replace_step(step.prepare_retry())
        self._rebuild_rows()

    def _edit_task_settings(
        self,
        definition: ClinicalTaskDefinition,
        checked: bool = False,
    ) -> None:
        del checked
        module_id = definition.module_id
        patient_id = str(self.patient.patient_id)
        record = self.task_config_store.load(module_id, patient_id=patient_id)
        config = task_config_from_dict(module_id, record.config)
        setup: QDialog

        if isinstance(config, TrackingBallConfig):
            setup = TrackingBallSetupDialog(
                self,
                config=config,
                image_library_path=self.settings.data_dir / "image_library",
            )
        elif isinstance(config, BinaryQuestionConfig):
            setup = BinaryQuestionSetupDialog(
                self,
                config=config,
                question_bank_path=self.settings.data_dir / "common_questions.json",
                layout=("vertical" if module_id == "binary_vertical" else "horizontal"),
            )
        elif isinstance(config, ScreenKeyboardConfig):
            setup = ScreenKeyboardSetupDialog(self, config=config)
        elif isinstance(config, MultipleChoiceConfig):
            setup = MultipleChoiceSetupDialog(self, config=config)
        elif isinstance(config, ImageChoiceConfig):
            setup = ImageChoiceSetupDialog(
                self,
                config=config,
                image_library_path=self.settings.data_dir / "image_library",
            )
        elif isinstance(config, InstructionFixationConfig):
            setup = InstructionFixationSetupDialog(self, config=config)
        elif isinstance(config, GazeGameConfig):
            setup = GazeGameSetupDialog(
                self,
                config=config,
                image_library_path=self.settings.data_dir / "image_library",
            )
        elif isinstance(config, VisualPreferenceConfig):
            setup = VisualPreferenceSetupDialog(
                self,
                config=config,
                image_library_path=self.settings.data_dir / "image_library",
            )
        else:
            raise TypeError(f"Unsupported task configuration: {module_id}")

        if setup.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            saved = self.task_config_store.save(
                module_id,
                task_config_to_dict(setup.build_config()),  # type: ignore[attr-defined]
                expected_revision=record.revision,
                patient_id=patient_id,
            )
        except TaskConfigConflict:
            QMessageBox.warning(
                self,
                "任务设置已更新",
                "其他管理端已修改本任务设置。请重新打开后确认。",
            )
            return
        self.plan = self.plan.with_config_revisions({module_id: saved.revision})
        self._rebuild_rows()

    def _save(self, checked: bool = False) -> None:
        del checked
        rest_ids = tuple(
            step.block_id
            for step in self.plan.steps
            if self._rest_boxes[step.block_id].isChecked()
            and self._selection_boxes[step.block_id].isChecked()
        )
        if len(rest_ids) > 3:
            QMessageBox.warning(
                self,
                "休息点过多",
                "默认两个休息点之外，本次最多再增加一个自定义休息点。",
            )
            return

        revisions: dict[str, int] = {}
        for step in self.plan.steps:
            if step.module_id == "eye_observation":
                continue
            revisions[step.module_id] = self.task_config_store.load(
                step.module_id,
                patient_id=str(self.patient.patient_id),
            ).revision
        self.plan = self.plan.with_config_revisions(revisions)
        self.plan = self.plan.with_rest_after_step_ids(rest_ids)
        self.saved_plan = self.plan
        self.accept()
