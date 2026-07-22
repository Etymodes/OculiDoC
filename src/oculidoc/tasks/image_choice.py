"""Built-in uniform picture library and two-picture gaze questions."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.tasks.binary_question import BinaryQuestionConfig, BinaryQuestionTask
from oculidoc.tasks.question_bank import BinaryQuestionType


@dataclass(frozen=True, slots=True)
class ImageAsset:
    image_id: str
    label: str
    symbol: str
    background: str


@dataclass(frozen=True, slots=True)
class ImageQuestion:
    question_id: str
    prompt: str
    option_1_image_id: str
    option_2_image_id: str
    correct_option_id: str


IMAGE_LIBRARY: tuple[ImageAsset, ...] = (
    ImageAsset("banana", "香蕉", "🍌", "#fff4b8"),
    ImageAsset("lion", "狮子", "🦁", "#ffe2bd"),
    ImageAsset("apple", "苹果", "🍎", "#ffe0e0"),
    ImageAsset("dog", "小狗", "🐶", "#e8ddcf"),
    ImageAsset("cup", "水杯", "🥤", "#dff3ff"),
    ImageAsset("bed", "床", "🛏", "#e8e4ff"),
    ImageAsset("sun", "太阳", "☀", "#fff0a8"),
    ImageAsset("moon", "月亮", "🌙", "#dfe7ff"),
    ImageAsset("car", "汽车", "🚗", "#dff0ff"),
    ImageAsset("flower", "花", "🌼", "#fff0f6"),
    ImageAsset("cat", "小猫", "🐱", "#fff0d6"),
    ImageAsset("shoe", "鞋", "👟", "#e4f3ef"),
)

IMAGE_QUESTIONS: tuple[ImageQuestion, ...] = (
    ImageQuestion("image-banana", "请看香蕉", "banana", "lion", "option_1"),
    ImageQuestion("image-apple", "请看苹果", "dog", "apple", "option_2"),
    ImageQuestion("image-cup", "请看水杯", "cup", "bed", "option_1"),
    ImageQuestion("image-sun", "请看太阳", "moon", "sun", "option_2"),
    ImageQuestion("image-car", "请看汽车", "car", "flower", "option_1"),
    ImageQuestion("image-cat", "请看小猫", "shoe", "cat", "option_2"),
)

_ASSET_BY_ID = {asset.image_id: asset for asset in IMAGE_LIBRARY}
_QUESTION_BY_ID = {question.question_id: question for question in IMAGE_QUESTIONS}


@dataclass(frozen=True, slots=True)
class ImageChoiceConfig:
    question_ids: tuple[str, ...] = ("image-banana", "image-apple")
    dwell_time_ms: int = 1_200
    duration_seconds: int = 30
    question_font_size_pt: int = 48
    randomize_sides: bool = True
    randomization_seed: int | None = None

    def __post_init__(self) -> None:
        normalized = tuple(str(value).strip() for value in self.question_ids)

        if not normalized:
            raise ValueError("question_ids must contain at least one image question.")

        if any(value not in _QUESTION_BY_ID for value in normalized):
            raise ValueError("question_ids contains an unknown image question.")

        if len(set(normalized)) != len(normalized):
            raise ValueError("question_ids cannot contain duplicates.")

        if not 250 <= self.dwell_time_ms <= 10_000:
            raise ValueError("dwell_time_ms must be between 250 and 10000.")

        if not 5 <= self.duration_seconds <= 600:
            raise ValueError("duration_seconds must be between 5 and 600.")

        if not 20 <= self.question_font_size_pt <= 120:
            raise ValueError("question_font_size_pt must be between 20 and 120.")

        if not isinstance(self.randomize_sides, bool):
            raise TypeError("randomize_sides must be a boolean.")

        if self.randomization_seed is not None and (
            not isinstance(self.randomization_seed, int)
            or isinstance(self.randomization_seed, bool)
            or self.randomization_seed < 0
        ):
            raise TypeError("randomization_seed must be a non-negative integer or null.")

        object.__setattr__(self, "question_ids", normalized)


def image_question_sequence(config: ImageChoiceConfig) -> tuple[ImageQuestion, ...]:
    return tuple(_QUESTION_BY_ID[question_id] for question_id in config.question_ids)


def render_image_card(
    image_id: str,
    *,
    size: int = 420,
    feedback: str | None = None,
) -> QPixmap:
    """Render one equal-size built-in card without external image files."""
    asset = _ASSET_BY_ID[image_id]
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(asset.background))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor("#6f94ad"), max(4, size // 70)))
    painter.drawRoundedRect(
        QRect(8, 8, size - 16, size - 16),
        size // 18,
        size // 18,
    )

    symbol_font = QFont("Segoe UI Emoji", max(80, int(size * 0.46)))
    painter.setFont(symbol_font)
    painter.setPen(QColor("#17324d"))
    painter.drawText(
        QRect(20, 20, size - 40, int(size * 0.68)),
        Qt.AlignmentFlag.AlignCenter,
        asset.symbol,
    )

    label_font = QFont("Microsoft YaHei UI", max(24, int(size * 0.10)))
    label_font.setBold(True)
    painter.setFont(label_font)
    painter.drawText(
        QRect(20, int(size * 0.70), size - 40, int(size * 0.22)),
        Qt.AlignmentFlag.AlignCenter,
        asset.label,
    )

    if feedback is not None:
        correct = feedback == "correct"
        color = QColor("#178447" if correct else "#b42318")
        painter.setBrush(color)
        painter.setPen(QPen(QColor("white"), max(3, size // 90)))
        badge_size = int(size * 0.24)
        badge_rect = QRect(size - badge_size - 16, 16, badge_size, badge_size)
        painter.drawEllipse(badge_rect)
        feedback_font = QFont("Arial", int(badge_size * 0.64))
        feedback_font.setBold(True)
        painter.setFont(feedback_font)
        painter.setPen(QColor("white"))
        painter.drawText(
            badge_rect,
            Qt.AlignmentFlag.AlignCenter,
            "✓" if correct else "×",
        )

    painter.end()
    return pixmap


class ImageChoiceTask(BinaryQuestionTask):
    """Show one built-in picture question using the binary dwell engine."""

    def __init__(
        self,
        question: ImageQuestion,
        config: ImageChoiceConfig,
        *,
        allow_mouse_fallback: bool = True,
    ) -> None:
        first_asset = _ASSET_BY_ID[question.option_1_image_id]
        second_asset = _ASSET_BY_ID[question.option_2_image_id]
        binary_config = BinaryQuestionConfig(
            question=question.prompt,
            option_1=first_asset.label,
            option_2=second_asset.label,
            question_type=BinaryQuestionType.QUESTION_ANSWER,
            correct_option_id=question.correct_option_id,
            dwell_time_ms=config.dwell_time_ms,
            duration_seconds=config.duration_seconds,
            question_font_size_pt=config.question_font_size_pt,
            option_font_size_pt=26,
            randomize_sides=config.randomize_sides,
            randomization_seed=config.randomization_seed,
        )
        super().__init__(
            binary_config,
            allow_mouse_fallback=allow_mouse_fallback,
            layout="horizontal",
        )
        self.image_question = question
        self.image_config = config
        self._image_id_by_option = {
            "option_1": question.option_1_image_id,
            "option_2": question.option_2_image_id,
        }
        self._apply_image_icons()

    def _apply_image_icons(self) -> None:
        icon_size = QSize(420, 420)

        for side, button in self._button_by_side.items():
            option_id = self._option_by_side[side]
            button.setText("")
            button.setIcon(QIcon(render_image_card(self._image_id_by_option[option_id])))
            button.setIconSize(icon_size)

    def _apply_feedback_icon(self, feedback: str) -> None:
        if self.result is None:
            return

        side, _answer = self.result
        option_id = self._option_by_side[side]
        button = self._button_by_side[side]
        button.setIcon(
            QIcon(
                render_image_card(
                    self._image_id_by_option[option_id],
                    feedback=feedback,
                )
            )
        )
        button.setText("")

    def show_correct_feedback(self) -> None:
        super().show_correct_feedback()
        self._apply_feedback_icon("correct")

    def show_incorrect_feedback(self) -> None:
        super().show_incorrect_feedback()
        self._apply_feedback_icon("incorrect")


class ImageChoiceSetupDialog(QDialog):
    """Choose several built-in picture questions and shared dwell settings."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        config: ImageChoiceConfig | None = None,
    ) -> None:
        super().__init__(parent)
        initial = config or ImageChoiceConfig()
        self._randomization_seed = initial.randomization_seed
        self.setWindowTitle("图片选择任务设置")
        self.resize(650, 620)

        form = QFormLayout()
        self.question_list = QListWidget()
        self.question_list.setMinimumHeight(280)

        for question in IMAGE_QUESTIONS:
            option_1 = _ASSET_BY_ID[question.option_1_image_id].label
            option_2 = _ASSET_BY_ID[question.option_2_image_id].label
            item = QListWidgetItem(
                f"{question.prompt}（{option_1} / {option_2}）",
                self.question_list,
            )
            item.setData(Qt.ItemDataRole.UserRole, question.question_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if question.question_id in initial.question_ids
                else Qt.CheckState.Unchecked
            )

        form.addRow("连续图片题：", self.question_list)

        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(250, 10_000)
        self.dwell_spin.setSingleStep(100)
        self.dwell_spin.setSuffix(" ms")
        self.dwell_spin.setValue(initial.dwell_time_ms)
        form.addRow("停留确认：", self.dwell_spin)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(5, 600)
        self.duration_spin.setSuffix(" 秒/题")
        self.duration_spin.setValue(initial.duration_seconds)
        form.addRow("每题最长时长：", self.duration_spin)

        self.question_font_size_spin = QSpinBox()
        self.question_font_size_spin.setRange(20, 120)
        self.question_font_size_spin.setSuffix(" pt")
        self.question_font_size_spin.setValue(initial.question_font_size_pt)
        form.addRow("问题字号：", self.question_font_size_spin)

        self.randomize_sides_check = QCheckBox("每题随机交换左右图片")
        self.randomize_sides_check.setChecked(initial.randomize_sides)
        form.addRow("位置随机化：", self.randomize_sides_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

    def selected_question_ids(self) -> tuple[str, ...]:
        selected: list[str] = []

        for index in range(self.question_list.count()):
            item = self.question_list.item(index)

            if item.checkState() == Qt.CheckState.Checked:
                selected.append(str(item.data(Qt.ItemDataRole.UserRole)))

        return tuple(selected)

    def build_config(self) -> ImageChoiceConfig:
        return ImageChoiceConfig(
            question_ids=self.selected_question_ids(),
            dwell_time_ms=self.dwell_spin.value(),
            duration_seconds=self.duration_spin.value(),
            question_font_size_pt=self.question_font_size_spin.value(),
            randomize_sides=self.randomize_sides_check.isChecked(),
            randomization_seed=self._randomization_seed,
        )
