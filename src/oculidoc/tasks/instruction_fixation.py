"""Follow-an-instruction fixation trials with target and catch conditions."""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, replace
from enum import StrEnum
from math import cos, pi, sin
from time import monotonic_ns

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.devices.contracts import EyeTrackerSample
from oculidoc.tasks.tracking_ball import TargetShape


class FixationCondition(StrEnum):
    TARGET_ONLY = "target_only"
    DISTRACTOR = "target_with_distractors"
    NO_TARGET = "no_target"


POSITION_CENTERS: dict[str, tuple[float, float]] = {
    "top_left": (0.18, 0.30),
    "top_center": (0.50, 0.30),
    "top_right": (0.82, 0.30),
    "middle_left": (0.18, 0.55),
    "center": (0.50, 0.55),
    "middle_right": (0.82, 0.55),
    "bottom_left": (0.18, 0.80),
    "bottom_center": (0.50, 0.80),
    "bottom_right": (0.82, 0.80),
}

POSITION_LABELS: dict[str, str] = {
    "top_left": "左上",
    "top_center": "上中",
    "top_right": "右上",
    "middle_left": "左中",
    "center": "中央",
    "middle_right": "右中",
    "bottom_left": "左下",
    "bottom_center": "下中",
    "bottom_right": "右下",
}


@dataclass(frozen=True, slots=True)
class InstructionFixationConfig:
    target_description: str = "黄色圆形"
    target_shape: TargetShape = TargetShape.CIRCLE
    target_color: str = "#ffcc00"
    distractor_shape: TargetShape = TargetShape.SQUARE
    distractor_color: str = "#4f8edc"
    background_color: str = "#071521"
    position_ids: tuple[str, ...] = (
        "top_left",
        "top_right",
        "center",
        "bottom_left",
        "bottom_right",
    )
    target_only_trial_count: int = 2
    distractor_trial_count: int = 4
    no_target_trial_count: int = 2
    distractor_count: int = 2
    target_size_px: int = 240
    dwell_time_ms: int = 1_200
    trial_duration_seconds: int = 15
    instruction_font_size_pt: int = 48
    randomize_trial_order: bool = True
    randomization_seed: int | None = None
    show_gaze_cursor: bool = False

    def __post_init__(self) -> None:
        description = self.target_description.strip()
        positions = tuple(str(value).strip() for value in self.position_ids)
        object.__setattr__(self, "target_description", description)
        object.__setattr__(self, "target_shape", TargetShape(self.target_shape))
        object.__setattr__(self, "distractor_shape", TargetShape(self.distractor_shape))
        object.__setattr__(self, "position_ids", positions)

        if not 1 <= len(description) <= 80:
            raise ValueError("target_description must contain 1 to 80 characters.")

        if not positions or any(value not in POSITION_CENTERS for value in positions):
            raise ValueError("position_ids must contain supported screen positions.")

        if len(set(positions)) != len(positions):
            raise ValueError("position_ids cannot contain duplicates.")

        counts = (
            self.target_only_trial_count,
            self.distractor_trial_count,
            self.no_target_trial_count,
        )

        if any(value < 0 or value > 100 for value in counts):
            raise ValueError("Each trial count must be between 0 and 100.")

        total_trials = sum(counts)

        if not 1 <= total_trials <= 100:
            raise ValueError("The protocol must contain between 1 and 100 trials.")

        if not 1 <= self.distractor_count <= 6:
            raise ValueError("distractor_count must be between 1 and 6.")

        required_positions = self.distractor_count + (1 if self.distractor_trial_count > 0 else 0)

        if (self.distractor_trial_count > 0 or self.no_target_trial_count > 0) and len(
            positions
        ) < required_positions:
            raise ValueError("Selected positions are insufficient for the configured distractors.")

        if not 40 <= self.target_size_px <= 600:
            raise ValueError("target_size_px must be between 40 and 600.")

        if not 250 <= self.dwell_time_ms <= 10_000:
            raise ValueError("dwell_time_ms must be between 250 and 10000.")

        if not 3 <= self.trial_duration_seconds <= 120:
            raise ValueError("trial_duration_seconds must be between 3 and 120.")

        if total_trials * self.trial_duration_seconds > 3_500:
            raise ValueError("The configured protocol is too long for one task run.")

        if not 20 <= self.instruction_font_size_pt <= 120:
            raise ValueError("instruction_font_size_pt must be between 20 and 120.")

        for name, value in (
            ("target_color", self.target_color),
            ("distractor_color", self.distractor_color),
            ("background_color", self.background_color),
        ):
            if not QColor(value).isValid():
                raise ValueError(f"{name} must be a valid color.")

        if self.target_shape is self.distractor_shape and QColor(self.target_color) == QColor(
            self.distractor_color
        ):
            raise ValueError("Target and distractors must differ in shape or color.")

        if not isinstance(self.randomize_trial_order, bool):
            raise TypeError("randomize_trial_order must be a boolean.")

        if not isinstance(self.show_gaze_cursor, bool):
            raise TypeError("show_gaze_cursor must be a boolean.")

        if self.randomization_seed is not None and (
            not isinstance(self.randomization_seed, int)
            or isinstance(self.randomization_seed, bool)
            or self.randomization_seed < 0
        ):
            raise TypeError("randomization_seed must be a non-negative integer or null.")

    @property
    def trial_count(self) -> int:
        return (
            self.target_only_trial_count + self.distractor_trial_count + self.no_target_trial_count
        )


@dataclass(frozen=True, slots=True)
class InstructionFixationTrial:
    trial_id: str
    trial_number: int
    trial_count: int
    condition: FixationCondition
    prompt: str
    target_position: str | None
    distractor_positions: tuple[str, ...]

    @property
    def target_present(self) -> bool:
        return self.target_position is not None


def instruction_fixation_protocol(
    config: InstructionFixationConfig,
) -> tuple[InstructionFixationTrial, ...]:
    """Build a balanced, reproducible sequence of target and catch trials."""
    seed = (
        config.randomization_seed if config.randomization_seed is not None else secrets.randbits(63)
    )
    rng = random.Random(seed)
    conditions = (
        [FixationCondition.TARGET_ONLY] * config.target_only_trial_count
        + [FixationCondition.DISTRACTOR] * config.distractor_trial_count
        + [FixationCondition.NO_TARGET] * config.no_target_trial_count
    )

    if config.randomize_trial_order:
        rng.shuffle(conditions)

    target_positions: list[str] = []

    def next_target_position() -> str:
        nonlocal target_positions

        if not target_positions:
            target_positions = list(config.position_ids)
            rng.shuffle(target_positions)

        return target_positions.pop()

    trials: list[InstructionFixationTrial] = []

    for index, condition in enumerate(conditions):
        target_position = (
            None if condition is FixationCondition.NO_TARGET else next_target_position()
        )
        available = [position for position in config.position_ids if position != target_position]
        distractor_positions = (
            tuple(rng.sample(available, config.distractor_count))
            if condition is not FixationCondition.TARGET_ONLY
            else ()
        )
        trials.append(
            InstructionFixationTrial(
                trial_id=f"fixation-{index + 1:03d}-{condition.value}",
                trial_number=index + 1,
                trial_count=len(conditions),
                condition=condition,
                prompt=f"请注视{config.target_description}",
                target_position=target_position,
                distractor_positions=distractor_positions,
            )
        )

    return tuple(trials)


def _shape_path(shape: TargetShape, rectangle: QRectF) -> QPainterPath:
    path = QPainterPath()

    if shape is TargetShape.CIRCLE:
        path.addEllipse(rectangle)
        return path

    if shape is TargetShape.SQUARE:
        path.addRoundedRect(rectangle, rectangle.width() * 0.08, rectangle.height() * 0.08)
        return path

    center = rectangle.center()
    radius = min(rectangle.width(), rectangle.height()) / 2.0

    if shape is TargetShape.DIAMOND:
        points = [
            QPointF(center.x(), center.y() - radius),
            QPointF(center.x() + radius, center.y()),
            QPointF(center.x(), center.y() + radius),
            QPointF(center.x() - radius, center.y()),
        ]
    else:
        points = []

        for index in range(10):
            angle = -pi / 2.0 + index * pi / 5.0
            point_radius = radius if index % 2 == 0 else radius * 0.44
            points.append(
                QPointF(
                    center.x() + point_radius * cos(angle),
                    center.y() + point_radius * sin(angle),
                )
            )

    path.addPolygon(QPolygonF(points))
    path.closeSubpath()
    return path


class InstructionFixationTask(QWidget):
    """Present static AOIs and record command-following gaze evidence."""

    instruction_changed = Signal(str)
    protocol_completed = Signal()

    feedback_delay_ms = 700

    def __init__(
        self,
        config: InstructionFixationConfig,
        *,
        allow_mouse_fallback: bool = True,
    ) -> None:
        super().__init__()
        self.config = config
        self.allow_mouse_fallback = allow_mouse_fallback
        self.protocol_seed = (
            config.randomization_seed
            if config.randomization_seed is not None
            else secrets.randbits(63)
        )
        self.trials = instruction_fixation_protocol(
            replace(config, randomization_seed=self.protocol_seed)
        )
        self.setMouseTracking(allow_mouse_fallback)
        self.setMinimumSize(800, 560)

        if not allow_mouse_fallback:
            self.setCursor(Qt.CursorShape.BlankCursor)

        self._trial_timer = QTimer(self)
        self._trial_timer.setInterval(50)
        self._trial_timer.timeout.connect(self._check_trial_deadline)
        self._advance_timer = QTimer(self)
        self._advance_timer.setSingleShot(True)
        self._advance_timer.timeout.connect(self._advance_trial)
        self._reset_run_state()

    def _reset_run_state(self) -> None:
        self._started = False
        self._running = False
        self._protocol_finished = False
        self._trial_index = 0
        self._trial_started_ns: int | None = None
        self._trial_deadline_ns: int | None = None
        self._trial_finished = False
        self._trial_feedback: str | None = None
        self._last_gaze_normalized: tuple[float, float] | None = None
        self._recording_events: list[dict[str, object]] = []
        self._completed_trials: list[dict[str, object]] = []
        self._run_sample_count = 0
        self._run_valid_sample_count = 0
        self._reset_trial_measurements()

    def _reset_trial_measurements(self) -> None:
        self._trial_sample_count = 0
        self._trial_valid_sample_count = 0
        self._trial_invalid_sample_count = 0
        self._target_sample_count = 0
        self._distractor_sample_count = 0
        self._target_duration_ms = 0.0
        self._distractor_duration_ms = 0.0
        self._target_run_ms = 0.0
        self._distractor_run_ms = 0.0
        self._longest_target_run_ms = 0.0
        self._first_target_entry_ms: float | None = None
        self._first_target_acquired_ms: float | None = None
        self._target_acquired = False
        self._distractor_fixation_count = 0
        self._distractor_visit_acquired = False
        self._previous_region: tuple[str, str] | None = None
        self._previous_valid = False
        self._last_sample_timestamp_ns: int | None = None

    @property
    def current_trial(self) -> InstructionFixationTrial:
        return self.trials[min(self._trial_index, len(self.trials) - 1)]

    @property
    def patient_display_text(self) -> str:
        trial = self.current_trial
        return f"第 {trial.trial_number}/{trial.trial_count} 次\n{trial.prompt}"

    def _queue_event(
        self,
        event_type: str,
        *,
        timestamp_ns: int,
        payload: dict[str, object] | None = None,
    ) -> None:
        self._recording_events.append(
            {
                "event_type": event_type,
                "monotonic_timestamp_ns": int(timestamp_ns),
                "payload": dict(payload or {}),
            }
        )

    def _trial_payload(self, trial: InstructionFixationTrial | None = None) -> dict[str, object]:
        active = trial or self.current_trial
        return {
            "trial_id": active.trial_id,
            "trial_number": active.trial_number,
            "trial_count": active.trial_count,
            "condition": active.condition.value,
            "prompt": active.prompt,
            "target_present": active.target_present,
            "target_position": active.target_position,
            "distractor_positions": list(active.distractor_positions),
            "configured_dwell_ms": self.config.dwell_time_ms,
            "trial_duration_seconds": self.config.trial_duration_seconds,
            "randomization_seed": self.protocol_seed,
        }

    def _begin_trial(self, timestamp_ns: int) -> None:
        self._reset_trial_measurements()
        self._trial_started_ns = int(timestamp_ns)
        self._trial_deadline_ns = (
            int(timestamp_ns) + self.config.trial_duration_seconds * 1_000_000_000
        )
        self._trial_finished = False
        self._trial_feedback = None
        self._queue_event(
            "stimulus_presented",
            timestamp_ns=timestamp_ns,
            payload=self._trial_payload(),
        )
        self.instruction_changed.emit(self.current_trial.prompt)
        self.update()

    def start(self) -> None:
        self._reset_run_state()
        self._started = True
        self._running = True
        timestamp_ns = monotonic_ns()
        self._begin_trial(timestamp_ns)
        self._trial_timer.start()

    def stop(self) -> None:
        self._trial_timer.stop()
        self._advance_timer.stop()
        self._running = False

    def _check_trial_deadline(self) -> None:
        if (
            not self._running
            or self._trial_finished
            or self._protocol_finished
            or self._trial_deadline_ns is None
        ):
            return

        timestamp_ns = monotonic_ns()

        if timestamp_ns >= self._trial_deadline_ns:
            self._complete_trial("trial_timeout", timestamp_ns=timestamp_ns)

    def _advance_trial(self) -> None:
        if self._protocol_finished or not self._trial_finished:
            return

        if self._trial_index + 1 >= len(self.trials):
            self._protocol_finished = True
            self._trial_timer.stop()
            self.protocol_completed.emit()
            self.update()
            return

        self._trial_index += 1
        self._begin_trial(monotonic_ns())

    def advance_after_feedback(self) -> None:
        """Advance a finished trial immediately; useful for deterministic integration tests."""
        self._advance_timer.stop()
        self._advance_trial()

    def expire_current_trial(self, *, timestamp_ns: int | None = None) -> None:
        """Finish the current trial at its time limit without inferring a clinical response."""
        self._complete_trial(
            "trial_timeout",
            timestamp_ns=monotonic_ns() if timestamp_ns is None else timestamp_ns,
        )

    def _advance_previous_interval(self, timestamp_ns: int) -> bool:
        previous_timestamp = self._last_sample_timestamp_ns

        if previous_timestamp is None or timestamp_ns <= previous_timestamp:
            return False

        delta_ms = min(250.0, (timestamp_ns - previous_timestamp) / 1_000_000.0)

        if not self._previous_valid or self._previous_region is None:
            return False

        role, position = self._previous_region

        if role == "target":
            self._target_duration_ms += delta_ms
            self._target_run_ms += delta_ms
            self._longest_target_run_ms = max(
                self._longest_target_run_ms,
                self._target_run_ms,
            )

            if not self._target_acquired and self._target_run_ms >= self.config.dwell_time_ms:
                self._target_acquired = True
                started_ns = self._trial_started_ns or timestamp_ns
                self._first_target_acquired_ms = max(
                    0.0,
                    (timestamp_ns - started_ns) / 1_000_000.0,
                )
                self._queue_event(
                    "selection_committed",
                    timestamp_ns=timestamp_ns,
                    payload={
                        **self._trial_payload(),
                        "aoi_id": f"target:{position}",
                        "continuous_fixation_ms": self._target_run_ms,
                    },
                )
                self._complete_trial("target_fixation_acquired", timestamp_ns=timestamp_ns)
                return True

        elif role == "distractor":
            self._distractor_duration_ms += delta_ms
            self._distractor_run_ms += delta_ms

            if (
                not self._distractor_visit_acquired
                and self._distractor_run_ms >= self.config.dwell_time_ms
            ):
                self._distractor_visit_acquired = True
                self._distractor_fixation_count += 1
                self._queue_event(
                    "distractor_fixation_acquired",
                    timestamp_ns=timestamp_ns,
                    payload={
                        **self._trial_payload(),
                        "aoi_id": f"distractor:{position}",
                        "continuous_fixation_ms": self._distractor_run_ms,
                    },
                )

        return False

    def _leave_previous_region(self, timestamp_ns: int, *, reason: str) -> None:
        if self._previous_region is None:
            return

        role, position = self._previous_region
        self._queue_event(
            "aoi_exited",
            timestamp_ns=timestamp_ns,
            payload={
                **self._trial_payload(),
                "aoi_id": f"{role}:{position}",
                "role": role,
                "reason": reason,
            },
        )

        if role == "target":
            self._longest_target_run_ms = max(
                self._longest_target_run_ms,
                self._target_run_ms,
            )
            self._target_run_ms = 0.0
        else:
            self._distractor_run_ms = 0.0
            self._distractor_visit_acquired = False

    def _region_for_gaze(self, x: float, y: float) -> tuple[str, str] | None:
        trial = self.current_trial

        if trial.target_position is not None and self._normalized_aoi_rect(
            trial.target_position
        ).contains(QPointF(x, y)):
            return ("target", trial.target_position)

        for position in trial.distractor_positions:
            if self._normalized_aoi_rect(position).contains(QPointF(x, y)):
                return ("distractor", position)

        return None

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        if not self._running or self._trial_finished or self._protocol_finished:
            return

        timestamp_ns = sample.timestamp.monotonic_timestamp_ns

        if (
            self._trial_sample_count == 0
            and self._trial_started_ns is not None
            and timestamp_ns < self._trial_started_ns
        ):
            self._trial_started_ns = timestamp_ns
            self._trial_deadline_ns = (
                timestamp_ns + self.config.trial_duration_seconds * 1_000_000_000
            )

            if self._recording_events:
                self._recording_events[-1]["monotonic_timestamp_ns"] = timestamp_ns

        if self._advance_previous_interval(timestamp_ns):
            self._last_sample_timestamp_ns = timestamp_ns
            return

        self._trial_sample_count += 1
        self._run_sample_count += 1
        gaze_x = sample.gaze_x_normalized
        gaze_y = sample.gaze_y_normalized
        valid = bool(sample.gaze_valid and gaze_x is not None and gaze_y is not None)

        if not valid:
            self._trial_invalid_sample_count += 1

            if self._previous_region is not None:
                self._leave_previous_region(timestamp_ns, reason="invalid_gaze")

            self._previous_region = None
            self._previous_valid = False
            self._last_gaze_normalized = None
            self._last_sample_timestamp_ns = timestamp_ns
            self.update()
            return

        assert gaze_x is not None
        assert gaze_y is not None
        x = max(0.0, min(1.0, float(gaze_x)))
        y = max(0.0, min(1.0, float(gaze_y)))
        region = self._region_for_gaze(x, y)
        self._trial_valid_sample_count += 1
        self._run_valid_sample_count += 1
        self._last_gaze_normalized = (x, y)

        if region != self._previous_region:
            if self._previous_region is not None:
                self._leave_previous_region(timestamp_ns, reason="gaze_moved")

            if region is not None:
                role, position = region
                self._queue_event(
                    "aoi_entered",
                    timestamp_ns=timestamp_ns,
                    payload={
                        **self._trial_payload(),
                        "aoi_id": f"{role}:{position}",
                        "role": role,
                    },
                )

                if role == "target" and self._first_target_entry_ms is None:
                    started_ns = self._trial_started_ns or timestamp_ns
                    self._first_target_entry_ms = max(
                        0.0,
                        (timestamp_ns - started_ns) / 1_000_000.0,
                    )

        if region is not None:
            role, _position = region

            if role == "target":
                self._target_sample_count += 1
            else:
                self._distractor_sample_count += 1

        self._previous_region = region
        self._previous_valid = True
        self._last_sample_timestamp_ns = timestamp_ns
        self.update()

    def _trial_result(self, reason: str, timestamp_ns: int) -> dict[str, object]:
        trial = self.current_trial
        started_ns = self._trial_started_ns or timestamp_ns
        sample_count = self._trial_sample_count
        valid_count = self._trial_valid_sample_count
        target_present = trial.target_present

        if target_present:
            outcome = "target_acquired" if self._target_acquired else "target_not_acquired"
        elif self._distractor_fixation_count:
            outcome = "distractor_fixation_observed"
        else:
            outcome = "no_stable_fixation_observed"

        return {
            **self._trial_payload(trial),
            "completion_reason": reason,
            "outcome": outcome,
            "sample_count": sample_count,
            "valid_sample_count": valid_count,
            "invalid_sample_count": self._trial_invalid_sample_count,
            "valid_sample_ratio": valid_count / sample_count if sample_count else 0.0,
            "target_sample_count": self._target_sample_count,
            "distractor_sample_count": self._distractor_sample_count,
            "target_duration_ms": self._target_duration_ms,
            "distractor_duration_ms": self._distractor_duration_ms,
            "first_target_entry_ms": self._first_target_entry_ms,
            "first_target_acquired_ms": self._first_target_acquired_ms,
            "longest_continuous_target_fixation_ms": self._longest_target_run_ms,
            "target_acquired": self._target_acquired,
            "distractor_fixation_count": self._distractor_fixation_count,
            "no_target_false_fixation": (
                not target_present and self._distractor_fixation_count > 0
            ),
            "recording_duration_ms": max(0.0, (timestamp_ns - started_ns) / 1_000_000.0),
        }

    def _complete_trial(self, reason: str, *, timestamp_ns: int) -> None:
        if self._trial_finished or self._protocol_finished:
            return

        self._longest_target_run_ms = max(
            self._longest_target_run_ms,
            self._target_run_ms,
        )
        result = self._trial_result(reason, timestamp_ns)
        self._completed_trials.append(result)
        self._trial_finished = True
        self._trial_feedback = str(result["outcome"])
        self._queue_event(
            "trial_completed",
            timestamp_ns=timestamp_ns,
            payload=result,
        )
        self._advance_timer.start(self.feedback_delay_ms)
        self.update()

    def recording_result(self, reason: str) -> dict[str, object]:
        trials = list(self._completed_trials)

        if self._started and not self._trial_finished and not self._protocol_finished:
            trials.append(self._trial_result(reason, monotonic_ns()))

        target_trials = [trial for trial in trials if trial.get("target_present") is True]
        acquired_trials = [trial for trial in target_trials if trial.get("target_acquired") is True]
        no_target_trials = [trial for trial in trials if trial.get("target_present") is False]

        def numeric_values(records: list[dict[str, object]], key: str) -> list[float]:
            return [
                float(value)
                for trial in records
                if isinstance((value := trial.get(key)), (int, float))
                and not isinstance(value, bool)
            ]

        entry_latencies = numeric_values(target_trials, "first_target_entry_ms")
        acquisition_latencies = numeric_values(target_trials, "first_target_acquired_ms")
        distractor_counts = numeric_values(trials, "distractor_fixation_count")
        longest_fixations = numeric_values(
            trials,
            "longest_continuous_target_fixation_ms",
        )
        completion_status = "completed" if self._protocol_finished else "partial"

        return {
            "completion_status": completion_status,
            "completion_reason": reason.strip() or "completed",
            "randomization_seed": self.protocol_seed,
            "trial_count": len(self.trials),
            "completed_trial_count": len(self._completed_trials),
            "target_present_trial_count": len(target_trials),
            "target_acquired_trial_count": len(acquired_trials),
            "target_acquisition_ratio": (
                len(acquired_trials) / len(target_trials) if target_trials else None
            ),
            "no_target_trial_count": len(no_target_trials),
            "no_target_false_fixation_count": sum(
                trial.get("no_target_false_fixation") is True for trial in no_target_trials
            ),
            "distractor_fixation_count": sum(int(value) for value in distractor_counts),
            "valid_sample_ratio": (
                self._run_valid_sample_count / self._run_sample_count
                if self._run_sample_count
                else 0.0
            ),
            "mean_first_target_entry_ms": (
                sum(entry_latencies) / len(entry_latencies) if entry_latencies else None
            ),
            "mean_first_target_acquired_ms": (
                sum(acquisition_latencies) / len(acquisition_latencies)
                if acquisition_latencies
                else None
            ),
            "longest_continuous_target_fixation_ms": max(longest_fixations, default=0.0),
            "trials": trials,
            "interpretation": "descriptive_command_following_evidence_only",
        }

    def drain_recording_events(self) -> tuple[dict[str, object], ...]:
        events = tuple(self._recording_events)
        self._recording_events.clear()
        return events

    def _normalized_aoi_rect(self, position: str) -> QRectF:
        center_x, center_y = POSITION_CENTERS[position]
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        half_x = min(0.14, self.config.target_size_px / width / 2.0)
        half_y = min(0.18, self.config.target_size_px / height / 2.0)
        return QRectF(
            max(0.0, center_x - half_x),
            max(0.18, center_y - half_y),
            min(1.0, center_x + half_x) - max(0.0, center_x - half_x),
            min(0.98, center_y + half_y) - max(0.18, center_y - half_y),
        )

    def _aoi_payload(self, position: str, *, role: str) -> dict[str, object]:
        rectangle = self._normalized_aoi_rect(position)
        return {
            "aoi_id": f"{role}:{position}",
            "role": "target" if role == "target" else "incorrect_option",
            "left": rectangle.left(),
            "top": rectangle.top(),
            "right": rectangle.right(),
            "bottom": rectangle.bottom(),
            "label": role,
            "metadata": {
                "position": position,
                "condition": self.current_trial.condition.value,
            },
        }

    def recording_context_for_sample(self, _sample: EyeTrackerSample) -> dict[str, object]:
        trial = self.current_trial
        aois: list[dict[str, object]] = []
        reference: dict[str, object] | None = None

        if trial.target_position is not None:
            reference = self._aoi_payload(trial.target_position, role="target")
            aois.append(reference)

        aois.extend(
            self._aoi_payload(position, role="distractor")
            for position in trial.distractor_positions
        )
        return {
            "question_id": trial.trial_id,
            "phase": "feedback" if self._trial_finished else "stimulus",
            "aois": aois,
            "reference_aoi": reference,
            "question_metadata": self._trial_payload(trial),
            "register_layout": True,
        }

    def _pixel_rect(self, position: str) -> QRectF:
        normalized = self._normalized_aoi_rect(position)
        return QRectF(
            normalized.left() * self.width(),
            normalized.top() * self.height(),
            normalized.width() * self.width(),
            normalized.height() * self.height(),
        )

    def _draw_stimulus(
        self,
        painter: QPainter,
        *,
        position: str,
        shape: TargetShape,
        color: str,
    ) -> None:
        rectangle = self._pixel_rect(position)
        path = _shape_path(shape, rectangle)
        painter.setPen(QPen(QColor("#ffffff"), max(3.0, rectangle.width() * 0.025)))
        painter.fillPath(path, QColor(color))
        painter.drawPath(path)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(self.config.background_color))
        trial = self.current_trial

        painter.setPen(QColor("#ffffff"))
        font = painter.font()
        font.setFamily("Microsoft YaHei UI")
        font.setPointSize(self.config.instruction_font_size_pt)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(30.0, 8.0, max(1.0, self.width() - 60.0), self.height() * 0.16),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            trial.prompt,
        )

        for position in trial.distractor_positions:
            self._draw_stimulus(
                painter,
                position=position,
                shape=self.config.distractor_shape,
                color=self.config.distractor_color,
            )

        if trial.target_position is not None:
            self._draw_stimulus(
                painter,
                position=trial.target_position,
                shape=self.config.target_shape,
                color=self.config.target_color,
            )

            if self._target_run_ms > 0 and not self._trial_finished:
                rectangle = self._pixel_rect(trial.target_position).adjusted(-12, -12, 12, 12)
                progress = min(1.0, self._target_run_ms / self.config.dwell_time_ms)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#ffe66d"), 9))
                painter.drawArc(rectangle, 90 * 16, int(-360 * 16 * progress))

        if self._trial_feedback == "target_acquired" and trial.target_position is not None:
            rectangle = self._pixel_rect(trial.target_position).adjusted(-16, -16, 16, 16)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#35d07f"), 14))
            painter.drawEllipse(rectangle)

        if self.config.show_gaze_cursor and self._last_gaze_normalized is not None:
            x, y = self._last_gaze_normalized
            point = QPointF(x * self.width(), y * self.height())
            painter.setBrush(QColor(255, 255, 255, 50))
            painter.setPen(QPen(QColor("#40e0ff"), 4))
            painter.drawEllipse(point, 18, 18)

        small = painter.font()
        small.setPointSize(18)
        small.setBold(True)
        painter.setFont(small)
        painter.setPen(QColor("#dcecff"))
        painter.drawText(22, self.height() - 22, f"{trial.trial_number}/{trial.trial_count}")
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.allow_mouse_fallback or self.width() <= 0 or self.height() <= 0:
            event.ignore()
            return

        position = event.position()
        self._last_gaze_normalized = (
            max(0.0, min(1.0, position.x() / self.width())),
            max(0.0, min(1.0, position.y() / self.height())),
        )
        self.update()


class InstructionFixationSetupDialog(QDialog):
    """Configure the instruction, stimuli, AOIs, and trial composition."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        config: InstructionFixationConfig | None = None,
    ) -> None:
        super().__init__(parent)
        initial = config or InstructionFixationConfig()
        self._randomization_seed = initial.randomization_seed
        self.setWindowTitle("随指令注视设置")
        self.resize(700, 760)

        guide = QLabel(
            "患者将听到并看到“请注视……”指令。目标存在、目标伴干扰和无目标试次"
            "会按设置组合；无目标试次只记录是否出现干扰区稳定注视，不自动判定意识。"
        )
        guide.setWordWrap(True)
        guide.setStyleSheet("color:#365269; background:#eef7ff; padding:9px;")
        form = QFormLayout()

        self.description_edit = QLineEdit(initial.target_description)
        self.description_edit.setMaxLength(80)
        form.addRow("指令中的目标描述：", self.description_edit)

        self.target_shape_combo = self._shape_combo(initial.target_shape)
        self.distractor_shape_combo = self._shape_combo(initial.distractor_shape)
        form.addRow("目标形状：", self.target_shape_combo)
        form.addRow("干扰形状：", self.distractor_shape_combo)

        self.target_color_edit = QLineEdit(initial.target_color)
        self.distractor_color_edit = QLineEdit(initial.distractor_color)
        self.background_color_edit = QLineEdit(initial.background_color)
        form.addRow(
            "目标颜色：",
            self._color_row(self.target_color_edit, "选择目标颜色"),
        )
        form.addRow(
            "干扰颜色：",
            self._color_row(self.distractor_color_edit, "选择干扰颜色"),
        )
        form.addRow(
            "背景颜色：",
            self._color_row(self.background_color_edit, "选择背景颜色"),
        )

        self.position_list = QListWidget()
        self.position_list.setMinimumHeight(150)

        for position, label in POSITION_LABELS.items():
            item = QListWidgetItem(label, self.position_list)
            item.setData(Qt.ItemDataRole.UserRole, position)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if position in initial.position_ids
                else Qt.CheckState.Unchecked
            )

        form.addRow("可用屏幕 AOI（多选）：", self.position_list)

        self.target_only_spin = self._count_spin(initial.target_only_trial_count)
        self.distractor_trial_spin = self._count_spin(initial.distractor_trial_count)
        self.no_target_spin = self._count_spin(initial.no_target_trial_count)
        form.addRow("仅目标试次数：", self.target_only_spin)
        form.addRow("目标 + 干扰试次数：", self.distractor_trial_spin)
        form.addRow("无目标试次数：", self.no_target_spin)

        self.distractor_count_spin = QSpinBox()
        self.distractor_count_spin.setRange(1, 6)
        self.distractor_count_spin.setValue(initial.distractor_count)
        form.addRow("每个干扰试次的干扰数：", self.distractor_count_spin)

        self.target_size_spin = QSpinBox()
        self.target_size_spin.setRange(40, 600)
        self.target_size_spin.setSuffix(" px")
        self.target_size_spin.setValue(initial.target_size_px)
        form.addRow("刺激大小：", self.target_size_spin)

        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(250, 10_000)
        self.dwell_spin.setSingleStep(100)
        self.dwell_spin.setSuffix(" ms")
        self.dwell_spin.setValue(initial.dwell_time_ms)
        form.addRow("持续注视阈值：", self.dwell_spin)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(3, 120)
        self.duration_spin.setSuffix(" 秒/试次")
        self.duration_spin.setValue(initial.trial_duration_seconds)
        form.addRow("每试次最长时长：", self.duration_spin)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(20, 120)
        self.font_size_spin.setSuffix(" pt")
        self.font_size_spin.setValue(initial.instruction_font_size_pt)
        form.addRow("指令字号：", self.font_size_spin)

        self.randomize_check = QCheckBox("随机排列三类试次，并平衡目标位置")
        self.randomize_check.setChecked(initial.randomize_trial_order)
        form.addRow("试次顺序：", self.randomize_check)

        self.gaze_cursor_check = QCheckBox("患者屏幕显示实时视线光标（默认关闭）")
        self.gaze_cursor_check.setChecked(initial.show_gaze_cursor)
        form.addRow("视线反馈：", self.gaze_cursor_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addWidget(guide)
        root.addLayout(form)
        root.addWidget(buttons)

    @staticmethod
    def _shape_combo(value: TargetShape) -> QComboBox:
        combo = QComboBox()

        for label, shape in (
            ("圆形", TargetShape.CIRCLE),
            ("方形", TargetShape.SQUARE),
            ("菱形", TargetShape.DIAMOND),
            ("星形", TargetShape.STAR),
        ):
            combo.addItem(label, shape)

        combo.setCurrentIndex(combo.findData(value))
        return combo

    @staticmethod
    def _count_spin(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setValue(value)
        spin.setSuffix(" 次")
        return spin

    def _color_row(self, edit: QLineEdit, title: str) -> QHBoxLayout:
        row = QHBoxLayout()
        button = QPushButton("选择…")
        button.clicked.connect(lambda: self._select_color(edit, title))
        row.addWidget(edit, 1)
        row.addWidget(button)
        return row

    def _select_color(self, edit: QLineEdit, title: str) -> None:
        color = QColorDialog.getColor(QColor(edit.text()), self, title)

        if color.isValid():
            edit.setText(color.name())

    def _selected_positions(self) -> tuple[str, ...]:
        return tuple(
            str(item.data(Qt.ItemDataRole.UserRole))
            for index in range(self.position_list.count())
            if (item := self.position_list.item(index)).checkState() is Qt.CheckState.Checked
        )

    def build_config(self) -> InstructionFixationConfig:
        return InstructionFixationConfig(
            target_description=self.description_edit.text(),
            target_shape=TargetShape(self.target_shape_combo.currentData()),
            target_color=self.target_color_edit.text(),
            distractor_shape=TargetShape(self.distractor_shape_combo.currentData()),
            distractor_color=self.distractor_color_edit.text(),
            background_color=self.background_color_edit.text(),
            position_ids=self._selected_positions(),
            target_only_trial_count=self.target_only_spin.value(),
            distractor_trial_count=self.distractor_trial_spin.value(),
            no_target_trial_count=self.no_target_spin.value(),
            distractor_count=self.distractor_count_spin.value(),
            target_size_px=self.target_size_spin.value(),
            dwell_time_ms=self.dwell_spin.value(),
            trial_duration_seconds=self.duration_spin.value(),
            instruction_font_size_pt=self.font_size_spin.value(),
            randomize_trial_order=self.randomize_check.isChecked(),
            randomization_seed=self._randomization_seed,
            show_gaze_cursor=self.gaze_cursor_check.isChecked(),
        )

    def _accept_if_valid(self) -> None:
        try:
            self.build_config()
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "随指令注视设置无效", str(error))
            return

        self.accept()
