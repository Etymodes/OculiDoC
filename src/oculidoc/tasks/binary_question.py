"""Horizontal or vertical two-option gaze-question task."""

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
    QListWidget,
    QListWidgetItem,
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

BINARY_LAYOUTS = frozenset({"horizontal", "vertical"})


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
    question_template_ids: tuple[str, ...]

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
        question_template_ids: tuple[str, ...] | list[str] = (),
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

        normalized_template_ids = tuple(str(value).strip() for value in question_template_ids)

        if any(not value for value in normalized_template_ids):
            raise ValueError("question_template_ids cannot contain empty identifiers.")

        if len(set(normalized_template_ids)) != len(normalized_template_ids):
            raise ValueError("question_template_ids cannot contain duplicates.")

        if len(normalized_template_ids) > 50:
            raise ValueError("question_template_ids cannot contain more than 50 questions.")

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
        object.__setattr__(self, "question_template_ids", normalized_template_ids)

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
    """Select one of two randomized horizontal or vertical options by gaze dwell."""

    answered = Signal(str, str)

    def __init__(
        self,
        config: BinaryQuestionConfig,
        *,
        allow_mouse_fallback: bool = True,
        layout: str = "horizontal",
    ) -> None:
        super().__init__()
        normalized_layout = layout.strip().lower()

        if normalized_layout not in BINARY_LAYOUTS:
            raise ValueError("layout must be horizontal or vertical.")

        self.config = config
        self.layout_orientation = normalized_layout
        self.allow_mouse_fallback = allow_mouse_fallback
        self._position_names = (
            ("top", "bottom") if self.layout_orientation == "vertical" else ("left", "right")
        )
        self.randomization_seed = (
            config.randomization_seed
            if config.randomization_seed is not None
            else secrets.randbits(63)
        )
        option_order = ["option_1", "option_2"]

        if config.randomize_sides:
            random.Random(self.randomization_seed).shuffle(option_order)

        self._option_by_side = dict(zip(self._position_names, option_order, strict=True))
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

        button_minimum_height = 260 if self.layout_orientation == "vertical" else 620
        self.setMinimumSize(800, 720 if self.layout_orientation == "vertical" else 520)
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
            QPushButton#answerButton[correct="true"] {
                border-color: #6ee7a2;
                background: #176b36;
                color: white;
            }
            QPushButton#answerButton[incorrect="true"] {
                border-color: #ffb4ad;
                background: #8f231d;
                color: white;
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

        first_position, second_position = self._position_names
        self.left_button = QPushButton(self._answer_for_side(first_position))
        self.right_button = QPushButton(self._answer_for_side(second_position))
        self._button_by_side = {
            first_position: self.left_button,
            second_position: self.right_button,
        }

        option_font = QFont(config.question_font_family)
        option_font.setPointSize(config.option_font_size_pt)
        option_font.setBold(True)

        for button in (
            self.left_button,
            self.right_button,
        ):
            button.setObjectName("answerButton")
            button.setProperty("active", False)
            button.setMinimumHeight(button_minimum_height)
            button.setFont(option_font)
            button.setStyleSheet(f"font-size: {config.option_font_size_pt}pt;")

        self.left_button.clicked.connect(lambda: self._commit(first_position))
        self.right_button.clicked.connect(lambda: self._commit(second_position))

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

        answers = QVBoxLayout() if self.layout_orientation == "vertical" else QHBoxLayout()
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
        return {position: self._answer_for_side(position) for position in self._position_names}

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

    def _layout_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"layout": self.layout_orientation}

        for position in self._position_names:
            payload[f"{position}_option_id"] = self._option_by_side[position]
            payload[f"{position}_answer"] = self._answer_for_side(position)

        return payload

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
            "position": side,
            "layout": self.layout_orientation,
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
                **self._layout_payload(),
                "displayed_correct_side": (self.displayed_correct_side),
                "displayed_correct_position": (self.displayed_correct_side),
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
            "selected_position": (selected_side),
            "selected_answer": (selected_answer),
            "is_scored": (self.config.is_scored),
            "correct_option_id": (self.config.correct_option_id),
            "correct": correct,
            "displayed_correct_side": (self.displayed_correct_side),
            "displayed_correct_position": (self.displayed_correct_side),
            "reaction_time_ms": (reaction_time_ms),
            "confirmation_dwell_ms": (self._confirmation_dwell_ms),
            "configured_dwell_ms": (self.config.dwell_time_ms),
            "selection_method": (self._selection_method),
            "completion_status": (completion_status),
            "completion_reason": (reason_text),
            "randomization_seed": (self.randomization_seed),
            **self._layout_payload(),
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
        gaze_axis: float,
    ) -> str | None:
        half_neutral = self.config.neutral_zone_width / 2.0
        first_position, second_position = self._position_names
        boundary = 0.5

        if self.layout_orientation == "vertical" and self.isVisible():
            first_bounds = self._button_bounds_normalized(
                self._button_by_side[first_position],
                side=first_position,
            )
            second_bounds = self._button_bounds_normalized(
                self._button_by_side[second_position],
                side=second_position,
            )
            boundary = (first_bounds[3] + second_bounds[1]) / 2.0

        if gaze_axis < boundary - half_neutral:
            return first_position

        if gaze_axis > boundary + half_neutral:
            return second_position

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

        if self.isVisible() and right > left and bottom > top:
            return (
                left,
                top,
                right,
                bottom,
            )

        half_neutral = self.config.neutral_zone_width / 2.0

        first_position, _ = self._position_names

        if self.layout_orientation == "vertical":
            if side == first_position:
                return (
                    0.0,
                    0.0,
                    1.0,
                    0.5 - half_neutral,
                )

            return (
                0.0,
                0.5 + half_neutral,
                1.0,
                1.0,
            )

        if side == first_position:
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

        for side, button in self._button_by_side.items():
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
                        "position": side,
                        "layout": self.layout_orientation,
                        "answer": answer,
                        "logical_option_id": option_id,
                        "is_scored": self.config.is_scored,
                    },
                }
            )

        sample_side: str | None = None

        sample_axis = (
            sample.gaze_y_normalized
            if self.layout_orientation == "vertical"
            else sample.gaze_x_normalized
        )

        if sample.gaze_valid and sample_axis is not None:
            sample_side = self._side_for_gaze(
                max(
                    0.0,
                    min(
                        1.0,
                        float(sample_axis),
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
                **self._layout_payload(),
                "correct_side": self.displayed_correct_side,
                "correct_position": self.displayed_correct_side,
                "randomize_sides": self.config.randomize_sides,
                "randomization_seed": self.randomization_seed,
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

        gaze_axis = (
            sample.gaze_y_normalized
            if self.layout_orientation == "vertical"
            else sample.gaze_x_normalized
        )

        if gaze_axis is None:
            self.advance_dwell(
                None,
                elapsed_ms,
                monotonic_timestamp_ns=(timestamp_ns),
                interruption_reason=("invalid_gaze"),
            )
            return

        normalized_axis = max(
            0.0,
            min(
                1.0,
                float(gaze_axis),
            ),
        )
        side = self._side_for_gaze(normalized_axis)
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

        if side is not None and side not in self._position_names:
            positions = ", ".join(self._position_names)
            raise ValueError(f"side must be {positions}, or None.")

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
        first_position, second_position = self._position_names
        left_value = int(self._dwell_ms) if self._active_side == first_position else 0
        right_value = int(self._dwell_ms) if self._active_side == second_position else 0

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
        first_position, second_position = self._position_names
        self.left_button.setProperty(
            "active",
            self._active_side == first_position,
        )
        self.right_button.setProperty(
            "active",
            self._active_side == second_position,
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

    def _refresh_feedback_button(self, button: QPushButton) -> None:
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    def show_correct_feedback(self) -> None:
        """Mark the selected option with a green check while awaiting manual advance."""
        if self._result is None:
            return

        side, answer = self._result
        button = self._button_by_side[side]
        button.setProperty("correct", True)
        button.setText(f"✓\n{answer}")
        self.question_label.setText("回答正确 · 按空格或 Enter 进入下一题")
        self._refresh_feedback_button(button)

    def show_incorrect_feedback(self) -> None:
        """Mark an incorrect option before the same question is retried."""
        if self._result is None:
            return

        side, answer = self._result
        button = self._button_by_side[side]
        button.setProperty("incorrect", True)
        button.setText(f"✕\n{answer}")
        self.question_label.setText("答案不正确 · 即将重新呈现本题")
        self._refresh_feedback_button(button)


class BinaryQuestionSetupDialog(QDialog):
    """Configure a reusable two-option question."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        question_bank_path: str | Path | None = None,
        config: BinaryQuestionConfig | None = None,
        layout: str = "horizontal",
    ) -> None:
        super().__init__(parent)
        normalized_layout = layout.strip().lower()

        if normalized_layout not in BINARY_LAYOUTS:
            raise ValueError("layout must be horizontal or vertical.")

        self.layout_orientation = normalized_layout
        initial = config or BinaryQuestionConfig(question="你现在感到舒服吗？")
        self._randomization_seed = initial.randomization_seed
        self.setWindowTitle(
            "上下二分问答设置" if self.layout_orientation == "vertical" else "左右二分问答设置"
        )
        self.resize(680, 680)

        if question_bank_path is None:
            question_bank_path = Path.home() / ".oculidoc" / "data" / "common_questions.json"

        self.question_store = CommonQuestionStore(question_bank_path)
        self._templates: dict[str, CommonQuestionTemplate] = {}
        self._initial_sequence_ids = set(initial.question_template_ids)
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

        self.sequence_question_list = QListWidget()
        self.sequence_question_list.setObjectName("binarySequenceQuestionList")
        self.sequence_question_list.setMinimumHeight(150)
        form.addRow("连续题目（可勾选多题）：", self.sequence_question_list)

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

        position_label = "上下" if self.layout_orientation == "vertical" else "左右"
        self.randomize_sides_check = QCheckBox(f"每次呈现时随机交换{position_label}位置")
        self.randomize_sides_check.setChecked(initial.randomize_sides)
        form.addRow(f"{position_label}随机化：", self.randomize_sides_check)

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
        checked_ids = self.selected_question_template_ids()

        if not checked_ids and self.sequence_question_list.count() == 0:
            checked_ids = tuple(self._initial_sequence_ids)

        self.common_question_combo.blockSignals(True)
        self.common_question_combo.clear()
        self.common_question_combo.addItem("选择常用问题…", None)
        self.sequence_question_list.clear()

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

            sequence_item = QListWidgetItem(
                f"[{prefix}] {template.question}",
                self.sequence_question_list,
            )
            sequence_item.setData(Qt.ItemDataRole.UserRole, template.template_id)
            sequence_item.setFlags(sequence_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            sequence_item.setCheckState(
                Qt.CheckState.Checked
                if template.template_id in checked_ids
                else Qt.CheckState.Unchecked
            )

        self.common_question_combo.setCurrentIndex(selected_index)
        self.common_question_combo.blockSignals(False)
        self._refresh_common_question_actions()

    def selected_question_template_ids(self) -> tuple[str, ...]:
        """Return checked common-question identifiers in visible order."""
        selected: list[str] = []

        for index in range(self.sequence_question_list.count()):
            item = self.sequence_question_list.item(index)

            if item.checkState() == Qt.CheckState.Checked:
                selected.append(str(item.data(Qt.ItemDataRole.UserRole)))

        return tuple(selected)

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
            question_template_ids=self.selected_question_template_ids(),
        )


def binary_question_sequence(
    config: BinaryQuestionConfig,
    store: CommonQuestionStore,
) -> tuple[tuple[str, BinaryQuestionConfig], ...]:
    """Resolve checked common questions while preserving display and dwell settings."""
    if not config.question_template_ids:
        return (("binary-question-1", config),)

    templates = {template.template_id: template for template in store.load()}
    resolved: list[tuple[str, BinaryQuestionConfig]] = []

    for template_id in config.question_template_ids:
        template = templates.get(template_id)

        if template is None:
            raise ValueError(f"连续题目已不存在：{template_id}")

        resolved.append(
            (
                template.template_id,
                BinaryQuestionConfig(
                    question=template.question,
                    option_1=template.option_1,
                    option_2=template.option_2,
                    question_type=template.question_type,
                    correct_option_id=template.correct_option_id,
                    dwell_time_ms=config.dwell_time_ms,
                    duration_seconds=config.duration_seconds,
                    question_font_family=config.question_font_family,
                    question_font_size_pt=config.question_font_size_pt,
                    option_font_size_pt=config.option_font_size_pt,
                    neutral_zone_width=config.neutral_zone_width,
                    randomize_sides=config.randomize_sides,
                    randomization_seed=config.randomization_seed,
                ),
            )
        )

    return tuple(resolved)
