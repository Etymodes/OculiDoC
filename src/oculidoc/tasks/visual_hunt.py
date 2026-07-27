"""Pure trial generation and descriptive summaries for visual treasure hunt."""

from __future__ import annotations

import random
import secrets
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from math import hypot, pi, sin, sqrt
from statistics import median
from time import monotonic_ns

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from oculidoc.devices.contracts import EyeTrackerSample
from oculidoc.image_library import (
    ImageAsset,
    ImageLibraryStore,
    asset_preview_pixmap,
)
from oculidoc.tasks.tracking_dwell import DwellPhase, TrackingDwellController


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


def _string_tuple(name: str, values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError(f"{name} cannot contain empty values.")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} cannot contain duplicates.")
    return normalized


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _median(values: Iterable[float]) -> float | None:
    materialized = tuple(values)
    return float(median(materialized)) if materialized else None


@dataclass(frozen=True, slots=True)
class VisualHuntConfig:
    preview_trial_count: int = 6
    popout_trial_count: int = 4
    catch_trial_count: int = 2
    distractor_count: int = 3
    target_preview_ms: int = 1500
    interstimulus_ms: int = 500
    dwell_time_ms: int = 800
    trial_duration_seconds: int = 12
    reward_animation_ms: int = 1000
    sound_enabled: bool = True
    show_gaze_cursor: bool = False
    randomize_trial_order: bool = True
    randomization_seed: int | None = None
    category_filters: tuple[str, ...] = ()
    style_filters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "category_filters",
            _string_tuple("category_filters", self.category_filters),
        )
        object.__setattr__(
            self,
            "style_filters",
            _string_tuple("style_filters", self.style_filters),
        )
        _bounded_int("preview_trial_count", self.preview_trial_count, 0, 30)
        _bounded_int("popout_trial_count", self.popout_trial_count, 0, 30)
        _bounded_int("catch_trial_count", self.catch_trial_count, 0, 10)
        _bounded_int("distractor_count", self.distractor_count, 1, 5)
        _bounded_int("target_preview_ms", self.target_preview_ms, 500, 5000)
        _bounded_int("interstimulus_ms", self.interstimulus_ms, 250, 2000)
        _bounded_int("dwell_time_ms", self.dwell_time_ms, 250, 5000)
        _bounded_int("trial_duration_seconds", self.trial_duration_seconds, 3, 60)
        _bounded_int("reward_animation_ms", self.reward_animation_ms, 500, 3000)
        if not isinstance(self.sound_enabled, bool):
            raise TypeError("sound_enabled must be a boolean.")
        if not isinstance(self.show_gaze_cursor, bool):
            raise TypeError("show_gaze_cursor must be a boolean.")
        if not isinstance(self.randomize_trial_order, bool):
            raise TypeError("randomize_trial_order must be a boolean.")
        _optional_seed(self.randomization_seed)

        if self.trial_count == 0:
            raise ValueError("At least one visual-hunt trial is required.")
        if self.catch_trial_count / self.trial_count > 0.40:
            raise ValueError("Catch trials cannot exceed 40 percent of the protocol.")

    @property
    def trial_count(self) -> int:
        return self.preview_trial_count + self.popout_trial_count + self.catch_trial_count


class VisualHuntCondition(StrEnum):
    PREVIEW_SEARCH = "preview_search"
    POPOUT = "popout"
    CATCH = "catch"


@dataclass(frozen=True, slots=True)
class VisualHuntStimulus:
    resource_id: str
    label: str

    def __post_init__(self) -> None:
        if not self.resource_id.strip():
            raise ValueError("resource_id cannot be empty.")
        if not self.label.strip():
            raise ValueError("label cannot be empty.")


@dataclass(frozen=True, slots=True)
class VisualHuntTrial:
    trial_id: str
    trial_number: int
    trial_count: int
    condition: VisualHuntCondition
    target_stimulus_id: str
    target_label: str
    array_stimulus_ids: tuple[str, ...]
    array_labels: tuple[str, ...]
    target_position: int | None

    @property
    def target_present(self) -> bool:
        return self.target_position is not None


@dataclass(frozen=True, slots=True)
class VisualHuntProtocol:
    randomization_seed: int
    trials: tuple[VisualHuntTrial, ...]


def _stimuli_by_label(
    stimuli: Iterable[VisualHuntStimulus],
) -> dict[str, tuple[VisualHuntStimulus, ...]]:
    grouped: dict[str, list[VisualHuntStimulus]] = {}
    resource_ids: set[str] = set()

    for stimulus in stimuli:
        if stimulus.resource_id in resource_ids:
            raise ValueError("Visual-hunt resource IDs must be unique.")
        resource_ids.add(stimulus.resource_id)
        grouped.setdefault(stimulus.label, []).append(stimulus)

    return {label: tuple(items) for label, items in grouped.items()}


def visual_hunt_protocol(
    config: VisualHuntConfig,
    stimuli: Iterable[VisualHuntStimulus],
) -> VisualHuntProtocol:
    """Create reproducible target-present, popout, and target-absent trials."""
    grouped = _stimuli_by_label(stimuli)
    required_labels = config.distractor_count + 1
    if len(grouped) < required_labels:
        raise ValueError(f"Visual hunt needs at least {required_labels} different image labels.")

    seed = (
        config.randomization_seed if config.randomization_seed is not None else secrets.randbits(32)
    )
    rng = random.Random(seed)
    conditions = (
        [VisualHuntCondition.PREVIEW_SEARCH] * config.preview_trial_count
        + [VisualHuntCondition.POPOUT] * config.popout_trial_count
        + [VisualHuntCondition.CATCH] * config.catch_trial_count
    )
    if config.randomize_trial_order:
        rng.shuffle(conditions)

    labels = tuple(grouped)
    target_labels: list[str] = []
    target_positions: list[int] = []

    def next_target_label() -> str:
        nonlocal target_labels
        if not target_labels:
            target_labels = list(labels)
            rng.shuffle(target_labels)
        return target_labels.pop()

    def next_target_position() -> int:
        nonlocal target_positions
        if not target_positions:
            target_positions = list(range(config.distractor_count + 1))
            rng.shuffle(target_positions)
        return target_positions.pop()

    trials: list[VisualHuntTrial] = []
    for index, condition in enumerate(conditions):
        target_label = next_target_label()
        target_stimulus = rng.choice(grouped[target_label])
        distractor_labels = rng.sample(
            [label for label in labels if label != target_label],
            config.distractor_count,
        )
        distractors = [rng.choice(grouped[label]) for label in distractor_labels]

        if condition is VisualHuntCondition.CATCH:
            array = distractors
            target_position = None
            rng.shuffle(array)
        else:
            target_position = next_target_position()
            array = distractors
            array.insert(target_position, target_stimulus)

        trials.append(
            VisualHuntTrial(
                trial_id=f"hunt-{index + 1:03d}-{condition.value}",
                trial_number=index + 1,
                trial_count=len(conditions),
                condition=condition,
                target_stimulus_id=target_stimulus.resource_id,
                target_label=target_label,
                array_stimulus_ids=tuple(item.resource_id for item in array),
                array_labels=tuple(item.label for item in array),
                target_position=target_position,
            )
        )

    return VisualHuntProtocol(
        randomization_seed=seed,
        trials=tuple(trials),
    )


@dataclass(frozen=True, slots=True)
class VisualHuntTrialObservation:
    condition: VisualHuntCondition
    target_present: bool
    sample_count: int
    valid_sample_count: int
    target_acquired: bool = False
    first_target_entry_ms: float | None = None
    target_acquisition_ms: float | None = None
    longest_target_dwell_ms: float = 0.0
    distractor_dwell_ms: float = 0.0
    array_valid_duration_ms: float = 0.0
    wrong_dwell_count: int = 0
    aoi_visits_before_target: int | None = None
    normalized_scanpath_length: float | None = None
    target_field: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "condition", VisualHuntCondition(self.condition))
        _bounded_int("sample_count", self.sample_count, 0, 2_000_000_000)
        _bounded_int(
            "valid_sample_count",
            self.valid_sample_count,
            0,
            self.sample_count,
        )
        _bounded_int("wrong_dwell_count", self.wrong_dwell_count, 0, 2_000_000_000)
        if self.aoi_visits_before_target is not None:
            _bounded_int(
                "aoi_visits_before_target",
                self.aoi_visits_before_target,
                0,
                2_000_000_000,
            )
        durations = (
            self.first_target_entry_ms,
            self.target_acquisition_ms,
            self.longest_target_dwell_ms,
            self.distractor_dwell_ms,
            self.array_valid_duration_ms,
            self.normalized_scanpath_length,
        )
        if any(value is not None and value < 0 for value in durations):
            raise ValueError("Visual-hunt measurements cannot be negative.")
        if self.condition is VisualHuntCondition.CATCH and self.target_present:
            raise ValueError("Catch observations cannot contain a target.")
        if not self.target_present and self.target_acquired:
            raise ValueError("A missing target cannot be acquired.")


def summarize_visual_hunt_observations(
    observations: Iterable[VisualHuntTrialObservation],
) -> dict[str, object]:
    """Aggregate all trials while retaining failures and catch false selections."""
    trials = tuple(observations)
    target_present = tuple(trial for trial in trials if trial.target_present)
    successful = tuple(trial for trial in target_present if trial.target_acquired)
    catch = tuple(trial for trial in trials if trial.condition is VisualHuntCondition.CATCH)
    sample_count = sum(trial.sample_count for trial in trials)
    valid_sample_count = sum(trial.valid_sample_count for trial in trials)

    acquisition_by_condition = {
        condition.value: _ratio(
            sum(trial.target_acquired for trial in target_present if trial.condition is condition),
            sum(1 for trial in target_present if trial.condition is condition),
        )
        for condition in (
            VisualHuntCondition.PREVIEW_SEARCH,
            VisualHuntCondition.POPOUT,
        )
    }
    field_trials = tuple(trial for trial in target_present if trial.target_field is not None)
    field_hits = Counter(trial.target_field for trial in field_trials if trial.target_acquired)
    field_counts = Counter(trial.target_field for trial in field_trials)

    return {
        "trial_count": len(trials),
        "target_present_trial_count": len(target_present),
        "successful_trial_count": len(successful),
        "failed_or_timeout_trial_count": len(target_present) - len(successful),
        "valid_sample_ratio": _ratio(valid_sample_count, sample_count),
        "target_acquisition_ratio": _ratio(len(successful), len(target_present)),
        "target_acquisition_ratio_by_condition": acquisition_by_condition,
        "median_first_target_entry_ms": _median(
            trial.first_target_entry_ms
            for trial in target_present
            if trial.first_target_entry_ms is not None
        ),
        "median_target_acquisition_ms": _median(
            trial.target_acquisition_ms
            for trial in successful
            if trial.target_acquisition_ms is not None
        ),
        "target_acquisition_latency_denominator": sum(
            trial.target_acquisition_ms is not None for trial in successful
        ),
        "longest_target_dwell_ms": max(
            (trial.longest_target_dwell_ms for trial in target_present),
            default=None,
        ),
        "distractor_dwell_ratio": _ratio(
            sum(trial.distractor_dwell_ms for trial in trials),
            sum(trial.array_valid_duration_ms for trial in trials),
        ),
        "wrong_dwell_count": sum(trial.wrong_dwell_count for trial in trials),
        "median_aoi_visits_before_target": _median(
            trial.aoi_visits_before_target
            for trial in successful
            if trial.aoi_visits_before_target is not None
        ),
        "median_normalized_scanpath_length": _median(
            trial.normalized_scanpath_length
            for trial in trials
            if trial.normalized_scanpath_length is not None
        ),
        "catch_false_selection_ratio": _ratio(
            sum(trial.wrong_dwell_count > 0 for trial in catch),
            len(catch),
        ),
        "field_hit_ratio": {
            str(field): _ratio(field_hits[field], field_counts[field])
            for field in sorted(field_counts, key=str)
        },
        "interpretation": "descriptive_visual_search_observation_only",
    }


def eligible_visual_hunt_assets(
    config: VisualHuntConfig,
    store: ImageLibraryStore,
) -> tuple[ImageAsset, ...]:
    """Return the existing-library assets eligible for one hunt protocol."""
    categories = set(config.category_filters)
    styles = set(config.style_filters)
    assets = tuple(
        asset
        for asset in store.load()
        if (not categories or asset.category in categories)
        and (not styles or asset.style in styles)
    )
    required_labels = config.distractor_count + 1

    if len({asset.label.casefold() for asset in assets}) < required_labels:
        raise ValueError(f"视觉寻宝至少需要 {required_labels} 个名称不同的可用图片。")

    return assets


class VisualHuntPhase(StrEnum):
    READY = "ready"
    EXAMPLE = "example"
    PREVIEW = "preview"
    INTERVAL = "interval"
    ARRAY = "array"
    REWARD = "reward"
    COMPLETED = "completed"


class VisualHuntTask(QWidget):
    """Render visual-search trials and expose the existing task-recording protocol."""

    protocol_completed = Signal()
    speech_requested = Signal(str)

    def __init__(
        self,
        config: VisualHuntConfig,
        store: ImageLibraryStore,
        *,
        assets: Iterable[ImageAsset] | None = None,
        allow_mouse_fallback: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.image_store = store
        eligible = (
            tuple(assets)
            if assets is not None
            else eligible_visual_hunt_assets(
                config,
                store,
            )
        )
        self._assets = {asset.image_id: asset for asset in eligible}
        self.protocol = visual_hunt_protocol(
            config,
            (VisualHuntStimulus(asset.image_id, asset.label) for asset in eligible),
        )
        self.allow_mouse_fallback = allow_mouse_fallback
        self.setMinimumSize(800, 560)
        self.setMouseTracking(allow_mouse_fallback)

        if not allow_mouse_fallback:
            self.setCursor(Qt.CursorShape.BlankCursor)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.advance_time)
        self._dwell = TrackingDwellController(
            dwell_time_ms=config.dwell_time_ms,
            dropout_grace_ms=0,
        )
        self._pixmap_cache: dict[tuple[str, int], QPixmap] = {}
        self._reset_run_state()

    def _reset_run_state(self) -> None:
        self._running = False
        self._protocol_finished = False
        self._phase = VisualHuntPhase.READY
        self._phase_started_ns: int | None = None
        self._phase_deadline_ns: int | None = None
        self._trial_index = 0
        self._recording_events: list[dict[str, object]] = []
        self._trial_observations: list[VisualHuntTrialObservation] = []
        self._result_cache: dict[str, object] | None = None
        self._star_count = 0
        self._last_gaze_normalized: tuple[float, float] | None = None
        self._reset_trial_measurements()

    def _reset_trial_measurements(self) -> None:
        self._array_started_ns: int | None = None
        self._last_sample_timestamp_ns: int | None = None
        self._previous_valid = False
        self._previous_index: int | None = None
        self._last_scan_gaze: tuple[float, float] | None = None
        self._current_target_run_ms = 0.0
        self._sample_count = 0
        self._valid_sample_count = 0
        self._array_valid_duration_ms = 0.0
        self._target_dwell_ms = 0.0
        self._distractor_dwell_ms = 0.0
        self._longest_target_dwell_ms = 0.0
        self._first_target_entry_ms: float | None = None
        self._target_acquisition_ms: float | None = None
        self._target_acquired = False
        self._wrong_dwell_count = 0
        self._visited_indices_before_target: set[int] = set()
        self._scanpath_length = 0.0
        self._dwell_index: int | None = None
        self._dwell_latched_index: int | None = None
        self._dwell.reset()

    @property
    def phase(self) -> VisualHuntPhase:
        return self._phase

    @property
    def phase_deadline_ns(self) -> int | None:
        return self._phase_deadline_ns

    @property
    def current_trial(self) -> VisualHuntTrial:
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

    def _trial_payload(self, trial: VisualHuntTrial | None = None) -> dict[str, object]:
        active = trial or self.current_trial
        rectangles = self.array_rectangles_normalized(active)
        return {
            "game_mode": "treasure_hunt",
            "trial_id": active.trial_id,
            "trial_number": active.trial_number,
            "trial_count": active.trial_count,
            "condition": active.condition.value,
            "target_stimulus_id": active.target_stimulus_id,
            "target_label": active.target_label,
            "target_present": active.target_present,
            "target_position": active.target_position,
            "array_stimulus_ids": list(active.array_stimulus_ids),
            "array_labels": list(active.array_labels),
            "array_layout": [
                {
                    "position": index,
                    "stimulus_id": active.array_stimulus_ids[index],
                    "label": active.array_labels[index],
                    "left": rectangle.left(),
                    "top": rectangle.top(),
                    "right": rectangle.right(),
                    "bottom": rectangle.bottom(),
                }
                for index, rectangle in enumerate(rectangles)
            ],
            "randomization_seed": self.protocol.randomization_seed,
        }

    def _set_phase(
        self,
        phase: VisualHuntPhase,
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
                "game_mode": "treasure_hunt",
                "trial_count": len(self.protocol.trials),
                "randomization_seed": self.protocol.randomization_seed,
            },
        )
        self._queue_event(
            "example_presented",
            timestamp_ns=started_ns,
            payload={
                "game_mode": "treasure_hunt",
                "stimulus_id": self.protocol.trials[0].target_stimulus_id,
                "counted": False,
                "randomization_seed": self.protocol.randomization_seed,
            },
        )

        if self.config.sound_enabled:
            self.speech_requested.emit("先看看宝物，再找一找")

        self._set_phase(
            VisualHuntPhase.EXAMPLE,
            started_ns,
            self.config.target_preview_ms,
        )
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._running = False

    def _begin_trial(self, timestamp_ns: int) -> None:
        self._reset_trial_measurements()
        trial = self.current_trial
        self._queue_event(
            "trial_started",
            timestamp_ns=timestamp_ns,
            payload=self._trial_payload(trial),
        )

        if trial.condition is VisualHuntCondition.POPOUT:
            self._set_phase(
                VisualHuntPhase.INTERVAL,
                timestamp_ns,
                self.config.interstimulus_ms,
            )
            return

        self._queue_event(
            "target_previewed",
            timestamp_ns=timestamp_ns,
            payload=self._trial_payload(trial),
        )

        if self.config.sound_enabled:
            self.speech_requested.emit("找一找这个")

        self._set_phase(
            VisualHuntPhase.PREVIEW,
            timestamp_ns,
            self.config.target_preview_ms,
        )

    def _begin_array(self, timestamp_ns: int) -> None:
        self._array_started_ns = int(timestamp_ns)
        self._queue_event(
            "array_presented",
            timestamp_ns=timestamp_ns,
            payload={
                **self._trial_payload(),
                "layout": [
                    {
                        "position": index,
                        "stimulus_id": stimulus_id,
                        "label": self.current_trial.array_labels[index],
                    }
                    for index, stimulus_id in enumerate(self.current_trial.array_stimulus_ids)
                ],
            },
        )
        self._set_phase(
            VisualHuntPhase.ARRAY,
            timestamp_ns,
            self.config.trial_duration_seconds * 1000,
        )

    def _target_field(self) -> str | None:
        position = self.current_trial.target_position

        if position is None:
            return None

        rectangles = self.array_rectangles_normalized()
        center = rectangles[position].center()
        horizontal = "left" if center.x() < 0.5 else "right"
        vertical = "top" if center.y() < 0.5 else "bottom"
        return f"{horizontal}_{vertical}"

    def _current_observation(self) -> VisualHuntTrialObservation:
        return VisualHuntTrialObservation(
            condition=self.current_trial.condition,
            target_present=self.current_trial.target_present,
            sample_count=self._sample_count,
            valid_sample_count=self._valid_sample_count,
            target_acquired=self._target_acquired,
            first_target_entry_ms=self._first_target_entry_ms,
            target_acquisition_ms=self._target_acquisition_ms,
            longest_target_dwell_ms=max(
                self._longest_target_dwell_ms,
                self._current_target_run_ms,
            ),
            distractor_dwell_ms=self._distractor_dwell_ms,
            array_valid_duration_ms=self._array_valid_duration_ms,
            wrong_dwell_count=self._wrong_dwell_count,
            aoi_visits_before_target=(
                len(self._visited_indices_before_target)
                if self.current_trial.target_present
                else None
            ),
            normalized_scanpath_length=self._scanpath_length,
            target_field=self._target_field(),
        )

    def _finish_trial(self, timestamp_ns: int, *, reason: str) -> None:
        observation = self._current_observation()
        self._trial_observations.append(observation)
        self._queue_event(
            "trial_finished",
            timestamp_ns=timestamp_ns,
            payload={
                **self._trial_payload(),
                "reason": reason,
                "target_acquired": observation.target_acquired,
                "sample_count": observation.sample_count,
                "valid_sample_count": observation.valid_sample_count,
                "wrong_dwell_count": observation.wrong_dwell_count,
            },
        )

        if self._trial_index + 1 >= len(self.protocol.trials):
            self._finish_protocol(timestamp_ns)
            return

        self._trial_index += 1
        self._begin_trial(timestamp_ns)

    def _finish_protocol(self, timestamp_ns: int) -> None:
        if self._protocol_finished:
            return

        self._protocol_finished = True
        self._running = False
        self._timer.stop()
        self._set_phase(VisualHuntPhase.COMPLETED, timestamp_ns, None)
        self._queue_event(
            "protocol_finished",
            timestamp_ns=timestamp_ns,
            payload={
                "game_mode": "treasure_hunt",
                "trial_count": len(self._trial_observations),
                "star_count": self._star_count,
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

            if self._phase is VisualHuntPhase.EXAMPLE:
                self._begin_trial(boundary_ns)
            elif self._phase is VisualHuntPhase.PREVIEW:
                self._set_phase(
                    VisualHuntPhase.INTERVAL,
                    boundary_ns,
                    self.config.interstimulus_ms,
                )
            elif self._phase is VisualHuntPhase.INTERVAL:
                self._begin_array(boundary_ns)
            elif self._phase is VisualHuntPhase.ARRAY:
                self._finish_trial(boundary_ns, reason="trial_timeout")
            elif self._phase is VisualHuntPhase.REWARD:
                self._finish_trial(boundary_ns, reason="target_acquired")
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

    def array_rectangles_normalized(
        self,
        trial: VisualHuntTrial | None = None,
    ) -> tuple[QRectF, ...]:
        active = trial or self.current_trial
        count = len(active.array_stimulus_ids)

        if count == 1:
            return (QRectF(0.31, 0.22, 0.38, 0.60),)

        columns = 2 if count <= 4 else 3
        rows = (count + columns - 1) // columns
        top = 0.12
        width = 0.88
        height = 0.80
        gap_x = 0.035
        gap_y = 0.045
        cell_width = (width - gap_x * (columns - 1)) / columns
        cell_height = (height - gap_y * (rows - 1)) / rows
        rectangles: list[QRectF] = []

        for index in range(count):
            row = index // columns
            column = index % columns
            used_columns = min(columns, count - row * columns)
            row_width = used_columns * cell_width + max(0, used_columns - 1) * gap_x
            row_left = 0.5 - row_width / 2.0
            rectangles.append(
                QRectF(
                    row_left + column * (cell_width + gap_x),
                    top + row * (cell_height + gap_y),
                    cell_width,
                    cell_height,
                )
            )

        return tuple(rectangles)

    def _index_at(self, x: float, y: float) -> int | None:
        point = QPointF(x, y)

        for index, rectangle in enumerate(self.array_rectangles_normalized()):
            if rectangle.contains(point):
                return index

        return None

    def _advance_previous_interval(self, timestamp_ns: int) -> None:
        previous_timestamp_ns = self._last_sample_timestamp_ns

        if previous_timestamp_ns is None or timestamp_ns <= previous_timestamp_ns:
            return

        delta_ms = min(250.0, (timestamp_ns - previous_timestamp_ns) / 1_000_000.0)

        if not self._previous_valid:
            return

        self._array_valid_duration_ms += delta_ms

        if self._previous_index is None:
            return

        if self._previous_index == self.current_trial.target_position:
            self._target_dwell_ms += delta_ms
            self._current_target_run_ms += delta_ms
            self._longest_target_dwell_ms = max(
                self._longest_target_dwell_ms,
                self._current_target_run_ms,
            )
        else:
            self._distractor_dwell_ms += delta_ms

    def _leave_index(self) -> None:
        if self._previous_index == self.current_trial.target_position:
            self._longest_target_dwell_ms = max(
                self._longest_target_dwell_ms,
                self._current_target_run_ms,
            )
            self._current_target_run_ms = 0.0

    def _acquire_target(self, timestamp_ns: int) -> None:
        if self._target_acquired or self._array_started_ns is None:
            return

        self._target_acquired = True
        self._target_acquisition_ms = max(
            0.0,
            (timestamp_ns - self._array_started_ns) / 1_000_000.0,
        )
        self._star_count += 1
        self._queue_event(
            "target_acquired",
            timestamp_ns=timestamp_ns,
            payload={
                **self._trial_payload(),
                "target_acquisition_ms": self._target_acquisition_ms,
                "dwell_threshold_ms": self.config.dwell_time_ms,
            },
        )
        self._queue_event(
            "reward_presented",
            timestamp_ns=timestamp_ns,
            payload={
                **self._trial_payload(),
                "reward_animation_ms": self.config.reward_animation_ms,
            },
        )

        if self.config.sound_enabled:
            self.speech_requested.emit("找到了")

        self._set_phase(
            VisualHuntPhase.REWARD,
            timestamp_ns,
            self.config.reward_animation_ms,
        )

    def _record_wrong_dwell(self, index: int, timestamp_ns: int) -> None:
        self._wrong_dwell_count += 1
        self._queue_event(
            "distractor_dwell",
            timestamp_ns=timestamp_ns,
            payload={
                **self._trial_payload(),
                "aoi_id": f"hunt-option-{index}",
                "position": index,
                "stimulus_id": self.current_trial.array_stimulus_ids[index],
                "dwell_threshold_ms": self.config.dwell_time_ms,
            },
        )

        if not self.current_trial.target_present:
            self._queue_event(
                "catch_false_selection",
                timestamp_ns=timestamp_ns,
                payload={
                    **self._trial_payload(),
                    "aoi_id": f"hunt-option-{index}",
                    "position": index,
                    "stimulus_id": self.current_trial.array_stimulus_ids[index],
                },
            )

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        if not self._running or self._phase is not VisualHuntPhase.ARRAY:
            return

        timestamp_ns = sample.timestamp.monotonic_timestamp_ns
        self.advance_time(timestamp_ns)

        if not self._running or self._phase is not VisualHuntPhase.ARRAY:
            return

        self._advance_previous_interval(timestamp_ns)
        self._sample_count += 1
        gaze_x_value = sample.gaze_x_normalized
        gaze_y_value = sample.gaze_y_normalized
        valid = bool(sample.gaze_valid and gaze_x_value is not None and gaze_y_value is not None)

        if not valid:
            self._leave_index()
            self._previous_valid = False
            self._previous_index = None
            self._last_scan_gaze = None
            self._last_gaze_normalized = None
            self._last_sample_timestamp_ns = timestamp_ns
            self._dwell_index = None
            self._dwell_latched_index = None
            self._dwell.reset()
            self.update()
            return

        assert gaze_x_value is not None
        assert gaze_y_value is not None
        gaze_x = max(0.0, min(1.0, float(gaze_x_value)))
        gaze_y = max(0.0, min(1.0, float(gaze_y_value)))
        index = self._index_at(gaze_x, gaze_y)
        self._valid_sample_count += 1
        self._last_gaze_normalized = (gaze_x, gaze_y)

        if self._last_scan_gaze is not None:
            previous_x, previous_y = self._last_scan_gaze
            self._scanpath_length += hypot(
                gaze_x - previous_x,
                gaze_y - previous_y,
            ) / sqrt(2.0)

        self._last_scan_gaze = (gaze_x, gaze_y)

        if index != self._previous_index:
            self._leave_index()
            self._dwell.reset()
            self._dwell_index = index
            self._dwell_latched_index = None

            if index is not None:
                if index != self.current_trial.target_position:
                    self._visited_indices_before_target.add(index)

                if (
                    index == self.current_trial.target_position
                    and self._first_target_entry_ms is None
                    and self._array_started_ns is not None
                ):
                    self._first_target_entry_ms = max(
                        0.0,
                        (timestamp_ns - self._array_started_ns) / 1_000_000.0,
                    )
                    self._queue_event(
                        "target_entered",
                        timestamp_ns=timestamp_ns,
                        payload={
                            **self._trial_payload(),
                            "position": index,
                            "first_target_entry_ms": self._first_target_entry_ms,
                        },
                    )

        self._previous_valid = True
        self._previous_index = index
        self._last_sample_timestamp_ns = timestamp_ns

        if index is None:
            self._dwell.reset()
            self._dwell_index = None
            self.update()
            return

        if self._dwell_index != index:
            self._dwell.reset()
            self._dwell_index = index
            self._dwell_latched_index = None

        snapshot = self._dwell.observe(True, timestamp_ns)

        if snapshot.phase is DwellPhase.MAINTAINED and self._dwell_latched_index != index:
            self._dwell_latched_index = index

            if index == self.current_trial.target_position:
                self._acquire_target(timestamp_ns)
            else:
                self._record_wrong_dwell(index, timestamp_ns)

        self.update()

    def _array_aois(self) -> tuple[dict[str, object], ...]:
        trial = self.current_trial
        aois: list[dict[str, object]] = []

        for index, rectangle in enumerate(self.array_rectangles_normalized()):
            target = trial.target_position == index
            aois.append(
                {
                    "aoi_id": f"hunt-option-{index}",
                    "role": "correct_option" if target else "incorrect_option",
                    "left": rectangle.left(),
                    "top": rectangle.top(),
                    "right": rectangle.right(),
                    "bottom": rectangle.bottom(),
                    "label": "hunt_target" if target else "hunt_distractor",
                    "metadata": {
                        "trial_id": trial.trial_id,
                        "condition": trial.condition.value,
                        "position": index,
                        "stimulus_id": trial.array_stimulus_ids[index],
                        "stimulus_label": trial.array_labels[index],
                        "target_present": trial.target_present,
                    },
                }
            )

        aois.append(
            {
                "aoi_id": "hunt-background",
                "role": "other",
                "left": 0.0,
                "top": 0.0,
                "right": 1.0,
                "bottom": 1.0,
                "label": "hunt_background",
                "metadata": {
                    "trial_id": trial.trial_id,
                    "condition": trial.condition.value,
                },
            }
        )
        return tuple(aois)

    def recording_context_for_sample(
        self,
        _sample: EyeTrackerSample,
    ) -> dict[str, object]:
        if self._phase is VisualHuntPhase.ARRAY:
            aois = self._array_aois()
        else:
            aois = (
                {
                    "aoi_id": "hunt-background",
                    "role": "other",
                    "left": 0.0,
                    "top": 0.0,
                    "right": 1.0,
                    "bottom": 1.0,
                    "label": "hunt_background",
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

        if not self._protocol_finished and self._phase in {
            VisualHuntPhase.ARRAY,
            VisualHuntPhase.REWARD,
        }:
            observations.append(self._current_observation())

        completed = self._protocol_finished and reason_text in {
            "completed",
            "protocol_completed",
            "test_complete",
        }
        result_trials: list[dict[str, object]] = []

        for index, observation in enumerate(observations):
            trial = self.protocol.trials[index]
            result_trials.append(
                {
                    **self._trial_payload(trial),
                    "sample_count": observation.sample_count,
                    "valid_sample_count": observation.valid_sample_count,
                    "target_acquired": observation.target_acquired,
                    "first_target_entry_ms": observation.first_target_entry_ms,
                    "target_acquisition_ms": observation.target_acquisition_ms,
                    "longest_target_dwell_ms": observation.longest_target_dwell_ms,
                    "distractor_dwell_ms": observation.distractor_dwell_ms,
                    "array_valid_duration_ms": observation.array_valid_duration_ms,
                    "wrong_dwell_count": observation.wrong_dwell_count,
                    "aoi_visits_before_target": observation.aoi_visits_before_target,
                    "normalized_scanpath_length": observation.normalized_scanpath_length,
                    "target_field": observation.target_field,
                }
            )

        result = {
            "task_kind": "gaze_games",
            "game_mode": "treasure_hunt",
            "completion_status": "completed" if completed else "interrupted",
            "completion_reason": reason_text,
            "randomization_seed": self.protocol.randomization_seed,
            "configuration": {
                "preview_trial_count": self.config.preview_trial_count,
                "popout_trial_count": self.config.popout_trial_count,
                "catch_trial_count": self.config.catch_trial_count,
                "distractor_count": self.config.distractor_count,
                "target_preview_ms": self.config.target_preview_ms,
                "interstimulus_ms": self.config.interstimulus_ms,
                "dwell_time_ms": self.config.dwell_time_ms,
                "trial_duration_seconds": self.config.trial_duration_seconds,
                "reward_animation_ms": self.config.reward_animation_ms,
                "category_filters": list(self.config.category_filters),
                "style_filters": list(self.config.style_filters),
                "sound_enabled": self.config.sound_enabled,
                "show_gaze_cursor": self.config.show_gaze_cursor,
            },
            "star_count": self._star_count,
            "trials": result_trials,
            **summarize_visual_hunt_observations(observations),
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
        *,
        scale: float = 1.0,
        rewarded: bool = False,
    ) -> None:
        scaled = QRectF(rectangle)

        if scale != 1.0:
            center = scaled.center()
            scaled.setWidth(scaled.width() * scale)
            scaled.setHeight(scaled.height() * scale)
            scaled.moveCenter(center)

        painter.setBrush(QColor("#f7fbff"))
        painter.setPen(
            QPen(
                QColor("#ffd166" if rewarded else "#6f94ad"),
                8 if rewarded else 4,
            )
        )
        painter.drawRoundedRect(scaled, 24, 24)
        inset = scaled.adjusted(14, 14, -14, -14)
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

        if self._phase is VisualHuntPhase.COMPLETED:
            painter.setPen(QColor("#fff4be"))
            font = painter.font()
            font.setFamily("Microsoft YaHei UI")
            font.setPointSize(38)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                f"寻宝结束\n收集 {self._star_count} 颗星",
            )
            painter.end()
            return

        if self._phase in {
            VisualHuntPhase.EXAMPLE,
            VisualHuntPhase.PREVIEW,
        }:
            image_id = (
                self.protocol.trials[0].target_stimulus_id
                if self._phase is VisualHuntPhase.EXAMPLE
                else self.current_trial.target_stimulus_id
            )
            rectangle = QRectF(
                self.width() * 0.31,
                self.height() * 0.18,
                self.width() * 0.38,
                self.height() * 0.64,
            )
            self._draw_asset(painter, image_id, rectangle)
        elif self._phase is VisualHuntPhase.INTERVAL:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#d8f3dc"))
            painter.drawEllipse(
                QPointF(self.width() / 2.0, self.height() / 2.0),
                18,
                18,
            )
        elif self._phase in {
            VisualHuntPhase.ARRAY,
            VisualHuntPhase.REWARD,
        }:
            target_position = self.current_trial.target_position
            now_ns = monotonic_ns()

            for index, normalized in enumerate(self.array_rectangles_normalized()):
                scale = 1.0

                if (
                    self._phase is VisualHuntPhase.ARRAY
                    and self.current_trial.condition is VisualHuntCondition.POPOUT
                    and index == target_position
                ):
                    scale = 1.0 + 0.03 * sin(now_ns / 1_000_000_000.0 * 2.0 * pi)

                self._draw_asset(
                    painter,
                    self.current_trial.array_stimulus_ids[index],
                    self._pixel_rectangle(normalized),
                    scale=scale,
                    rewarded=(self._phase is VisualHuntPhase.REWARD and index == target_position),
                )

            if self._phase is VisualHuntPhase.REWARD and target_position is not None:
                target_rectangle = self._pixel_rectangle(
                    self.array_rectangles_normalized()[target_position]
                )
                painter.setPen(QColor("#fff4a8"))
                font = painter.font()
                font.setPointSize(48)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(
                    target_rectangle,
                    Qt.AlignmentFlag.AlignCenter,
                    "★",
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
