"""Randomized large-picture gaze questions backed by the shared image library."""

from __future__ import annotations

import random
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.image_library import (
    ImageAsset,
    ImageLibraryDialog,
    ImageLibraryStore,
    asset_preview_pixmap,
)
from oculidoc.tasks.binary_question import BinaryQuestionConfig, BinaryQuestionTask
from oculidoc.tasks.question_bank import BinaryQuestionType


@dataclass(frozen=True, slots=True)
class ImageQuestion:
    question_id: str
    prompt: str
    option_1_image_id: str
    option_2_image_id: str
    correct_option_id: str
    position_seed: int


@dataclass(frozen=True, slots=True)
class ImageChoiceConfig:
    """Select an eligible picture pool; individual questions are made at run time."""

    category_filters: tuple[str, ...] = ()
    style_filters: tuple[str, ...] = ()
    question_count: int = 6
    dwell_time_ms: int = 1_200
    duration_seconds: int = 30
    question_font_size_pt: int = 48
    randomize_sides: bool = True
    randomization_seed: int | None = None
    question_ids: tuple[str, ...] = ()  # M3D12D compatibility; fixed pairings are ignored.

    def __post_init__(self) -> None:
        categories = tuple(str(value).strip() for value in self.category_filters)
        styles = tuple(str(value).strip() for value in self.style_filters)
        legacy_ids = tuple(str(value).strip() for value in self.question_ids)

        for name, values in (
            ("category_filters", categories),
            ("style_filters", styles),
            ("question_ids", legacy_ids),
        ):
            if any(not value for value in values):
                raise ValueError(f"{name} cannot contain empty values.")

            if len(set(values)) != len(values):
                raise ValueError(f"{name} cannot contain duplicates.")

        if not 1 <= self.question_count <= 100:
            raise ValueError("question_count must be between 1 and 100.")

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

        object.__setattr__(self, "category_filters", categories)
        object.__setattr__(self, "style_filters", styles)
        object.__setattr__(self, "question_ids", legacy_ids)


def eligible_image_assets(
    config: ImageChoiceConfig,
    store: ImageLibraryStore,
) -> tuple[ImageAsset, ...]:
    """Return assets matching any selected category and any selected style."""
    categories = set(config.category_filters)
    styles = set(config.style_filters)
    eligible = tuple(
        asset
        for asset in store.load()
        if (not categories or asset.category in categories)
        and (not styles or asset.style in styles)
    )

    if len(eligible) < 2 or len({asset.label.casefold() for asset in eligible}) < 2:
        raise ValueError("所选类别与风格至少需要两张名称不同的图片。")

    if config.question_count > len(eligible):
        raise ValueError(f"当前筛选只有 {len(eligible)} 张图片，题数不能超过可用图片数。")

    return eligible


def image_question_sequence(
    config: ImageChoiceConfig,
    store: ImageLibraryStore,
) -> tuple[ImageQuestion, ...]:
    """Randomly choose targets, distractors, logical answers, order, and screen seeds."""
    pool = list(eligible_image_assets(config, store))
    seed = (
        config.randomization_seed if config.randomization_seed is not None else secrets.randbits(63)
    )
    rng = random.Random(seed)
    rng.shuffle(pool)
    targets = pool[: config.question_count]
    questions: list[ImageQuestion] = []

    for index, target in enumerate(targets):
        distractors = [asset for asset in pool if asset.label.casefold() != target.label.casefold()]
        distractor = rng.choice(distractors)
        target_first = bool(rng.getrandbits(1))
        questions.append(
            ImageQuestion(
                question_id=f"image-{index + 1}-{target.image_id}",
                prompt=f"请看{target.label}",
                option_1_image_id=(target.image_id if target_first else distractor.image_id),
                option_2_image_id=(distractor.image_id if target_first else target.image_id),
                correct_option_id=("option_1" if target_first else "option_2"),
                position_seed=rng.getrandbits(63),
            )
        )

    return tuple(questions)


def render_image_card(
    asset: ImageAsset,
    store: ImageLibraryStore,
    *,
    size: int = 900,
    feedback: str | None = None,
) -> QPixmap:
    """Render a large image-only card; the asset name is deliberately never drawn."""
    pixmap = asset_preview_pixmap(asset, store, size=size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#6f94ad"), max(5, size // 100)))
    painter.drawRoundedRect(QRect(6, 6, size - 12, size - 12), size // 24, size // 24)

    if feedback is not None:
        correct = feedback == "correct"
        color = QColor("#178447" if correct else "#b42318")
        painter.setBrush(color)
        painter.setPen(QPen(QColor("white"), max(3, size // 120)))
        badge_size = int(size * 0.20)
        badge_rect = QRect(size - badge_size - 20, 20, badge_size, badge_size)
        painter.drawEllipse(badge_rect)
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setFamily("Arial")
        font.setPointSize(max(36, int(badge_size * 0.46)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "✓" if correct else "×")

    painter.end()
    return pixmap


class ImageChoiceTask(BinaryQuestionTask):
    """Show one randomized picture question using the shared dwell engine."""

    def __init__(
        self,
        question: ImageQuestion,
        config: ImageChoiceConfig,
        store: ImageLibraryStore,
        *,
        assets: Mapping[str, ImageAsset] | None = None,
        allow_mouse_fallback: bool = True,
    ) -> None:
        asset_map = dict(assets or {asset.image_id: asset for asset in store.load()})
        first_asset = asset_map[question.option_1_image_id]
        second_asset = asset_map[question.option_2_image_id]
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
            randomization_seed=question.position_seed,
        )
        super().__init__(
            binary_config,
            allow_mouse_fallback=allow_mouse_fallback,
            layout="horizontal",
        )
        self.image_question = question
        self.image_config = config
        self.image_store = store
        self._assets = asset_map
        self._image_id_by_option = {
            "option_1": question.option_1_image_id,
            "option_2": question.option_2_image_id,
        }
        self._icons: dict[str, QIcon] = {}
        self._apply_image_icons()

    def _layout_payload(self) -> dict[str, object]:
        payload = super()._layout_payload()

        for option_id, image_id in self._image_id_by_option.items():
            asset = self._assets[image_id]
            payload[f"{option_id}_image_id"] = image_id
            payload[f"{option_id}_image_category"] = asset.category
            payload[f"{option_id}_image_style"] = asset.style

        correct_option = self.config.correct_option_id
        payload["correct_image_id"] = (
            self._image_id_by_option[correct_option] if correct_option is not None else None
        )
        return payload

    def _render_icon(self, option_id: str, feedback: str | None = None) -> QIcon:
        image_id = self._image_id_by_option[option_id]
        return QIcon(
            render_image_card(
                self._assets[image_id],
                self.image_store,
                feedback=feedback,
            )
        )

    def _refresh_icon_sizes(self) -> None:
        for button in self._button_by_side.values():
            available = min(button.width() - 28, button.height() - 28)
            size = max(360, min(760, available if available > 0 else 620))
            button.setIconSize(QSize(size, size))

    def _apply_image_icons(self) -> None:
        for side, button in self._button_by_side.items():
            option_id = self._option_by_side[side]
            icon = self._render_icon(option_id)
            self._icons[option_id] = icon
            button.setText("")
            button.setIcon(icon)

        self._refresh_icon_sizes()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_icon_sizes()

    def _apply_feedback_icon(self, feedback: str) -> None:
        if self.result is None:
            return

        side, _answer = self.result
        option_id = self._option_by_side[side]
        button = self._button_by_side[side]
        button.setIcon(self._render_icon(option_id, feedback))
        button.setText("")
        self._refresh_icon_sizes()

    def show_correct_feedback(self) -> None:
        super().show_correct_feedback()
        self._apply_feedback_icon("correct")

    def show_incorrect_feedback(self) -> None:
        super().show_incorrect_feedback()
        self._apply_feedback_icon("incorrect")


class ImageChoiceSetupDialog(QDialog):
    """Choose category/style pools and generate fresh pairings for every run."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        config: ImageChoiceConfig | None = None,
        image_library_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        initial = config or ImageChoiceConfig()
        self._randomization_seed = initial.randomization_seed
        self.image_store = ImageLibraryStore(
            image_library_path or (Path.home() / ".oculidoc" / "data" / "image_library")
        )
        self.setWindowTitle("图片选择任务设置")
        self.resize(760, 700)

        explanation = QLabel(
            "每次运行都会从所选类别与风格中随机抽取正确图片、干扰图片和呈现位置；"
            "患者屏幕只显示大图，不显示图片名称。"
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color:#365269; background:#eef7ff; padding:8px;")

        form = QFormLayout()
        self.category_list = QListWidget()
        self.category_list.setMinimumHeight(150)
        self.style_list = QListWidget()
        self.style_list.setMinimumHeight(120)
        filters = QHBoxLayout()
        filters.addWidget(self.category_list, 1)
        filters.addWidget(self.style_list, 1)
        form.addRow("类别 / 风格（可多选）：", filters)

        manage_button = QPushButton("打开图片库：上传、修改或删除图片…")
        manage_button.clicked.connect(self._open_image_library)
        form.addRow("图片库：", manage_button)

        self.question_count_spin = QSpinBox()
        self.question_count_spin.setRange(1, 100)
        self.question_count_spin.setValue(initial.question_count)
        self.question_count_spin.setSuffix(" 题")
        form.addRow("本次随机题数：", self.question_count_spin)

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

        self.randomize_sides_check = QCheckBox("每题随机交换左右图片位置")
        self.randomize_sides_check.setChecked(initial.randomize_sides)
        form.addRow("位置随机化：", self.randomize_sides_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(explanation)
        root.addLayout(form)
        root.addStretch(1)
        root.addWidget(buttons)
        self._reload_filters(
            selected_categories=set(initial.category_filters),
            selected_styles=set(initial.style_filters),
        )

    @staticmethod
    def _checked_values(widget: QListWidget) -> tuple[str, ...]:
        values: list[str] = []

        for index in range(widget.count()):
            item = widget.item(index)

            if item.checkState() == Qt.CheckState.Checked:
                values.append(str(item.data(Qt.ItemDataRole.UserRole)))

        return tuple(values)

    @staticmethod
    def _fill_filter_list(
        widget: QListWidget,
        values: list[str],
        selected: set[str],
    ) -> None:
        widget.clear()
        select_all = not selected

        for value in values:
            item = QListWidgetItem(value, widget)
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if select_all or value in selected
                else Qt.CheckState.Unchecked
            )

    def _reload_filters(
        self,
        *,
        selected_categories: set[str] | None = None,
        selected_styles: set[str] | None = None,
    ) -> None:
        if selected_categories is None:
            selected_categories = set(self._checked_values(self.category_list))

        if selected_styles is None:
            selected_styles = set(self._checked_values(self.style_list))

        assets = self.image_store.load()
        categories = sorted({asset.category for asset in assets})
        styles = sorted({asset.style for asset in assets})
        self._fill_filter_list(self.category_list, categories, selected_categories)
        self._fill_filter_list(self.style_list, styles, selected_styles)
        self.question_count_spin.setMaximum(max(1, len(assets)))

    def _open_image_library(self) -> None:
        ImageLibraryDialog(self.image_store, self).exec()
        self._reload_filters()

    def build_config(self) -> ImageChoiceConfig:
        return ImageChoiceConfig(
            category_filters=self._checked_values(self.category_list),
            style_filters=self._checked_values(self.style_list),
            question_count=self.question_count_spin.value(),
            dwell_time_ms=self.dwell_spin.value(),
            duration_seconds=self.duration_spin.value(),
            question_font_size_pt=self.question_font_size_spin.value(),
            randomize_sides=self.randomize_sides_check.isChecked(),
            randomization_seed=self._randomization_seed,
        )

    def _accept_if_valid(self) -> None:
        try:
            eligible_image_assets(self.build_config(), self.image_store)
        except (OSError, KeyError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "图片题设置无效", str(error))
            return

        self.accept()
