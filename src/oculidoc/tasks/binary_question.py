"""Horizontal two-option gaze-question task."""

from __future__ import annotations

import json
import random
import secrets
from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns

from PySide6.QtCore import (
    QPoint,
    Qt,
    Signal,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.devices.contracts import (
    EyeTrackerSample,
)
from oculidoc.tasks.question_bank import (
    BinaryQuestionType,
    CommonQuestionStore,
    CommonQuestionTemplate,
)


@dataclass(
    frozen=True,
    slots=True,
    init=False,
)
class BinaryQuestionConfig:
    """Logical options independent of their displayed sides."""

    question: str
    option_1: str
    option_2: str
    question_type: BinaryQuestionType
    correct_option_id: str | None
    dwell_time_ms: int
    duration_seconds: int
    question_font_family: str
    question_font_size_pt: int
    option_font_size_pt: int
    neutral_zone_width: float
    randomize_sides: bool
    randomization_seed: int | None

    def __init__(
        self,
        question: str,
        option_1: str | None = None,
        option_2: str | None = None,
        *,
        question_type: BinaryQuestionType | str = BinaryQuestionType.INQUIRY,
        correct_option_id: str | None = None,
        dwell_time_ms: int = 1_200,
        duration_seconds: int = 30,
        question_font_family: str = "Microsoft YaHei UI",
        question_font_size_pt: int = 48,
        option_font_size_pt: int = 44,
        neutral_zone_width: float = 0.08,
        randomize_sides: bool = True,
        randomization_seed: int | None = None,
        left_answer: str | None = None,
        right_answer: str | None = None,
        correct_side: str | None = None,
    ) -> None:
        legacy_arguments = any(
            value is not None
            for value in (
                left_answer,
                right_answer,
                correct_side,
            )
        )

        if option_1 is None:
            option_1 = left_answer

        if option_2 is None:
            option_2 = right_answer

        option_1 = option_1 or "是"
        option_2 = option_2 or "否"
        normalized_type = BinaryQuestionType(question_type)

        if legacy_arguments:
            randomize_sides = False

            if correct_side is None:
                normalized_type = BinaryQuestionType.INQUIRY
                correct_option_id = None
            elif correct_side == "left":
                normalized_type = BinaryQuestionType.YES_NO
                correct_option_id = "option_1"
            elif correct_side == "right":
                normalized_type = BinaryQuestionType.YES_NO
                correct_option_id = "option_2"
            else:
                raise ValueError("correct_side must be left, right, or None.")

        normalized_question = question.strip()
        normalized_option_1 = option_1.strip()
        normalized_option_2 = option_2.strip()
        normalized_family = question_font_family.strip()

        for field_name, value in (
            ("question", normalized_question),
            ("option_1", normalized_option_1),
            ("option_2", normalized_option_2),
            ("question_font_family", normalized_family),
        ):
            if not value:
                raise ValueError(f"{field_name} cannot be empty.")

        if normalized_type.is_scored:
            correct_option_id = correct_option_id or "option_1"

            if correct_option_id not in {"option_1", "option_2"}:
                raise ValueError("correct_option_id must be option_1 or option_2.")
        else:
            correct_option_id = None

        if not 250 <= dwell_time_ms <= 10_000:
            raise ValueError("dwell_time_ms must be between 250 and 10000.")

        if not 5 <= duration_seconds <= 600:
            raise ValueError("duration_seconds must be between 5 and 600.")

        if not 12 <= question_font_size_pt <= 120:
            raise ValueError("question_font_size_pt must be between 12 and 120.")

        if not 12 <= option_font_size_pt <= 120:
            raise ValueError("option_font_size_pt must be between 12 and 120.")

        if not 0.0 <= neutral_zone_width <= 0.6:
            raise ValueError("neutral_zone_width must be between 0 and 0.6.")

        if randomization_seed is not None and randomization_seed < 0:
            raise ValueError("randomization_seed cannot be negative.")

        object.__setattr__(self, "question", normalized_question)
        object.__setattr__(self, "option_1", normalized_option_1)
        object.__setattr__(self, "option_2", normalized_option_2)
        object.__setattr__(self, "question_type", normalized_type)
        object.__setattr__(self, "correct_option_id", correct_option_id)
        object.__setattr__(self, "dwell_time_ms", int(dwell_time_ms))
        object.__setattr__(self, "duration_seconds", int(duration_seconds))
        object.__setattr__(
            self,
            "question_font_family",
            normalized_family,
        )
        object.__setattr__(
            self,
            "question_font_size_pt",
            int(question_font_size_pt),
        )
        object.__setattr__(
            self,
            "option_font_size_pt",
            int(option_font_size_pt),
        )
        object.__setattr__(
            self,
            "neutral_zone_width",
            float(neutral_zone_width),
        )
        object.__setattr__(
            self,
            "randomize_sides",
            bool(randomize_sides),
        )
        object.__setattr__(
            self,
            "randomization_seed",
            randomization_seed,
        )

    @property
    def is_scored(self) -> bool:
        return self.question_type.is_scored

    @property
    def left_answer(self) -> str:
        """Legacy logical-option alias."""

        return self.option_1

    @property
    def right_answer(self) -> str:
        """Legacy logical-option alias."""

        return self.option_2

    @property
    def correct_side(self) -> str | None:
        """Legacy logical-side alias."""

        if self.correct_option_id == "option_1":
            return "left"

        if self.correct_option_id == "option_2":
            return "right"

        return None


class BinaryQuestionTask(QWidget):
    """Select one of two randomized options by gaze dwell."""

    answered = Signal(str, str)

    def __init__(
        self,
        config: BinaryQuestionConfig,
        *,
        allow_mouse_fallback: bool = True,
    ) -> None:
        super().__init__()
        self.config = config
        self.allow_mouse_fallback = allow_mouse_fallback
        self.randomization_seed = (
            config.randomization_seed
            if config.randomization_seed is not None
            else secrets.randbits(63)
        )
        option_order = ["option_1", "option_2"]

        if config.randomize_sides:
            random.Random(self.randomization_seed).shuffle(option_order)

        self._option_by_side = {
            "left": option_order[0],
            "right": option_order[1],
        }
        self._active_side: str | None = None
        self._dwell_ms = 0.0
        self._last_timestamp_ns: int | None = None
        self._result: tuple[str, str] | None = None
        self._recording_events: list[dict[str, object]] = []
        self._task_started_monotonic_ns: int | None = None
        self._answer_committed_monotonic_ns: int | None = None
        self._confirmation_dwell_ms: float | None = None
        self._selection_method: str | None = None
        self._final_event_recorded = False

        self.setMinimumSize(800, 520)
        self.setStyleSheet(
            """
            QWidget {
                background: #071521;
                color: white;
                font-family: "Microsoft YaHei UI";
            }
            QLabel#questionLabel {
                font-weight: 700;
                padding: 24px;
            }
            QPushButton#answerButton {
                min-height: 620px;
                border: 8px solid #d9e7f2;
                border-radius: 24px;
                background: #173957;
                color: white;
                font-weight: 800;
                padding: 20px;
            }
            QPushButton#answerButton[active="true"] {
                border-color: #ffe66d;
                background: #285c85;
            }
            QProgressBar {
                min-height: 28px;
                border: 2px solid #d9e7f2;
                border-radius: 10px;
                text-align: center;
                font-size: 16px;
            }
            QProgressBar::chunk {
                background: #ffe66d;
                border-radius: 8px;
            }
            """
        )

        self.question_label = QLabel(config.question)
        self.question_label.setObjectName("questionLabel")
        self.question_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.question_label.setWordWrap(True)
        self.question_label.setMaximumHeight(190)

        question_font = QFont(config.question_font_family)
        question_font.setPointSize(config.question_font_size_pt)
        question_font.setBold(True)
        self.question_label.setFont(question_font)
        self.question_label.setStyleSheet(
            "font-family: "
            f'"{config.question_font_family}"; '
            "font-size: "
            f"{config.question_font_size_pt}pt; "
            "font-weight: 700;"
        )

        self.left_button = QPushButton(self._answer_for_side("left"))
        self.right_button = QPushButton(self._answer_for_side("right"))

        option_font = QFont(config.question_font_family)
        option_font.setPointSize(config.option_font_size_pt)
        option_font.setBold(True)

        for button in (
            self.left_button,
            self.right_button,
        ):
            button.setObjectName("answerButton")
            button.setProperty("active", False)
            button.setMinimumHeight(620)
            button.setFont(option_font)
            button.setStyleSheet(f"font-size: {config.option_font_size_pt}pt;")

        self.left_button.clicked.connect(lambda: self._commit("left"))
        self.right_button.clicked.connect(lambda: self._commit("right"))

        self.left_progress = QProgressBar()
        self.right_progress = QProgressBar()

        for progress in (
            self.left_progress,
            self.right_progress,
        ):
            progress.setRange(0, config.dwell_time_ms)
            progress.setValue(0)

        left_layout = QVBoxLayout()
        left_layout.addWidget(self.left_button, 1)
        left_layout.addWidget(self.left_progress)

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.right_button, 1)
        right_layout.addWidget(self.right_progress)

        if not self.allow_mouse_fallback:
            self.setCursor(Qt.CursorShape.BlankCursor)

            for button in (
                self.left_button,
                self.right_button,
            ):
                button.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                    True,
                )
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        answers = QHBoxLayout()
        answers.setSpacing(6)
        answers.addLayout(left_layout, 1)
        answers.addLayout(right_layout, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 6)
        root.setSpacing(6)
        root.addWidget(self.question_label)
        root.addLayout(answers, 1)

    @property
    def result(
        self,
    ) -> tuple[str, str] | None:
        return self._result

    @property
    def displayed_options(
        self,
    ) -> dict[str, str]:
        return {side: self._answer_for_side(side) for side in ("left", "right")}

    @property
    def displayed_correct_side(
        self,
    ) -> str | None:
        correct_option = self.config.correct_option_id

        if correct_option is None:
            return None

        return next(
            (
                side
                for side, option_id in self._option_by_side.items()
                if option_id == correct_option
            ),
            None,
        )

    def _answer_for_option(
        self,
        option_id: str,
    ) -> str:
        if option_id == "option_1":
            return self.config.option_1

        if option_id == "option_2":
            return self.config.option_2

        raise ValueError(f"Unknown option identifier: {option_id}")

    def _answer_for_side(
        self,
        side: str,
    ) -> str:
        return self._answer_for_option(self._option_by_side[side])

    @property
    def selected_option_id(
        self,
    ) -> str | None:
        """Return the selected logical option identifier."""

        if self._result is None:
            return None

        return self._option_by_side[self._result[0]]

    def _event_payload_for_side(
        self,
        side: str,
    ) -> dict[str, object]:
        option_id = self._option_by_side[side]
        correct: bool | None = None

        if self.config.is_scored:
            correct = option_id == self.config.correct_option_id

        return {
            "question_id": ("binary-question-1"),
            "side": side,
            "logical_option_id": (option_id),
            "selected_option_id": (option_id),
            "answer": (self._answer_for_option(option_id)),
            "selected_answer": (self._answer_for_option(option_id)),
            "is_scored": (self.config.is_scored),
            "correct_option_id": (self.config.correct_option_id),
            "correct": correct,
        }

    def _queue_recording_event(
        self,
        event_type: str,
        *,
        monotonic_timestamp_ns: (int | None) = None,
        payload: (dict[str, object] | None) = None,
    ) -> None:
        timestamp_ns = (
            monotonic_ns() if monotonic_timestamp_ns is None else int(monotonic_timestamp_ns)
        )

        if timestamp_ns < 0:
            raise ValueError("monotonic_timestamp_ns cannot be negative.")

        self._recording_events.append(
            {
                "event_type": event_type,
                "monotonic_timestamp_ns": (timestamp_ns),
                "payload": dict(payload or {}),
            }
        )

    def _ensure_question_presented(
        self,
        monotonic_timestamp_ns: (int | None) = None,
    ) -> int:
        if self._task_started_monotonic_ns is not None:
            return self._task_started_monotonic_ns

        timestamp_ns = (
            monotonic_ns() if monotonic_timestamp_ns is None else int(monotonic_timestamp_ns)
        )

        if timestamp_ns < 0:
            raise ValueError("monotonic_timestamp_ns cannot be negative.")

        self._task_started_monotonic_ns = timestamp_ns
        self._queue_recording_event(
            "question_presented",
            monotonic_timestamp_ns=(timestamp_ns),
            payload={
                "question_id": ("binary-question-1"),
                "question": (self.config.question),
                "question_type": (self.config.question_type.value),
                "is_scored": (self.config.is_scored),
                "option_1": (self.config.option_1),
                "option_2": (self.config.option_2),
                "correct_option_id": (self.config.correct_option_id),
                "left_option_id": (self._option_by_side["left"]),
                "right_option_id": (self._option_by_side["right"]),
                "left_answer": (self._answer_for_side("left")),
                "right_answer": (self._answer_for_side("right")),
                "displayed_correct_side": (self.displayed_correct_side),
                "randomization_seed": (self.randomization_seed),
                "configured_dwell_ms": (self.config.dwell_time_ms),
            },
        )
        return timestamp_ns

    def drain_recording_events(
        self,
    ) -> tuple[
        dict[str, object],
        ...,
    ]:
        """Return and clear pending semantic task events."""

        events = tuple(self._recording_events)
        self._recording_events.clear()
        return events

    def recording_result(
        self,
        reason: str,
    ) -> dict[str, object]:
        """Return the clinical result written to task_result.json."""

        reason_text = reason.strip() if reason.strip() else "completed"
        self._ensure_question_presented()

        selected_side: str | None = None
        selected_answer: str | None = None

        if self._result is not None:
            (
                selected_side,
                selected_answer,
            ) = self._result

        selected_option_id = self.selected_option_id
        correct: bool | None = None

        if self.config.is_scored and selected_option_id is not None:
            correct = selected_option_id == self.config.correct_option_id

        reaction_time_ms: float | None = None

        if (
            self._answer_committed_monotonic_ns is not None
            and self._task_started_monotonic_ns is not None
        ):
            reaction_time_ms = max(
                0.0,
                (self._answer_committed_monotonic_ns - self._task_started_monotonic_ns)
                / 1_000_000.0,
            )

        completion_status = "answered" if self._result is not None else "unanswered"
        result = {
            "question_id": ("binary-question-1"),
            "question": (self.config.question),
            "question_type": (self.config.question_type.value),
            "selected_option_id": (selected_option_id),
            "selected_side": (selected_side),
            "selected_answer": (selected_answer),
            "is_scored": (self.config.is_scored),
            "correct_option_id": (self.config.correct_option_id),
            "correct": correct,
            "displayed_correct_side": (self.displayed_correct_side),
            "reaction_time_ms": (reaction_time_ms),
            "confirmation_dwell_ms": (self._confirmation_dwell_ms),
            "configured_dwell_ms": (self.config.dwell_time_ms),
            "selection_method": (self._selection_method),
            "completion_status": (completion_status),
            "completion_reason": (reason_text),
            "randomization_seed": (self.randomization_seed),
            "left_option_id": (self._option_by_side["left"]),
            "right_option_id": (self._option_by_side["right"]),
            "left_answer": (self._answer_for_side("left")),
            "right_answer": (self._answer_for_side("right")),
        }

        if not self._final_event_recorded:
            event_type = "task_completed" if self._result is not None else "task_unanswered"
            event_timestamp_ns = self._answer_committed_monotonic_ns or monotonic_ns()
            self._queue_recording_event(
                event_type,
                monotonic_timestamp_ns=(event_timestamp_ns),
                payload=result,
            )
            self._final_event_recorded = True

        return result

    def start(self) -> None:
        self.reset()
        self._ensure_question_presented()

    def stop(self) -> None:
        return None

    def reset(self) -> None:
        self._active_side = None
        self._dwell_ms = 0.0
        self._last_timestamp_ns = None
        self._result = None
        self._recording_events.clear()
        self._task_started_monotonic_ns = None
        self._answer_committed_monotonic_ns = None
        self._confirmation_dwell_ms = None
        self._selection_method = None
        self._final_event_recorded = False

        self.left_button.setEnabled(True)
        self.right_button.setEnabled(True)
        self.left_progress.setValue(0)
        self.right_progress.setValue(0)
        self.question_label.setText(self.config.question)
        self._refresh_active_side()

    def _side_for_gaze(
        self,
        gaze_x: float,
    ) -> str | None:
        half_neutral = self.config.neutral_zone_width / 2.0

        if gaze_x < 0.5 - half_neutral:
            return "left"

        if gaze_x > 0.5 + half_neutral:
            return "right"

        return None

    def _button_bounds_normalized(
        self,
        button: QPushButton,
        *,
        side: str,
    ) -> tuple[
        float,
        float,
        float,
        float,
    ]:
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        top_left = button.mapTo(self, QPoint(0, 0))
        left = max(0.0, min(1.0, top_left.x() / width))
        top = max(0.0, min(1.0, top_left.y() / height))
        right = max(
            0.0,
            min(
                1.0,
                (top_left.x() + button.width()) / width,
            ),
        )
        bottom = max(
            0.0,
            min(
                1.0,
                (top_left.y() + button.height()) / height,
            ),
        )

        if right > left and bottom > top:
            return (
                left,
                top,
                right,
                bottom,
            )

        half_neutral = self.config.neutral_zone_width / 2.0

        if side == "left":
            return (
                0.0,
                0.0,
                0.5 - half_neutral,
                1.0,
            )

        return (
            0.5 + half_neutral,
            0.0,
            1.0,
            1.0,
        )

    def recording_context_for_sample(
        self,
        sample: EyeTrackerSample,
    ) -> dict[str, object]:
        """Return randomized AOIs and logical roles."""

        correct_option = self.config.correct_option_id

        def role_for_option(
            option_id: str,
        ) -> str:
            if not self.config.is_scored:
                return "other"

            if option_id == correct_option:
                return "correct_option"

            return "incorrect_option"

        aois: list[dict[str, object]] = []

        for side, button in (
            ("left", self.left_button),
            ("right", self.right_button),
        ):
            option_id = self._option_by_side[side]
            answer = self._answer_for_option(option_id)
            bounds = self._button_bounds_normalized(
                button,
                side=side,
            )
            aois.append(
                {
                    "aoi_id": f"{side}_answer",
                    "role": role_for_option(option_id),
                    "left": bounds[0],
                    "top": bounds[1],
                    "right": bounds[2],
                    "bottom": bounds[3],
                    "label": answer,
                    "metadata": {
                        "side": side,
                        "answer": answer,
                        "logical_option_id": option_id,
                        "is_scored": self.config.is_scored,
                    },
                }
            )

        sample_side: str | None = None

        if sample.gaze_valid and sample.gaze_x_normalized is not None:
            sample_side = self._side_for_gaze(
                max(
                    0.0,
                    min(
                        1.0,
                        float(sample.gaze_x_normalized),
                    ),
                )
            )

        phase = f"dwell_{sample_side}" if sample_side is not None else "response"

        if self._result is not None:
            phase = "answered"

        return {
            "question_id": "binary-question-1",
            "phase": phase,
            "aois": aois,
            "register_layout": True,
            "question_metadata": {
                "question": self.config.question,
                "question_type": self.config.question_type.value,
                "option_1": self.config.option_1,
                "option_2": self.config.option_2,
                "is_scored": self.config.is_scored,
                "correct_option_id": correct_option,
                "left_option_id": self._option_by_side["left"],
                "right_option_id": self._option_by_side["right"],
                "left_answer": self._answer_for_side("left"),
                "right_answer": self._answer_for_side("right"),
                "correct_side": self.displayed_correct_side,
                "randomize_sides": self.config.randomize_sides,
                "randomization_seed": self.randomization_seed,
                "layout": "horizontal",
            },
        }

    def consume_sample(
        self,
        sample: EyeTrackerSample,
    ) -> None:
        timestamp_ns = sample.timestamp.monotonic_timestamp_ns

        if self._last_timestamp_ns is None or timestamp_ns <= self._last_timestamp_ns:
            elapsed_ms = 0.0
        else:
            elapsed_ms = min(
                250.0,
                (timestamp_ns - self._last_timestamp_ns) / 1_000_000.0,
            )

        self._last_timestamp_ns = timestamp_ns

        if not sample.gaze_valid:
            self.advance_dwell(
                None,
                elapsed_ms,
                monotonic_timestamp_ns=(timestamp_ns),
                interruption_reason=("invalid_gaze"),
            )
            return

        gaze_x = max(
            0.0,
            min(
                1.0,
                float(sample.gaze_x_normalized),
            ),
        )
        side = self._side_for_gaze(gaze_x)
        self.advance_dwell(
            side,
            elapsed_ms,
            monotonic_timestamp_ns=(timestamp_ns),
            interruption_reason=("neutral_zone" if side is None else "side_changed"),
        )

    def advance_dwell(
        self,
        side: str | None,
        elapsed_ms: float,
        *,
        monotonic_timestamp_ns: (int | None) = None,
        interruption_reason: str = ("neutral_or_invalid"),
    ) -> None:
        """Advance dwell state for testing and gaze input."""

        if self._result is not None:
            return

        if side not in {
            None,
            "left",
            "right",
        }:
            raise ValueError("side must be left, right, or None.")

        if elapsed_ms < 0:
            raise ValueError("elapsed_ms cannot be negative.")

        event_timestamp_ns = (
            monotonic_ns() if monotonic_timestamp_ns is None else int(monotonic_timestamp_ns)
        )
        self._ensure_question_presented(event_timestamp_ns)

        if side is None:
            if self._active_side is not None:
                payload = self._event_payload_for_side(self._active_side)
                payload.update(
                    {
                        "accumulated_dwell_ms": (self._dwell_ms),
                        "reason": (interruption_reason),
                    }
                )
                self._queue_recording_event(
                    "dwell_cancelled",
                    monotonic_timestamp_ns=(event_timestamp_ns),
                    payload=payload,
                )

            self._active_side = None
            self._dwell_ms = 0.0
            self._refresh_progress()
            self._refresh_active_side()
            return

        if side != self._active_side:
            if self._active_side is not None:
                previous_payload = self._event_payload_for_side(self._active_side)
                previous_payload.update(
                    {
                        "accumulated_dwell_ms": (self._dwell_ms),
                        "reason": ("side_changed"),
                    }
                )
                self._queue_recording_event(
                    "dwell_cancelled",
                    monotonic_timestamp_ns=(event_timestamp_ns),
                    payload=(previous_payload),
                )

            self._active_side = side
            self._dwell_ms = 0.0
            payload = self._event_payload_for_side(side)
            self._queue_recording_event(
                "gaze_entered_option",
                monotonic_timestamp_ns=(event_timestamp_ns),
                payload=payload,
            )
            self._queue_recording_event(
                "dwell_started",
                monotonic_timestamp_ns=(event_timestamp_ns),
                payload=payload,
            )

        self._dwell_ms += elapsed_ms
        self._refresh_progress()
        self._refresh_active_side()

        if self._dwell_ms >= self.config.dwell_time_ms:
            self._commit(
                side,
                monotonic_timestamp_ns=(event_timestamp_ns),
            )

    def _refresh_progress(self) -> None:
        left_value = int(self._dwell_ms) if self._active_side == "left" else 0
        right_value = int(self._dwell_ms) if self._active_side == "right" else 0

        self.left_progress.setValue(
            min(
                self.config.dwell_time_ms,
                left_value,
            )
        )
        self.right_progress.setValue(
            min(
                self.config.dwell_time_ms,
                right_value,
            )
        )

    def _refresh_active_side(self) -> None:
        self.left_button.setProperty(
            "active",
            self._active_side == "left",
        )
        self.right_button.setProperty(
            "active",
            self._active_side == "right",
        )

        for button in (
            self.left_button,
            self.right_button,
        ):
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _commit(
        self,
        side: str,
        *,
        monotonic_timestamp_ns: (int | None) = None,
    ) -> None:
        if self._result is not None:
            return

        event_timestamp_ns = (
            monotonic_ns() if monotonic_timestamp_ns is None else int(monotonic_timestamp_ns)
        )
        self._ensure_question_presented(event_timestamp_ns)
        answer = self._answer_for_side(side)
        self._result = (
            side,
            answer,
        )
        self._answer_committed_monotonic_ns = event_timestamp_ns
        self._confirmation_dwell_ms = float(self._dwell_ms)
        self._selection_method = (
            "gaze_dwell" if self._dwell_ms >= self.config.dwell_time_ms else "manual_fallback"
        )
        payload = self._event_payload_for_side(side)
        payload.update(
            {
                "reaction_time_ms": max(
                    0.0,
                    (event_timestamp_ns - (self._task_started_monotonic_ns or event_timestamp_ns))
                    / 1_000_000.0,
                ),
                "confirmation_dwell_ms": (self._confirmation_dwell_ms),
                "configured_dwell_ms": (self.config.dwell_time_ms),
                "selection_method": (self._selection_method),
                "randomization_seed": (self.randomization_seed),
            }
        )
        self._queue_recording_event(
            "answer_committed",
            monotonic_timestamp_ns=(event_timestamp_ns),
            payload=payload,
        )

        self.left_button.setEnabled(False)
        self.right_button.setEnabled(False)
        self.question_label.setText(f"已选择：{answer}")
        self.answered.emit(
            side,
            answer,
        )


class BinaryQuestionSetupDialog(QDialog):
    """Configure a reusable two-option question."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        question_bank_path: str | Path | None = None,
        config: BinaryQuestionConfig | None = None,
    ) -> None:
        super().__init__(parent)
        initial = config or BinaryQuestionConfig(question="你现在感到舒服吗？")
        self._randomization_seed = initial.randomization_seed
        self.setWindowTitle("左右二分问答设置")
        self.resize(680, 680)

        if question_bank_path is None:
            question_bank_path = Path.home() / ".oculidoc" / "data" / "common_questions.json"

        self.question_store = CommonQuestionStore(question_bank_path)
        self._templates: dict[str, CommonQuestionTemplate] = {}
        form = QFormLayout()

        self.common_question_combo = QComboBox()
        self.common_question_combo.setObjectName("commonQuestionCombo")

        self.edit_common_button = QPushButton("保存修改")
        self.edit_common_button.setObjectName("editCommonQuestionButton")
        self.edit_common_button.setEnabled(False)
        self.edit_common_button.clicked.connect(self._edit_common_question)

        self.add_common_button = QPushButton("添加新常用问题")
        self.add_common_button.setObjectName("addCommonQuestionButton")
        self.add_common_button.clicked.connect(self._add_common_question)

        common_row = QHBoxLayout()
        common_row.addWidget(self.common_question_combo, 1)
        common_row.addWidget(self.edit_common_button)
        common_row.addWidget(self.add_common_button)
        form.addRow("常用问题：", common_row)

        self.question_type_group = QButtonGroup(self)
        self.question_type_group.setExclusive(True)
        self.question_type_buttons: dict[BinaryQuestionType, QRadioButton] = {}
        question_type_row = QHBoxLayout()

        for question_type in BinaryQuestionType:
            button = QRadioButton(question_type.display_label)
            button.setObjectName(f"binaryQuestionType_{question_type.value}")
            button.clicked.connect(self._refresh_option_labels)
            self.question_type_group.addButton(button)
            self.question_type_buttons[question_type] = button
            question_type_row.addWidget(button)

        question_type_row.addStretch(1)
        self.question_type_buttons[initial.question_type].setChecked(True)
        form.addRow("问题类型：", question_type_row)

        self.question_edit = QLineEdit(initial.question)
        self.question_edit.setObjectName("binaryQuestionEdit")
        form.addRow("问题：", self.question_edit)

        self.option_1_edit = QLineEdit(initial.option_1)
        self.option_2_edit = QLineEdit(initial.option_2)
        self.option_1_edit.setObjectName("binaryOption1Edit")
        self.option_2_edit.setObjectName("binaryOption2Edit")
        self.option_1_label = QLabel("选项1：")
        self.option_2_label = QLabel("选项2：")
        form.addRow(self.option_1_label, self.option_1_edit)
        form.addRow(self.option_2_label, self.option_2_edit)

        self.correct_option_combo = QComboBox()
        self.correct_option_combo.addItem("选项 1", "option_1")
        self.correct_option_combo.addItem("选项 2", "option_2")
        self.correct_option_combo.setCurrentIndex(
            self.correct_option_combo.findData(initial.correct_option_id or "option_1")
        )
        form.addRow("正确选项：", self.correct_option_combo)

        self.question_font_combo = QFontComboBox()
        self.question_font_combo.setCurrentFont(QFont(initial.question_font_family))
        self._question_font_family = initial.question_font_family
        self.question_font_combo.currentFontChanged.connect(
            lambda font: setattr(self, "_question_font_family", font.family())
        )
        form.addRow("文字字体：", self.question_font_combo)

        self.question_font_size_spin = QSpinBox()
        self.question_font_size_spin.setRange(12, 120)
        self.question_font_size_spin.setValue(initial.question_font_size_pt)
        self.question_font_size_spin.setSuffix(" pt")
        form.addRow("问题字号：", self.question_font_size_spin)

        self.option_font_size_spin = QSpinBox()
        self.option_font_size_spin.setRange(12, 120)
        self.option_font_size_spin.setValue(initial.option_font_size_pt)
        self.option_font_size_spin.setSuffix(" pt")
        form.addRow("选项字号：", self.option_font_size_spin)

        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(250, 10_000)
        self.dwell_spin.setValue(initial.dwell_time_ms)
        self.dwell_spin.setSingleStep(100)
        self.dwell_spin.setSuffix(" ms")
        form.addRow("停留确认：", self.dwell_spin)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(5, 600)
        self.duration_spin.setValue(initial.duration_seconds)
        self.duration_spin.setSuffix(" 秒")
        form.addRow("任务时长：", self.duration_spin)

        self.neutral_zone_spin = QDoubleSpinBox()
        self.neutral_zone_spin.setRange(0.0, 0.6)
        self.neutral_zone_spin.setSingleStep(0.01)
        self.neutral_zone_spin.setDecimals(2)
        self.neutral_zone_spin.setValue(initial.neutral_zone_width)
        form.addRow("中央中性区：", self.neutral_zone_spin)

        self.randomize_sides_check = QCheckBox("每次呈现时随机交换左右位置")
        self.randomize_sides_check.setChecked(initial.randomize_sides)
        form.addRow("左右随机化：", self.randomize_sides_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addStretch(1)
        root.addWidget(buttons)

        self.common_question_combo.currentIndexChanged.connect(self._load_selected_template)
        self._reload_common_questions()
        self._refresh_option_labels()

    def _current_question_type(
        self,
    ) -> BinaryQuestionType:
        for question_type, button in self.question_type_buttons.items():
            if button.isChecked():
                return question_type

        return BinaryQuestionType.INQUIRY

    def _set_question_type(
        self,
        question_type: BinaryQuestionType,
    ) -> None:
        self.question_type_buttons[question_type].setChecked(True)
        self._refresh_option_labels()

    def _refresh_option_labels(
        self,
        *_args: object,
    ) -> None:
        self.option_1_label.setText("选项1：")
        self.option_2_label.setText("选项2：")
        self.correct_option_combo.setEnabled(self._current_question_type().is_scored)

    def _reload_common_questions(
        self,
        selected_id: str | None = None,
    ) -> None:
        self.common_question_combo.blockSignals(True)
        self.common_question_combo.clear()
        self.common_question_combo.addItem("选择常用问题…", None)

        try:
            templates = self.question_store.load()
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ):
            templates = ()

        self._templates = {template.template_id: template for template in templates}
        selected_index = 0

        for template in templates:
            prefix = "内置" if template.built_in else "自定义"
            self.common_question_combo.addItem(
                (f"[{prefix}·{template.question_type.display_label}] {template.question}"),
                template.template_id,
            )

            if template.template_id == selected_id:
                selected_index = self.common_question_combo.count() - 1

        self.common_question_combo.setCurrentIndex(selected_index)
        self.common_question_combo.blockSignals(False)
        self._refresh_common_question_actions()

    def _refresh_common_question_actions(
        self,
    ) -> None:
        template_id = self.common_question_combo.currentData()
        template = self._templates.get(str(template_id)) if template_id is not None else None
        self.edit_common_button.setEnabled(template is not None)

        if template is not None and template.built_in:
            self.edit_common_button.setText("另存为自定义")
        else:
            self.edit_common_button.setText("保存修改")

    def _load_selected_template(
        self,
        *_args: object,
    ) -> None:
        template_id = self.common_question_combo.currentData()

        if template_id is None:
            self._refresh_common_question_actions()
            return

        template = self._templates.get(str(template_id))

        if template is None:
            self._refresh_common_question_actions()
            return

        self._set_question_type(template.question_type)
        self.question_edit.setText(template.question)
        self.option_1_edit.setText(template.option_1)
        self.option_2_edit.setText(template.option_2)
        self.correct_option_combo.setCurrentIndex(
            self.correct_option_combo.findData(template.correct_option_id or "option_1")
        )
        self._refresh_common_question_actions()

    def _template_from_fields(
        self,
        *,
        template_id: str | None = None,
    ) -> CommonQuestionTemplate:
        question_type = self._current_question_type()
        values = {
            "question_type": question_type,
            "question": self.question_edit.text(),
            "option_1": self.option_1_edit.text(),
            "option_2": self.option_2_edit.text(),
            "correct_option_id": (
                str(self.correct_option_combo.currentData()) if question_type.is_scored else None
            ),
        }

        if template_id is None:
            return CommonQuestionTemplate.create(**values)

        return CommonQuestionTemplate(
            template_id=template_id,
            **values,
        )

    def _edit_common_question(
        self,
        checked: bool = False,
    ) -> None:
        del checked
        template_id = self.common_question_combo.currentData()
        selected = self._templates.get(str(template_id)) if template_id is not None else None

        if selected is None:
            QMessageBox.information(
                self,
                "尚未选择常用问题",
                "请先选择需要修改的常用问题。",
            )
            return

        try:
            if selected.built_in:
                template = self._template_from_fields()
                self.question_store.add(template)
                title = "已另存为自定义问题"
            else:
                template = self._template_from_fields(
                    template_id=selected.template_id,
                )
                self.question_store.update(
                    selected.template_id,
                    template,
                )
                title = "常用问题已更新"
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "无法修改常用问题",
                str(error),
            )
            return

        self._reload_common_questions(template.template_id)
        QMessageBox.information(
            self,
            title,
            template.question,
        )

    def _add_common_question(
        self,
        checked: bool = False,
    ) -> None:
        del checked

        try:
            template = self._template_from_fields()
            self.question_store.add(template)
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "无法保存常用问题",
                str(error),
            )
            return

        self._reload_common_questions(template.template_id)
        QMessageBox.information(
            self,
            "常用问题已保存",
            template.question,
        )

    def build_config(self) -> BinaryQuestionConfig:
        question_type = self._current_question_type()

        return BinaryQuestionConfig(
            question=self.question_edit.text(),
            option_1=self.option_1_edit.text(),
            option_2=self.option_2_edit.text(),
            question_type=question_type,
            correct_option_id=(
                str(self.correct_option_combo.currentData()) if question_type.is_scored else None
            ),
            dwell_time_ms=self.dwell_spin.value(),
            duration_seconds=self.duration_spin.value(),
            question_font_family=self._question_font_family,
            question_font_size_pt=(self.question_font_size_spin.value()),
            option_font_size_pt=self.option_font_size_spin.value(),
            neutral_zone_width=self.neutral_zone_spin.value(),
            randomize_sides=self.randomize_sides_check.isChecked(),
            randomization_seed=self._randomization_seed,
        )
