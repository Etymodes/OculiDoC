"""Horizontal binary gaze-question task."""

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.devices.contracts import (
    EyeTrackerSample,
)


@dataclass(frozen=True, slots=True)
class BinaryQuestionConfig:
    question: str
    left_answer: str
    right_answer: str
    dwell_time_ms: int = 1_200
    neutral_zone_width: float = 0.12

    def __post_init__(self) -> None:
        for field_name in (
            "question",
            "left_answer",
            "right_answer",
        ):
            normalized = getattr(
                self,
                field_name,
            ).strip()

            if not normalized:
                raise ValueError(f"{field_name} cannot be empty.")

            object.__setattr__(
                self,
                field_name,
                normalized,
            )

        if not 250 <= self.dwell_time_ms <= 10_000:
            raise ValueError("dwell_time_ms must be between 250 and 10000.")

        if not 0.0 <= self.neutral_zone_width <= 0.6:
            raise ValueError("neutral_zone_width must be between 0 and 0.6.")


class BinaryQuestionTask(QWidget):
    """Select left or right by gaze dwell or click."""

    answered = Signal(str, str)

    def __init__(
        self,
        config: BinaryQuestionConfig,
    ) -> None:
        super().__init__()
        self.config = config
        self._active_side: str | None = None
        self._dwell_ms = 0.0
        self._last_timestamp_ns: int | None = None
        self._result: tuple[str, str] | None = None

        self.setMinimumSize(800, 520)
        self.setStyleSheet(
            """
            QWidget {
                background: #071521;
                color: white;
                font-family: "Microsoft YaHei UI";
            }
            QLabel#questionLabel {
                font-size: 38px;
                font-weight: 700;
                padding: 24px;
            }
            QPushButton#answerButton {
                min-height: 260px;
                border: 5px solid #d9e7f2;
                border-radius: 24px;
                background: #173957;
                color: white;
                font-size: 48px;
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

        self.left_button = QPushButton(config.left_answer)
        self.right_button = QPushButton(config.right_answer)

        for button in (
            self.left_button,
            self.right_button,
        ):
            button.setObjectName("answerButton")
            button.setProperty("active", False)

        self.left_button.clicked.connect(lambda: self._commit("left"))
        self.right_button.clicked.connect(lambda: self._commit("right"))

        self.left_progress = QProgressBar()
        self.right_progress = QProgressBar()

        for progress in (
            self.left_progress,
            self.right_progress,
        ):
            progress.setRange(
                0,
                config.dwell_time_ms,
            )
            progress.setValue(0)

        left_layout = QVBoxLayout()
        left_layout.addWidget(
            self.left_button,
            1,
        )
        left_layout.addWidget(self.left_progress)

        right_layout = QVBoxLayout()
        right_layout.addWidget(
            self.right_button,
            1,
        )
        right_layout.addWidget(self.right_progress)

        answers = QHBoxLayout()
        answers.setSpacing(34)
        answers.addLayout(left_layout, 1)
        answers.addLayout(right_layout, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(42, 38, 42, 42)
        root.setSpacing(30)
        root.addWidget(self.question_label)
        root.addLayout(answers, 1)

    @property
    def result(
        self,
    ) -> tuple[str, str] | None:
        return self._result

    def start(self) -> None:
        self.reset()

    def stop(self) -> None:
        return None

    def reset(self) -> None:
        self._active_side = None
        self._dwell_ms = 0.0
        self._last_timestamp_ns = None
        self._result = None

        self.left_button.setEnabled(True)
        self.right_button.setEnabled(True)
        self.left_progress.setValue(0)
        self.right_progress.setValue(0)
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
            )
            return

        gaze_x = max(
            0.0,
            min(
                1.0,
                float(sample.gaze_x_normalized),
            ),
        )

        self.advance_dwell(
            self._side_for_gaze(gaze_x),
            elapsed_ms,
        )

    def advance_dwell(
        self,
        side: str | None,
        elapsed_ms: float,
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

        if side is None:
            self._active_side = None
            self._dwell_ms = 0.0
            self._refresh_progress()
            self._refresh_active_side()
            return

        if side != self._active_side:
            self._active_side = side
            self._dwell_ms = 0.0

        self._dwell_ms += elapsed_ms
        self._refresh_progress()
        self._refresh_active_side()

        if self._dwell_ms >= self.config.dwell_time_ms:
            self._commit(side)

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
    ) -> None:
        if self._result is not None:
            return

        answer = self.config.left_answer if side == "left" else self.config.right_answer
        self._result = (
            side,
            answer,
        )

        self.left_button.setEnabled(False)
        self.right_button.setEnabled(False)
        self.question_label.setText(f"已选择：{answer}")
        self.answered.emit(
            side,
            answer,
        )


class BinaryQuestionSetupDialog(QDialog):
    """Configure a horizontal binary question."""

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("左右二分问答设置")
        self.resize(520, 280)

        form = QFormLayout()

        self.question_edit = QLineEdit("你现在感到舒服吗？")
        self.left_edit = QLineEdit("是")
        self.right_edit = QLineEdit("否")

        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(
            250,
            10_000,
        )
        self.dwell_spin.setValue(1_200)
        self.dwell_spin.setSingleStep(100)
        self.dwell_spin.setSuffix(" ms")

        form.addRow(
            "问题：",
            self.question_edit,
        )
        form.addRow(
            "左侧答案：",
            self.left_edit,
        )
        form.addRow(
            "右侧答案：",
            self.right_edit,
        )
        form.addRow(
            "停留确认：",
            self.dwell_spin,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addStretch(1)
        root.addWidget(buttons)

    def build_config(self) -> BinaryQuestionConfig:
        return BinaryQuestionConfig(
            question=self.question_edit.text(),
            left_answer=self.left_edit.text(),
            right_answer=self.right_edit.text(),
            dwell_time_ms=self.dwell_spin.value(),
        )
