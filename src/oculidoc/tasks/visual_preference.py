"""Pure side-swap protocols and descriptive metrics for visual preference."""

from __future__ import annotations

import random
import secrets
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from statistics import median
from time import monotonic_ns
from uuid import uuid4

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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
from oculidoc.image_library import (
    ImageAsset,
    ImageLibraryDialog,
    ImageLibraryStore,
    asset_preview_pixmap,
)


def _bounded_int(name: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")


def _optional_seed(value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("randomization_seed must be an integer or null.")
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("randomization_seed must be a 32-bit unsigned integer.")


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _median(values: Iterable[float]) -> float | None:
    materialized = tuple(values)
    return float(median(materialized)) if materialized else None


class PreferenceComparisonType(StrEnum):
    GENERIC_INTEREST = "generic_interest"


@dataclass(frozen=True, slots=True)
class PreferencePair:
    pair_id: str
    image_a_id: str
    image_b_id: str
    pair_label: str
    comparison_type: PreferenceComparisonType = PreferenceComparisonType.GENERIC_INTEREST
    matching_note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "comparison_type",
            PreferenceComparisonType(self.comparison_type),
        )
        for name in ("pair_id", "image_a_id", "image_b_id", "pair_label"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} cannot be empty.")
        if self.image_a_id == self.image_b_id:
            raise ValueError("A preference pair must use two different images.")

    @classmethod
    def from_value(cls, value: PreferencePair | dict[str, object]) -> PreferencePair:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("Preference pairs must be objects.")
        return cls(**value)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class VisualPreferenceConfig:
    pair_ids: tuple[str, ...] = ()
    pairs: tuple[PreferencePair, ...] = ()
    presentation_seconds: int = 6
    center_cue_ms: int = 800
    intertrial_ms: int = 1000
    present_each_side_once: bool = True
    randomize_pair_order: bool = True
    sound_intro_enabled: bool = True
    show_gaze_cursor: bool = False
    minimum_trial_valid_ratio: float = 0.50
    randomization_seed: int | None = None

    def __post_init__(self) -> None:
        pair_ids = tuple(str(value).strip() for value in self.pair_ids)
        pairs = tuple(PreferencePair.from_value(value) for value in self.pairs)
        object.__setattr__(self, "pair_ids", pair_ids)
        object.__setattr__(self, "pairs", pairs)

        if any(not value for value in pair_ids):
            raise ValueError("pair_ids cannot contain empty values.")
        if len(set(pair_ids)) != len(pair_ids):
            raise ValueError("pair_ids cannot contain duplicates.")
        if pair_ids and not 2 <= len(pair_ids) <= 12:
            raise ValueError("pair_ids must be empty or contain 2 to 12 pairs.")
        stored_ids = [pair.pair_id for pair in pairs]
        if len(set(stored_ids)) != len(stored_ids):
            raise ValueError("Stored preference pair IDs must be unique.")
        missing = sorted(set(pair_ids) - set(stored_ids))
        if missing:
            raise ValueError(f"Selected preference pairs are missing: {', '.join(missing)}")

        _bounded_int("presentation_seconds", self.presentation_seconds, 3, 15)
        _bounded_int("center_cue_ms", self.center_cue_ms, 0, 3000)
        _bounded_int("intertrial_ms", self.intertrial_ms, 500, 5000)
        if self.present_each_side_once is not True:
            raise ValueError("present_each_side_once must remain true in the MVP.")
        if not isinstance(self.randomize_pair_order, bool):
            raise TypeError("randomize_pair_order must be a boolean.")
        if not isinstance(self.sound_intro_enabled, bool):
            raise TypeError("sound_intro_enabled must be a boolean.")
        if not isinstance(self.show_gaze_cursor, bool):
            raise TypeError("show_gaze_cursor must be a boolean.")
        if isinstance(self.minimum_trial_valid_ratio, bool) or not isinstance(
            self.minimum_trial_valid_ratio,
            (int, float),
        ):
            raise TypeError("minimum_trial_valid_ratio must be numeric.")
        if not 0.20 <= float(self.minimum_trial_valid_ratio) <= 0.90:
            raise ValueError("minimum_trial_valid_ratio must be between 0.20 and 0.90.")
        _optional_seed(self.randomization_seed)

    @property
    def selected_pairs(self) -> tuple[PreferencePair, ...]:
        by_id = {pair.pair_id: pair for pair in self.pairs}
        return tuple(by_id[pair_id] for pair_id in self.pair_ids)


class VisualPreferenceSetupDialog(QDialog):
    """Build and select side-swapped image pairs from the shared image library."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        config: VisualPreferenceConfig | None = None,
        image_library_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        initial = config or VisualPreferenceConfig()
        self._initial = initial
        self._pairs = {pair.pair_id: pair for pair in initial.pairs}
        self.image_store = ImageLibraryStore(
            image_library_path or (Path.home() / ".oculidoc" / "data" / "image_library")
        )
        self.setWindowTitle("视觉偏好设置")
        self.resize(780, 780)

        explanation = QLabel(
            "每个刺激对固定呈现两次并交换左右位置。任务只描述注视分布，"
            "不把某张图片的停留解释为识别、选择或意识诊断。"
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color:#365269; background:#eef7ff; padding:8px;")

        self.pair_list = QListWidget()
        self.pair_list.setMinimumHeight(190)
        self._refresh_pair_list(selected=set(initial.pair_ids))

        self.image_a_combo = QComboBox()
        self.image_b_combo = QComboBox()
        self.pair_label_edit = QLineEdit()
        self.pair_label_edit.setPlaceholderText("例如：人物 / 日常物品")
        self._reload_image_choices()

        add_button = QPushButton("新增并选中刺激对")
        add_button.clicked.connect(self._add_pair)
        remove_button = QPushButton("删除列表中选中的刺激对")
        remove_button.clicked.connect(self._remove_pair)
        manage_button = QPushButton("管理共用图片库…")
        manage_button.clicked.connect(self._manage_images)

        pair_form = QFormLayout()
        pair_form.addRow("图片 A：", self.image_a_combo)
        pair_form.addRow("图片 B：", self.image_b_combo)
        pair_form.addRow("刺激对标签：", self.pair_label_edit)
        pair_buttons = QHBoxLayout()
        pair_buttons.addWidget(add_button)
        pair_buttons.addWidget(remove_button)
        pair_buttons.addWidget(manage_button)

        self.presentation_seconds = QSpinBox()
        self.presentation_seconds.setRange(3, 15)
        self.presentation_seconds.setValue(initial.presentation_seconds)
        self.presentation_seconds.setSuffix(" 秒")
        self.center_cue_ms = QSpinBox()
        self.center_cue_ms.setRange(0, 3000)
        self.center_cue_ms.setValue(initial.center_cue_ms)
        self.center_cue_ms.setSingleStep(100)
        self.center_cue_ms.setSuffix(" ms")
        self.intertrial_ms = QSpinBox()
        self.intertrial_ms.setRange(500, 5000)
        self.intertrial_ms.setValue(initial.intertrial_ms)
        self.intertrial_ms.setSingleStep(100)
        self.intertrial_ms.setSuffix(" ms")
        self.minimum_valid_ratio = QDoubleSpinBox()
        self.minimum_valid_ratio.setRange(0.20, 0.90)
        self.minimum_valid_ratio.setSingleStep(0.05)
        self.minimum_valid_ratio.setDecimals(2)
        self.minimum_valid_ratio.setValue(float(initial.minimum_trial_valid_ratio))
        self.randomize_pair_order = QCheckBox("随机排列刺激对")
        self.randomize_pair_order.setChecked(initial.randomize_pair_order)
        self.sound_intro = QCheckBox("启用开始语音")
        self.sound_intro.setChecked(initial.sound_intro_enabled)
        self.show_gaze_cursor = QCheckBox("患者屏幕显示实时视线光标")
        self.show_gaze_cursor.setChecked(initial.show_gaze_cursor)

        settings = QFormLayout()
        settings.addRow("单次呈现：", self.presentation_seconds)
        settings.addRow("中央提示：", self.center_cue_ms)
        settings.addRow("试次间隔：", self.intertrial_ms)
        settings.addRow("最低试次有效率：", self.minimum_valid_ratio)
        settings.addRow("顺序：", self.randomize_pair_order)
        settings.addRow("语音：", self.sound_intro)
        settings.addRow("视线反馈：", self.show_gaze_cursor)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(explanation)
        root.addWidget(QLabel("本次刺激对（勾选 2–12 组）："))
        root.addWidget(self.pair_list)
        root.addLayout(pair_form)
        root.addLayout(pair_buttons)
        root.addLayout(settings)
        root.addWidget(buttons)

    def _asset_labels(self) -> dict[str, str]:
        return {asset.image_id: asset.label for asset in self.image_store.load()}

    def _refresh_pair_list(self, *, selected: set[str] | None = None) -> None:
        if selected is None:
            selected = {
                str(self.pair_list.item(index).data(Qt.ItemDataRole.UserRole))
                for index in range(self.pair_list.count())
                if self.pair_list.item(index).checkState() == Qt.CheckState.Checked
            }

        labels = self._asset_labels()
        self.pair_list.clear()

        for pair in self._pairs.values():
            image_a = labels.get(pair.image_a_id, f"缺失：{pair.image_a_id}")
            image_b = labels.get(pair.image_b_id, f"缺失：{pair.image_b_id}")
            item = QListWidgetItem(f"{pair.pair_label} · {image_a} ↔ {image_b}")
            item.setData(Qt.ItemDataRole.UserRole, pair.pair_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if pair.pair_id in selected else Qt.CheckState.Unchecked
            )
            self.pair_list.addItem(item)

    def _reload_image_choices(self) -> None:
        selected_a = self.image_a_combo.currentData()
        selected_b = self.image_b_combo.currentData()
        assets = self.image_store.load()

        for combo, selected in (
            (self.image_a_combo, selected_a),
            (self.image_b_combo, selected_b),
        ):
            combo.clear()

            for asset in assets:
                combo.addItem(
                    f"{asset.label} · {asset.category} · {asset.style}",
                    asset.image_id,
                )

            index = combo.findData(selected)

            if index >= 0:
                combo.setCurrentIndex(index)

        if self.image_b_combo.count() > 1 and self.image_b_combo.currentIndex() == 0:
            self.image_b_combo.setCurrentIndex(1)

    def _manage_images(self) -> None:
        ImageLibraryDialog(self.image_store, self).exec()
        self._reload_image_choices()
        self._refresh_pair_list()

    def _add_pair(self) -> None:
        image_a_id = self.image_a_combo.currentData()
        image_b_id = self.image_b_combo.currentData()

        if not image_a_id or not image_b_id:
            QMessageBox.warning(self, "无法新增刺激对", "图片库中至少需要两张图片。")
            return

        if image_a_id == image_b_id:
            QMessageBox.warning(self, "无法新增刺激对", "图片 A 与图片 B 不能相同。")
            return

        labels = self._asset_labels()
        pair = PreferencePair(
            pair_id=f"pair-{uuid4().hex}",
            image_a_id=str(image_a_id),
            image_b_id=str(image_b_id),
            pair_label=(
                self.pair_label_edit.text().strip()
                or f"{labels.get(str(image_a_id), 'A')} / {labels.get(str(image_b_id), 'B')}"
            ),
        )
        self._pairs[pair.pair_id] = pair
        selected = {
            str(self.pair_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.pair_list.count())
            if self.pair_list.item(index).checkState() == Qt.CheckState.Checked
        }
        selected.add(pair.pair_id)
        self._refresh_pair_list(selected=selected)
        self.pair_label_edit.clear()

    def _remove_pair(self) -> None:
        item = self.pair_list.currentItem()

        if item is None:
            return

        pair_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._pairs.pop(pair_id, None)
        self._refresh_pair_list()

    def build_config(self) -> VisualPreferenceConfig:
        pair_ids = tuple(
            str(self.pair_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.pair_list.count())
            if self.pair_list.item(index).checkState() == Qt.CheckState.Checked
        )
        return VisualPreferenceConfig(
            pair_ids=pair_ids,
            pairs=tuple(self._pairs.values()),
            presentation_seconds=self.presentation_seconds.value(),
            center_cue_ms=self.center_cue_ms.value(),
            intertrial_ms=self.intertrial_ms.value(),
            present_each_side_once=True,
            randomize_pair_order=self.randomize_pair_order.isChecked(),
            sound_intro_enabled=self.sound_intro.isChecked(),
            show_gaze_cursor=self.show_gaze_cursor.isChecked(),
            minimum_trial_valid_ratio=self.minimum_valid_ratio.value(),
            randomization_seed=self._initial.randomization_seed,
        )

    def _accept_if_valid(self) -> None:
        try:
            config = self.build_config()
            validate_visual_preference_for_start(
                config,
                (asset.image_id for asset in self.image_store.load()),
            )
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "视觉偏好设置无效", str(error))
            return

        self.accept()


def validate_visual_preference_for_start(
    config: VisualPreferenceConfig,
    available_image_ids: Iterable[str],
) -> None:
    """Reject incomplete selection or references to images no longer available."""
    if not config.pair_ids:
        raise ValueError("Select 2 to 12 visual-preference pairs before starting.")
    available = {str(value) for value in available_image_ids}
    missing_images = sorted(
        {
            image_id
            for pair in config.selected_pairs
            for image_id in (pair.image_a_id, pair.image_b_id)
            if image_id not in available
        }
    )
    if missing_images:
        raise ValueError("Visual-preference images are missing: " + ", ".join(missing_images))


@dataclass(frozen=True, slots=True)
class VisualPreferenceTrial:
    trial_id: str
    trial_number: int
    trial_count: int
    pair_id: str
    side_presentation_index: int
    image_a_id: str
    image_b_id: str
    left_image_id: str
    right_image_id: str
    a_on_left: bool


@dataclass(frozen=True, slots=True)
class VisualPreferenceProtocol:
    randomization_seed: int
    trials: tuple[VisualPreferenceTrial, ...]


def visual_preference_protocol(
    config: VisualPreferenceConfig,
) -> VisualPreferenceProtocol:
    """Present every selected pair twice without adjacent repeated pairs."""
    if not config.pair_ids:
        raise ValueError("Select visual-preference pairs before building a protocol.")
    seed = (
        config.randomization_seed if config.randomization_seed is not None else secrets.randbits(32)
    )
    rng = random.Random(seed)
    pairs = list(config.selected_pairs)
    if config.randomize_pair_order:
        rng.shuffle(pairs)

    first_a_on_left = {pair.pair_id: index % 2 == 0 for index, pair in enumerate(pairs)}
    second = list(pairs)
    if config.randomize_pair_order:
        rng.shuffle(second)
    if second[0].pair_id == pairs[-1].pair_id:
        second = second[1:] + second[:1]

    scheduled = [(pair, 1) for pair in pairs] + [(pair, 2) for pair in second]
    trials: list[VisualPreferenceTrial] = []
    for index, (pair, presentation_index) in enumerate(scheduled):
        a_on_left = (
            first_a_on_left[pair.pair_id]
            if presentation_index == 1
            else not first_a_on_left[pair.pair_id]
        )
        trials.append(
            VisualPreferenceTrial(
                trial_id=f"preference-{index + 1:03d}-{pair.pair_id}",
                trial_number=index + 1,
                trial_count=len(scheduled),
                pair_id=pair.pair_id,
                side_presentation_index=presentation_index,
                image_a_id=pair.image_a_id,
                image_b_id=pair.image_b_id,
                left_image_id=pair.image_a_id if a_on_left else pair.image_b_id,
                right_image_id=pair.image_b_id if a_on_left else pair.image_a_id,
                a_on_left=a_on_left,
            )
        )
    return VisualPreferenceProtocol(
        randomization_seed=seed,
        trials=tuple(trials),
    )


@dataclass(frozen=True, slots=True)
class VisualPreferenceTrialObservation:
    pair_id: str
    a_on_left: bool
    sample_count: int
    valid_sample_count: int
    dwell_a_ms: float
    dwell_b_ms: float
    background_dwell_ms: float
    first_entry: str | None = None
    first_entry_ms: float | None = None
    switch_count: int = 0

    def __post_init__(self) -> None:
        if not self.pair_id.strip():
            raise ValueError("pair_id cannot be empty.")
        if not isinstance(self.a_on_left, bool):
            raise TypeError("a_on_left must be a boolean.")
        _bounded_int("sample_count", self.sample_count, 0, 2_000_000_000)
        _bounded_int(
            "valid_sample_count",
            self.valid_sample_count,
            0,
            self.sample_count,
        )
        _bounded_int("switch_count", self.switch_count, 0, 2_000_000_000)
        if any(
            value < 0
            for value in (
                self.dwell_a_ms,
                self.dwell_b_ms,
                self.background_dwell_ms,
            )
        ):
            raise ValueError("Preference dwell durations cannot be negative.")
        if self.first_entry not in {None, "a", "b"}:
            raise ValueError("first_entry must be 'a', 'b', or null.")
        if self.first_entry_ms is not None and self.first_entry_ms < 0:
            raise ValueError("first_entry_ms cannot be negative.")

    def is_usable(self, threshold: float) -> bool:
        return self.sample_count > 0 and self.valid_sample_count / self.sample_count >= threshold


def summarize_visual_preference_observations(
    config: VisualPreferenceConfig,
    observations: Iterable[VisualPreferenceTrialObservation],
) -> dict[str, object]:
    """Keep image preference separate from fixed left/right gaze bias."""
    trials = tuple(observations)
    usable = tuple(
        trial for trial in trials if trial.is_usable(float(config.minimum_trial_valid_ratio))
    )
    sample_count = sum(trial.sample_count for trial in trials)
    valid_sample_count = sum(trial.valid_sample_count for trial in trials)
    image_dwell_total = sum(trial.dwell_a_ms + trial.dwell_b_ms for trial in usable)
    left_dwell = sum(trial.dwell_a_ms if trial.a_on_left else trial.dwell_b_ms for trial in usable)
    right_dwell = image_dwell_total - left_dwell
    first_entries = tuple(trial for trial in usable if trial.first_entry is not None)
    pair_trials: dict[str, list[VisualPreferenceTrialObservation]] = defaultdict(list)
    for trial in usable:
        pair_trials[trial.pair_id].append(trial)

    eligible_pairs = 0
    consistent_pairs = 0
    for pair_observations in pair_trials.values():
        if len(pair_observations) != 2:
            continue
        if {trial.a_on_left for trial in pair_observations} != {True, False}:
            continue
        winners: list[str] = []
        for trial in pair_observations:
            if trial.dwell_a_ms == trial.dwell_b_ms:
                winners = []
                break
            winners.append("a" if trial.dwell_a_ms > trial.dwell_b_ms else "b")
        if len(winners) != 2:
            continue
        eligible_pairs += 1
        consistent_pairs += winners[0] == winners[1]

    side_swap_consistency = consistent_pairs / eligible_pairs if eligible_pairs >= 3 else None
    any_image_entry = sum(trial.dwell_a_ms + trial.dwell_b_ms > 0 for trial in usable)

    return {
        "trial_count": len(trials),
        "usable_trial_count": len(usable),
        "valid_sample_ratio": _ratio(valid_sample_count, sample_count),
        "usable_trial_ratio": _ratio(len(usable), len(trials)),
        "any_image_entry_ratio": _ratio(any_image_entry, len(usable)),
        "median_first_image_entry_ms": _median(
            trial.first_entry_ms for trial in first_entries if trial.first_entry_ms is not None
        ),
        "image_dwell_ms_a": sum(trial.dwell_a_ms for trial in usable),
        "image_dwell_ms_b": sum(trial.dwell_b_ms for trial in usable),
        "image_dwell_share_a": _ratio(
            sum(trial.dwell_a_ms for trial in usable),
            image_dwell_total,
        ),
        "image_dwell_share_b": _ratio(
            sum(trial.dwell_b_ms for trial in usable),
            image_dwell_total,
        ),
        "left_dwell_share": _ratio(left_dwell, left_dwell + right_dwell),
        "first_entry_share_a": _ratio(
            sum(trial.first_entry == "a" for trial in first_entries),
            len(first_entries),
        ),
        "first_entry_share_b": _ratio(
            sum(trial.first_entry == "b" for trial in first_entries),
            len(first_entries),
        ),
        "first_entry_share_left": _ratio(
            sum(
                (trial.first_entry == "a" and trial.a_on_left)
                or (trial.first_entry == "b" and not trial.a_on_left)
                for trial in first_entries
            ),
            len(first_entries),
        ),
        "first_entry_share_right": _ratio(
            sum(
                (trial.first_entry == "a" and not trial.a_on_left)
                or (trial.first_entry == "b" and trial.a_on_left)
                for trial in first_entries
            ),
            len(first_entries),
        ),
        "side_swap_consistency": side_swap_consistency,
        "side_swap_pair_denominator": eligible_pairs,
        "median_switch_count": _median(trial.switch_count for trial in usable),
        "background_dwell_ratio": _ratio(
            sum(trial.background_dwell_ms for trial in usable),
            sum(
                trial.dwell_a_ms + trial.dwell_b_ms + trial.background_dwell_ms for trial in usable
            ),
        ),
        "interpretation": "descriptive_visual_preference_observation_only",
    }


class VisualPreferencePhase(StrEnum):
    READY = "ready"
    EXAMPLE = "example"
    CENTER_CUE = "center_cue"
    PAIR_VISIBLE = "pair_visible"
    INTERTRIAL = "intertrial"
    COMPLETED = "completed"


class VisualPreferenceTask(QWidget):
    """Present fixed-duration side-swapped pairs without gaze-contingent feedback."""

    protocol_completed = Signal()
    speech_requested = Signal(str)

    def __init__(
        self,
        config: VisualPreferenceConfig,
        store: ImageLibraryStore,
        *,
        assets: Iterable[ImageAsset] | None = None,
        allow_mouse_fallback: bool = False,
    ) -> None:
        super().__init__()
        available = tuple(assets) if assets is not None else store.load()
        available_ids = {asset.image_id for asset in available}
        validate_visual_preference_for_start(config, available_ids)
        self.config = config
        self.image_store = store
        self._assets = {asset.image_id: asset for asset in available}
        self.protocol = visual_preference_protocol(config)
        self.allow_mouse_fallback = allow_mouse_fallback
        self.setMinimumSize(800, 560)
        self.setMouseTracking(allow_mouse_fallback)

        if not allow_mouse_fallback:
            self.setCursor(Qt.CursorShape.BlankCursor)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.advance_time)
        self._pixmap_cache: dict[tuple[str, int], QPixmap] = {}
        self._reset_run_state()

    def _reset_run_state(self) -> None:
        self._running = False
        self._protocol_finished = False
        self._phase = VisualPreferencePhase.READY
        self._phase_started_ns: int | None = None
        self._phase_deadline_ns: int | None = None
        self._trial_index = 0
        self._recording_events: list[dict[str, object]] = []
        self._trial_observations: list[VisualPreferenceTrialObservation] = []
        self._result_cache: dict[str, object] | None = None
        self._last_gaze_normalized: tuple[float, float] | None = None
        self._reset_trial_measurements()

    def _reset_trial_measurements(self) -> None:
        self._pair_started_ns: int | None = None
        self._last_sample_timestamp_ns: int | None = None
        self._previous_valid = False
        self._previous_region: str | None = None
        self._last_image_region: str | None = None
        self._sample_count = 0
        self._valid_sample_count = 0
        self._dwell_a_ms = 0.0
        self._dwell_b_ms = 0.0
        self._background_dwell_ms = 0.0
        self._first_entry: str | None = None
        self._first_entry_ms: float | None = None
        self._switch_count = 0

    @property
    def phase(self) -> VisualPreferencePhase:
        return self._phase

    @property
    def phase_deadline_ns(self) -> int | None:
        return self._phase_deadline_ns

    @property
    def current_trial(self) -> VisualPreferenceTrial:
        return self.protocol.trials[min(self._trial_index, len(self.protocol.trials) - 1)]

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

    def _trial_payload(
        self,
        trial: VisualPreferenceTrial | None = None,
    ) -> dict[str, object]:
        active = trial or self.current_trial
        return {
            "trial_id": active.trial_id,
            "trial_number": active.trial_number,
            "trial_count": active.trial_count,
            "pair_id": active.pair_id,
            "side_presentation_index": active.side_presentation_index,
            "image_a_id": active.image_a_id,
            "image_b_id": active.image_b_id,
            "left_image_id": active.left_image_id,
            "right_image_id": active.right_image_id,
            "a_on_left": active.a_on_left,
            "randomization_seed": self.protocol.randomization_seed,
        }

    def _set_phase(
        self,
        phase: VisualPreferencePhase,
        timestamp_ns: int,
        duration_ms: int | None,
    ) -> None:
        self._phase = phase
        self._phase_started_ns = int(timestamp_ns)
        self._phase_deadline_ns = (
            None if duration_ms is None else int(timestamp_ns) + int(duration_ms) * 1_000_000
        )
        self.update()

    def start(self, timestamp_ns: int | None = None) -> None:
        self._reset_run_state()
        started_ns = monotonic_ns() if timestamp_ns is None else int(timestamp_ns)
        self._running = True
        self._queue_event(
            "protocol_started",
            timestamp_ns=started_ns,
            payload={
                "task_kind": "visual_preference",
                "trial_count": len(self.protocol.trials),
                "randomization_seed": self.protocol.randomization_seed,
            },
        )
        self._queue_event(
            "example_presented",
            timestamp_ns=started_ns,
            payload={
                **self._trial_payload(self.protocol.trials[0]),
                "counted": False,
            },
        )

        if self.config.sound_intro_enabled:
            self.speech_requested.emit("看看这些图片")

        self._set_phase(
            VisualPreferencePhase.EXAMPLE,
            started_ns,
            min(2_000, self.config.presentation_seconds * 1_000),
        )
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._running = False

    def _begin_trial(self, timestamp_ns: int) -> None:
        self._reset_trial_measurements()
        self._queue_event(
            "trial_started",
            timestamp_ns=timestamp_ns,
            payload=self._trial_payload(),
        )
        self._set_phase(
            VisualPreferencePhase.CENTER_CUE,
            timestamp_ns,
            self.config.center_cue_ms,
        )

    def _begin_pair(self, timestamp_ns: int) -> None:
        self._pair_started_ns = int(timestamp_ns)
        self._queue_event(
            "pair_presented",
            timestamp_ns=timestamp_ns,
            payload=self._trial_payload(),
        )
        self._set_phase(
            VisualPreferencePhase.PAIR_VISIBLE,
            timestamp_ns,
            self.config.presentation_seconds * 1_000,
        )

    def _current_observation(self) -> VisualPreferenceTrialObservation:
        return VisualPreferenceTrialObservation(
            pair_id=self.current_trial.pair_id,
            a_on_left=self.current_trial.a_on_left,
            sample_count=self._sample_count,
            valid_sample_count=self._valid_sample_count,
            dwell_a_ms=self._dwell_a_ms,
            dwell_b_ms=self._dwell_b_ms,
            background_dwell_ms=self._background_dwell_ms,
            first_entry=self._first_entry,
            first_entry_ms=self._first_entry_ms,
            switch_count=self._switch_count,
        )

    def _finish_trial(self, timestamp_ns: int) -> None:
        observation = self._current_observation()
        self._trial_observations.append(observation)
        valid_ratio = (
            observation.valid_sample_count / observation.sample_count
            if observation.sample_count
            else 0.0
        )
        self._queue_event(
            "trial_finished",
            timestamp_ns=timestamp_ns,
            payload={
                **self._trial_payload(),
                "sample_count": observation.sample_count,
                "valid_sample_count": observation.valid_sample_count,
                "valid_sample_ratio": valid_ratio,
                "quality": (
                    "usable"
                    if valid_ratio >= self.config.minimum_trial_valid_ratio
                    else "low_validity"
                ),
                "dwell_a_ms": observation.dwell_a_ms,
                "dwell_b_ms": observation.dwell_b_ms,
                "background_dwell_ms": observation.background_dwell_ms,
                "first_entry": observation.first_entry,
                "first_entry_ms": observation.first_entry_ms,
                "switch_count": observation.switch_count,
            },
        )
        self._set_phase(
            VisualPreferencePhase.INTERTRIAL,
            timestamp_ns,
            self.config.intertrial_ms,
        )

    def _finish_protocol(self, timestamp_ns: int) -> None:
        if self._protocol_finished:
            return

        self._protocol_finished = True
        self._running = False
        self._timer.stop()
        self._set_phase(VisualPreferencePhase.COMPLETED, timestamp_ns, None)
        self._queue_event(
            "protocol_finished",
            timestamp_ns=timestamp_ns,
            payload={
                "task_kind": "visual_preference",
                "trial_count": len(self._trial_observations),
                "randomization_seed": self.protocol.randomization_seed,
            },
        )
        self.protocol_completed.emit()

    def advance_time(self, timestamp_ns: int | None = None) -> None:
        if not self._running or self._protocol_finished:
            return

        now_ns = monotonic_ns() if timestamp_ns is None else int(timestamp_ns)

        while (
            self._running
            and self._phase_deadline_ns is not None
            and now_ns >= self._phase_deadline_ns
        ):
            boundary_ns = self._phase_deadline_ns

            if self._phase is VisualPreferencePhase.EXAMPLE:
                self._begin_trial(boundary_ns)
            elif self._phase is VisualPreferencePhase.CENTER_CUE:
                self._begin_pair(boundary_ns)
            elif self._phase is VisualPreferencePhase.PAIR_VISIBLE:
                self._finish_trial(boundary_ns)
            elif self._phase is VisualPreferencePhase.INTERTRIAL:
                if self._trial_index + 1 >= len(self.protocol.trials):
                    self._finish_protocol(boundary_ns)
                else:
                    self._trial_index += 1
                    self._begin_trial(boundary_ns)
            else:
                break

        self.update()

    def expire_current_phase(self, *, timestamp_ns: int | None = None) -> None:
        """Advance to the current phase boundary for deterministic integration tests."""
        boundary = self._phase_deadline_ns
        self.advance_time(
            monotonic_ns()
            if timestamp_ns is None and boundary is None
            else boundary
            if timestamp_ns is None
            else timestamp_ns
        )

    @staticmethod
    def pair_rectangles_normalized() -> tuple[QRectF, QRectF]:
        return (
            QRectF(0.06, 0.16, 0.40, 0.70),
            QRectF(0.54, 0.16, 0.40, 0.70),
        )

    def _region_at(self, x: float, y: float) -> str:
        point = QPointF(x, y)
        left, right = self.pair_rectangles_normalized()

        if left.contains(point):
            return "a" if self.current_trial.a_on_left else "b"

        if right.contains(point):
            return "b" if self.current_trial.a_on_left else "a"

        return "background"

    def _advance_previous_interval(self, timestamp_ns: int) -> None:
        previous_timestamp_ns = self._last_sample_timestamp_ns

        if previous_timestamp_ns is None or timestamp_ns <= previous_timestamp_ns:
            return

        delta_ms = min(250.0, (timestamp_ns - previous_timestamp_ns) / 1_000_000.0)

        if not self._previous_valid:
            return

        if self._previous_region == "a":
            self._dwell_a_ms += delta_ms
        elif self._previous_region == "b":
            self._dwell_b_ms += delta_ms
        else:
            self._background_dwell_ms += delta_ms

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        if not self._running or self._phase is not VisualPreferencePhase.PAIR_VISIBLE:
            return

        timestamp_ns = sample.timestamp.monotonic_timestamp_ns
        self.advance_time(timestamp_ns)

        if not self._running or self._phase is not VisualPreferencePhase.PAIR_VISIBLE:
            return

        self._advance_previous_interval(timestamp_ns)
        self._sample_count += 1
        gaze_x_value = sample.gaze_x_normalized
        gaze_y_value = sample.gaze_y_normalized
        valid = bool(sample.gaze_valid and gaze_x_value is not None and gaze_y_value is not None)

        if not valid:
            self._previous_valid = False
            self._previous_region = None
            self._last_gaze_normalized = None
            self._last_sample_timestamp_ns = timestamp_ns
            self.update()
            return

        assert gaze_x_value is not None
        assert gaze_y_value is not None
        gaze_x = max(0.0, min(1.0, float(gaze_x_value)))
        gaze_y = max(0.0, min(1.0, float(gaze_y_value)))
        region = self._region_at(gaze_x, gaze_y)
        self._valid_sample_count += 1
        self._last_gaze_normalized = (gaze_x, gaze_y)

        if region != self._previous_region:
            self._queue_event(
                "aoi_transition",
                timestamp_ns=timestamp_ns,
                payload={
                    **self._trial_payload(),
                    "from_aoi": self._previous_region,
                    "to_aoi": region,
                },
            )

        if region in {"a", "b"}:
            if self._first_entry is None and self._pair_started_ns is not None:
                self._first_entry = region
                self._first_entry_ms = max(
                    0.0,
                    (timestamp_ns - self._pair_started_ns) / 1_000_000.0,
                )
                self._queue_event(
                    "first_aoi_entry",
                    timestamp_ns=timestamp_ns,
                    payload={
                        **self._trial_payload(),
                        "logical_stimulus": region,
                        "screen_side": (
                            "left" if (region == "a") == self.current_trial.a_on_left else "right"
                        ),
                        "latency_ms": self._first_entry_ms,
                    },
                )

            if self._last_image_region is not None and region != self._last_image_region:
                self._switch_count += 1

            self._last_image_region = region

        self._previous_valid = True
        self._previous_region = region
        self._last_sample_timestamp_ns = timestamp_ns
        self.update()

    def _pair_aois(self) -> tuple[dict[str, object], ...]:
        trial = self.current_trial
        left, right = self.pair_rectangles_normalized()
        aois: list[dict[str, object]] = []

        for side, rectangle, image_id, logical in (
            (
                "left",
                left,
                trial.left_image_id,
                "a" if trial.a_on_left else "b",
            ),
            (
                "right",
                right,
                trial.right_image_id,
                "b" if trial.a_on_left else "a",
            ),
        ):
            aois.append(
                {
                    "aoi_id": f"preference-{side}",
                    "role": "target",
                    "left": rectangle.left(),
                    "top": rectangle.top(),
                    "right": rectangle.right(),
                    "bottom": rectangle.bottom(),
                    "label": "preference_stimulus",
                    "metadata": {
                        "pair_id": trial.pair_id,
                        "stimulus_id": image_id,
                        "logical_stimulus": logical,
                        "screen_side": side,
                        "side_presentation_index": (trial.side_presentation_index),
                    },
                }
            )

        aois.append(
            {
                "aoi_id": "preference-background",
                "role": "other",
                "left": 0.0,
                "top": 0.0,
                "right": 1.0,
                "bottom": 1.0,
                "label": "preference_background",
                "metadata": {
                    "pair_id": trial.pair_id,
                    "side_presentation_index": trial.side_presentation_index,
                },
            }
        )
        return tuple(aois)

    def recording_context_for_sample(
        self,
        _sample: EyeTrackerSample,
    ) -> dict[str, object]:
        if self._phase is VisualPreferencePhase.PAIR_VISIBLE:
            aois = self._pair_aois()
        else:
            aois = (
                {
                    "aoi_id": "preference-background",
                    "role": "other",
                    "left": 0.0,
                    "top": 0.0,
                    "right": 1.0,
                    "bottom": 1.0,
                    "label": "preference_background",
                    "metadata": {"phase": self._phase.value},
                },
            )

        return {
            "question_id": f"{self.current_trial.trial_id}:{self._phase.value}",
            "phase": self._phase.value,
            "aois": aois,
            "question_metadata": self._trial_payload(),
        }

    def drain_recording_events(self) -> tuple[dict[str, object], ...]:
        events = tuple(self._recording_events)
        self._recording_events.clear()
        return events

    def recording_result(self, reason: str) -> dict[str, object]:
        if self._result_cache is not None:
            return dict(self._result_cache)

        reason_text = reason.strip() if reason.strip() else "completed"
        observations = list(self._trial_observations)

        if not self._protocol_finished and self._phase is VisualPreferencePhase.PAIR_VISIBLE:
            observations.append(self._current_observation())

        completed = self._protocol_finished and reason_text in {
            "completed",
            "protocol_completed",
            "test_complete",
        }
        result_trials: list[dict[str, object]] = []

        for index, observation in enumerate(observations):
            trial = self.protocol.trials[index]
            valid_ratio = (
                observation.valid_sample_count / observation.sample_count
                if observation.sample_count
                else 0.0
            )
            result_trials.append(
                {
                    **self._trial_payload(trial),
                    "sample_count": observation.sample_count,
                    "valid_sample_count": observation.valid_sample_count,
                    "valid_sample_ratio": valid_ratio,
                    "quality": (
                        "usable"
                        if valid_ratio >= self.config.minimum_trial_valid_ratio
                        else "low_validity"
                    ),
                    "dwell_a_ms": observation.dwell_a_ms,
                    "dwell_b_ms": observation.dwell_b_ms,
                    "background_dwell_ms": observation.background_dwell_ms,
                    "first_entry": observation.first_entry,
                    "first_entry_ms": observation.first_entry_ms,
                    "switch_count": observation.switch_count,
                }
            )

        result = {
            "task_kind": "visual_preference",
            "completion_status": "completed" if completed else "interrupted",
            "completion_reason": reason_text,
            "randomization_seed": self.protocol.randomization_seed,
            "configuration": {
                "pair_ids": list(self.config.pair_ids),
                "presentation_seconds": self.config.presentation_seconds,
                "center_cue_ms": self.config.center_cue_ms,
                "intertrial_ms": self.config.intertrial_ms,
                "present_each_side_once": self.config.present_each_side_once,
                "minimum_trial_valid_ratio": self.config.minimum_trial_valid_ratio,
                "sound_intro_enabled": self.config.sound_intro_enabled,
                "show_gaze_cursor": self.config.show_gaze_cursor,
            },
            "trials": result_trials,
            **summarize_visual_preference_observations(
                self.config,
                observations,
            ),
        }
        self._result_cache = result
        return dict(result)

    def _asset_pixmap(self, image_id: str, size: int) -> QPixmap:
        bounded_size = max(64, min(2_048, int(size)))
        key = (image_id, bounded_size)

        if key not in self._pixmap_cache:
            self._pixmap_cache[key] = asset_preview_pixmap(
                self._assets[image_id],
                self.image_store,
                size=bounded_size,
                background="#f7fbff",
            )

        return self._pixmap_cache[key]

    def _draw_asset(
        self,
        painter: QPainter,
        image_id: str,
        rectangle: QRectF,
    ) -> None:
        painter.setBrush(QColor("#f7fbff"))
        painter.setPen(QPen(QColor("#6f94ad"), 4))
        painter.drawRoundedRect(rectangle, 24, 24)
        inset = rectangle.adjusted(14, 14, -14, -14)
        size = max(64, round(min(inset.width(), inset.height())))
        pixmap = self._asset_pixmap(image_id, size)
        painter.drawPixmap(inset, pixmap, QRectF(pixmap.rect()))

    def _pixel_rectangle(self, normalized: QRectF) -> QRectF:
        return QRectF(
            normalized.left() * self.width(),
            normalized.top() * self.height(),
            normalized.width() * self.width(),
            normalized.height() * self.height(),
        )

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor("#081b2a"))

        if self._phase is VisualPreferencePhase.COMPLETED:
            painter.setPen(QColor("#f7f7d4"))
            font = painter.font()
            font.setFamily("Microsoft YaHei UI")
            font.setPointSize(38)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "本次观看结束",
            )
            painter.end()
            return

        if self._phase is VisualPreferencePhase.CENTER_CUE:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#d8f3dc"))
            painter.drawEllipse(
                QPointF(self.width() / 2.0, self.height() / 2.0),
                18,
                18,
            )
        elif self._phase in {
            VisualPreferencePhase.EXAMPLE,
            VisualPreferencePhase.PAIR_VISIBLE,
        }:
            trial = (
                self.protocol.trials[0]
                if self._phase is VisualPreferencePhase.EXAMPLE
                else self.current_trial
            )
            left, right = self.pair_rectangles_normalized()
            self._draw_asset(
                painter,
                trial.left_image_id,
                self._pixel_rectangle(left),
            )
            self._draw_asset(
                painter,
                trial.right_image_id,
                self._pixel_rectangle(right),
            )

        if self.config.show_gaze_cursor and self._last_gaze_normalized is not None:
            gaze_x, gaze_y = self._last_gaze_normalized
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#ffffff"), 3))
            painter.drawEllipse(
                QPointF(gaze_x * self.width(), gaze_y * self.height()),
                14,
                14,
            )

        painter.end()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.stop()
            self.window().close()
            return

        super().keyPressEvent(event)
