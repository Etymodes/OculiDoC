"""Top-level configuration shared by the two M3D13 gaze-game modes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from oculidoc.image_library import ImageLibraryDialog, ImageLibraryStore
from oculidoc.tasks.gaze_contingency import GazeContingencyConfig
from oculidoc.tasks.starlight_route import StarlightRouteConfig
from oculidoc.tasks.visual_hunt import (
    VisualHuntConfig,
    eligible_visual_hunt_assets,
)


class GazeGameMode(StrEnum):
    GARDEN = "garden"
    TREASURE_HUNT = "treasure_hunt"
    STARLIGHT_ROUTE = "starlight_route"


@dataclass(frozen=True, slots=True)
class GazeGameConfig:
    """Keep both mode settings under one stable top-level module ID."""

    default_mode: GazeGameMode = GazeGameMode.GARDEN
    garden: GazeContingencyConfig = field(default_factory=GazeContingencyConfig)
    treasure_hunt: VisualHuntConfig = field(default_factory=VisualHuntConfig)
    starlight_route: StarlightRouteConfig = field(default_factory=StarlightRouteConfig)

    def __post_init__(self) -> None:
        object.__setattr__(self, "default_mode", GazeGameMode(self.default_mode))
        if isinstance(self.garden, dict):
            object.__setattr__(
                self,
                "garden",
                GazeContingencyConfig(**self.garden),
            )
        if isinstance(self.treasure_hunt, dict):
            object.__setattr__(
                self,
                "treasure_hunt",
                VisualHuntConfig(**self.treasure_hunt),
            )
        if isinstance(self.starlight_route, dict):
            object.__setattr__(
                self,
                "starlight_route",
                StarlightRouteConfig(**self.starlight_route),
            )
        if not isinstance(self.garden, GazeContingencyConfig):
            raise TypeError("garden must be a GazeContingencyConfig.")
        if not isinstance(self.treasure_hunt, VisualHuntConfig):
            raise TypeError("treasure_hunt must be a VisualHuntConfig.")
        if not isinstance(self.starlight_route, StarlightRouteConfig):
            raise TypeError("starlight_route must be a StarlightRouteConfig.")


class GazeGameSetupDialog(QDialog):
    """Choose one game mode, configure it, and return without nested sessions."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        config: GazeGameConfig | None = None,
        image_library_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial = config or GazeGameConfig()
        self._selected_mode = self._initial.default_mode
        self.image_store = ImageLibraryStore(
            image_library_path or (Path.home() / ".oculidoc" / "data" / "image_library")
        )
        self.setWindowTitle("眼动游戏")
        self.resize(760, 760)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_mode_page())
        self.pages.addWidget(self._build_garden_page())
        self.pages.addWidget(self._build_hunt_page())
        self.pages.addWidget(self._build_starlight_page())

        root = QVBoxLayout(self)
        root.addWidget(self.pages)

    @property
    def selected_mode(self) -> GazeGameMode:
        return self._selected_mode

    def _build_mode_page(self) -> QWidget:
        page = QWidget()
        title = QLabel("请选择本次眼动游戏")
        title.setStyleSheet("font-size:24px; font-weight:700;")
        explanation = QLabel(
            "三个模式共享一个正式入口和一套会话记录。进入设置后可返回这里重新选择。"
        )
        explanation.setWordWrap(True)

        garden_button = QPushButton("点亮花园\n持续注视花朵触发柔和反馈")
        garden_button.setObjectName("gazeGameGardenButton")
        garden_button.setMinimumHeight(130)
        garden_button.clicked.connect(lambda: self.pages.setCurrentIndex(1))

        hunt_button = QPushButton("视觉寻宝\n在图片阵列中寻找预览目标")
        hunt_button.setObjectName("gazeGameTreasureHuntButton")
        hunt_button.setMinimumHeight(130)
        hunt_button.clicked.connect(lambda: self.pages.setCurrentIndex(2))

        starlight_button = QPushButton(
            "星光航线\n收集呼吸闪烁的星星，自适应学习可视区域"
        )
        starlight_button.setObjectName("gazeGameStarlightRouteButton")
        starlight_button.setMinimumHeight(130)
        starlight_button.clicked.connect(lambda: self.pages.setCurrentIndex(3))

        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(page)
        layout.addWidget(title)
        layout.addWidget(explanation)
        layout.addSpacing(16)
        layout.addWidget(garden_button)
        layout.addWidget(hunt_button)
        layout.addWidget(starlight_button)
        layout.addStretch(1)
        layout.addWidget(cancel_button)
        return page

    @staticmethod
    def _spin(
        minimum: int,
        maximum: int,
        value: int,
        *,
        suffix: str = "",
        step: int = 1,
    ) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.setSingleStep(step)
        widget.setSuffix(suffix)
        return widget

    def _page_buttons(self, mode: GazeGameMode) -> QHBoxLayout:
        row = QHBoxLayout()
        back = QPushButton("返回模式选择")
        start = QPushButton("保存并开始")
        cancel = QPushButton("取消")
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        start.clicked.connect(lambda: self._accept_mode(mode))
        cancel.clicked.connect(self.reject)
        row.addWidget(back)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(start)
        return row

    def _build_garden_page(self) -> QWidget:
        initial = self._initial.garden
        page = QWidget()
        form = QFormLayout()
        self.garden_object_count = self._spin(2, 6, initial.object_count)
        self.garden_object_diameter = self._spin(
            160,
            480,
            initial.object_diameter_px,
            suffix=" px",
        )
        self.garden_dwell = self._spin(
            250,
            3000,
            initial.dwell_time_ms,
            suffix=" ms",
            step=50,
        )
        self.garden_baseline = self._spin(
            5,
            30,
            initial.baseline_seconds,
            suffix=" 秒",
        )
        self.garden_contingent = self._spin(
            10,
            120,
            initial.contingent_block_seconds,
            suffix=" 秒/块",
        )
        self.garden_replay = self._spin(
            10,
            120,
            initial.replay_block_seconds,
            suffix=" 秒",
        )
        self.garden_reward = self._spin(
            500,
            3000,
            initial.reward_animation_ms,
            suffix=" ms",
            step=100,
        )
        self.garden_sound = QCheckBox("启用温和语音反馈")
        self.garden_sound.setChecked(initial.sound_enabled)
        self.garden_cursor = QCheckBox("患者屏幕显示实时视线光标")
        self.garden_cursor.setChecked(initial.show_gaze_cursor)

        form.addRow("花朵数量：", self.garden_object_count)
        form.addRow("花朵直径：", self.garden_object_diameter)
        form.addRow("持续注视阈值：", self.garden_dwell)
        form.addRow("基线块：", self.garden_baseline)
        form.addRow("联动块：", self.garden_contingent)
        form.addRow("回放块：", self.garden_replay)
        form.addRow("奖励动画：", self.garden_reward)
        form.addRow("声音：", self.garden_sound)
        form.addRow("视线反馈：", self.garden_cursor)

        layout = QVBoxLayout(page)
        heading = QLabel("点亮花园设置")
        heading.setStyleSheet("font-size:22px; font-weight:700;")
        layout.addWidget(heading)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(self._page_buttons(GazeGameMode.GARDEN))
        return page

    def _build_hunt_page(self) -> QWidget:
        initial = self._initial.treasure_hunt
        page = QWidget()
        form = QFormLayout()
        self.hunt_preview_count = self._spin(0, 30, initial.preview_trial_count)
        self.hunt_popout_count = self._spin(0, 30, initial.popout_trial_count)
        self.hunt_catch_count = self._spin(0, 10, initial.catch_trial_count)
        self.hunt_distractor_count = self._spin(1, 5, initial.distractor_count)
        self.hunt_preview_ms = self._spin(
            500,
            5000,
            initial.target_preview_ms,
            suffix=" ms",
            step=100,
        )
        self.hunt_interval_ms = self._spin(
            250,
            2000,
            initial.interstimulus_ms,
            suffix=" ms",
            step=50,
        )
        self.hunt_dwell = self._spin(
            250,
            5000,
            initial.dwell_time_ms,
            suffix=" ms",
            step=50,
        )
        self.hunt_trial_seconds = self._spin(
            3,
            60,
            initial.trial_duration_seconds,
            suffix=" 秒/试次",
        )
        self.hunt_reward = self._spin(
            500,
            3000,
            initial.reward_animation_ms,
            suffix=" ms",
            step=100,
        )
        self.hunt_category_list = QListWidget()
        self.hunt_category_list.setMinimumHeight(110)
        self.hunt_style_list = QListWidget()
        self.hunt_style_list.setMinimumHeight(90)
        filters = QHBoxLayout()
        filters.addWidget(self.hunt_category_list)
        filters.addWidget(self.hunt_style_list)
        self.hunt_randomize = QCheckBox("随机排列试次并平衡目标位置")
        self.hunt_randomize.setChecked(initial.randomize_trial_order)
        self.hunt_sound = QCheckBox("启用温和语音反馈")
        self.hunt_sound.setChecked(initial.sound_enabled)
        self.hunt_cursor = QCheckBox("患者屏幕显示实时视线光标")
        self.hunt_cursor.setChecked(initial.show_gaze_cursor)
        manage = QPushButton("管理共用图片库…")
        manage.clicked.connect(self._manage_images)

        form.addRow("预览搜索试次：", self.hunt_preview_count)
        form.addRow("突现试次：", self.hunt_popout_count)
        form.addRow("目标缺失试次：", self.hunt_catch_count)
        form.addRow("干扰图片数量：", self.hunt_distractor_count)
        form.addRow("目标预览：", self.hunt_preview_ms)
        form.addRow("预览后间隔：", self.hunt_interval_ms)
        form.addRow("持续注视阈值：", self.hunt_dwell)
        form.addRow("最长呈现：", self.hunt_trial_seconds)
        form.addRow("奖励动画：", self.hunt_reward)
        form.addRow("类别 / 风格（可多选）：", filters)
        form.addRow("图片库：", manage)
        form.addRow("顺序：", self.hunt_randomize)
        form.addRow("声音：", self.hunt_sound)
        form.addRow("视线反馈：", self.hunt_cursor)
        self._reload_filters(
            selected_categories=set(initial.category_filters),
            selected_styles=set(initial.style_filters),
        )

        layout = QVBoxLayout(page)
        heading = QLabel("视觉寻宝设置")
        heading.setStyleSheet("font-size:22px; font-weight:700;")
        layout.addWidget(heading)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(self._page_buttons(GazeGameMode.TREASURE_HUNT))
        return page

    def _build_starlight_page(self) -> QWidget:
        initial = self._initial.starlight_route
        page = QWidget()
        form = QFormLayout()
        self.starlight_round_count = self._spin(6, 120, initial.round_count)
        self.starlight_initial_level = self._spin(1, 10, initial.initial_level)
        self.starlight_dwell = self._spin(
            250, 3000, initial.dwell_time_ms, suffix=" ms", step=50
        )
        self.starlight_trial_seconds = self._spin(
            3, 30, initial.trial_duration_seconds, suffix=" 秒/轮"
        )
        self.starlight_probe_interval = self._spin(
            2, 10, initial.edge_probe_interval, suffix=" 轮"
        )
        self.starlight_sound = QCheckBox("启用温和语音反馈")
        self.starlight_sound.setChecked(initial.sound_enabled)
        self.starlight_cursor = QCheckBox("患者屏幕显示实时视线光标")
        self.starlight_cursor.setChecked(initial.show_gaze_cursor)
        form.addRow("总轮数：", self.starlight_round_count)
        form.addRow("起始等级：", self.starlight_initial_level)
        form.addRow("持续注视阈值：", self.starlight_dwell)
        form.addRow("每轮最长：", self.starlight_trial_seconds)
        form.addRow("边缘试探间隔：", self.starlight_probe_interval)
        form.addRow("声音：", self.starlight_sound)
        form.addRow("视线反馈：", self.starlight_cursor)
        note = QLabel(
            "低质量或断流轮次只标记为无效，不触发降级。有效表现下降时自动回退；"
            "系统从中央安全区域开始，逐边试探并学习患者可达边界。"
        )
        note.setWordWrap(True)
        layout = QVBoxLayout(page)
        heading = QLabel("星光航线设置")
        heading.setStyleSheet("font-size:22px; font-weight:700;")
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(self._page_buttons(GazeGameMode.STARLIGHT_ROUTE))
        return page

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
            selected_categories = set(self._checked_values(self.hunt_category_list))

        if selected_styles is None:
            selected_styles = set(self._checked_values(self.hunt_style_list))

        assets = self.image_store.load()
        self._fill_filter_list(
            self.hunt_category_list,
            sorted({asset.category for asset in assets}),
            selected_categories,
        )
        self._fill_filter_list(
            self.hunt_style_list,
            sorted({asset.style for asset in assets}),
            selected_styles,
        )

    def _manage_images(self) -> None:
        ImageLibraryDialog(self.image_store, self).exec()
        self._reload_filters()

    def build_config(self) -> GazeGameConfig:
        garden = GazeContingencyConfig(
            object_count=self.garden_object_count.value(),
            object_diameter_px=self.garden_object_diameter.value(),
            dwell_time_ms=self.garden_dwell.value(),
            baseline_seconds=self.garden_baseline.value(),
            contingent_block_seconds=self.garden_contingent.value(),
            replay_block_seconds=self.garden_replay.value(),
            reward_animation_ms=self.garden_reward.value(),
            sound_enabled=self.garden_sound.isChecked(),
            show_gaze_cursor=self.garden_cursor.isChecked(),
            randomization_seed=self._initial.garden.randomization_seed,
        )
        hunt = VisualHuntConfig(
            preview_trial_count=self.hunt_preview_count.value(),
            popout_trial_count=self.hunt_popout_count.value(),
            catch_trial_count=self.hunt_catch_count.value(),
            distractor_count=self.hunt_distractor_count.value(),
            target_preview_ms=self.hunt_preview_ms.value(),
            interstimulus_ms=self.hunt_interval_ms.value(),
            dwell_time_ms=self.hunt_dwell.value(),
            trial_duration_seconds=self.hunt_trial_seconds.value(),
            reward_animation_ms=self.hunt_reward.value(),
            sound_enabled=self.hunt_sound.isChecked(),
            show_gaze_cursor=self.hunt_cursor.isChecked(),
            randomize_trial_order=self.hunt_randomize.isChecked(),
            randomization_seed=self._initial.treasure_hunt.randomization_seed,
            category_filters=self._checked_values(self.hunt_category_list),
            style_filters=self._checked_values(self.hunt_style_list),
        )
        starlight = StarlightRouteConfig(
            round_count=self.starlight_round_count.value(),
            initial_level=self.starlight_initial_level.value(),
            dwell_time_ms=self.starlight_dwell.value(),
            trial_duration_seconds=self.starlight_trial_seconds.value(),
            edge_probe_interval=self.starlight_probe_interval.value(),
            sound_enabled=self.starlight_sound.isChecked(),
            show_gaze_cursor=self.starlight_cursor.isChecked(),
            randomization_seed=self._initial.starlight_route.randomization_seed,
        )
        return GazeGameConfig(
            default_mode=self._selected_mode,
            garden=garden,
            treasure_hunt=hunt,
            starlight_route=starlight,
        )

    def _accept_mode(self, mode: GazeGameMode) -> None:
        self._selected_mode = mode

        try:
            config = self.build_config()

            if mode == GazeGameMode.TREASURE_HUNT:
                eligible_visual_hunt_assets(config.treasure_hunt, self.image_store)
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "眼动游戏设置无效", str(error))
            return

        self.accept()
