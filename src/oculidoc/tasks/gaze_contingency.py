"""Pure protocol logic for the gaze-contingent garden task."""

from __future__ import annotations

import random
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from math import cos, pi, sin
from statistics import median
from time import monotonic_ns

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from oculidoc.devices.contracts import EyeTrackerSample


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


@dataclass(frozen=True, slots=True)
class GazeContingencyConfig:
    """Versioned settings for the fixed garden protocol."""

    object_count: int = 4
    object_diameter_px: int = 280
    dwell_time_ms: int = 800
    baseline_seconds: int = 8
    contingent_block_seconds: int = 30
    replay_block_seconds: int = 30
    reward_animation_ms: int = 1200
    sound_enabled: bool = True
    show_gaze_cursor: bool = False
    randomization_seed: int | None = None

    def __post_init__(self) -> None:
        _bounded_int("object_count", self.object_count, 2, 6)
        _bounded_int("object_diameter_px", self.object_diameter_px, 160, 480)
        _bounded_int("dwell_time_ms", self.dwell_time_ms, 250, 3000)
        _bounded_int("baseline_seconds", self.baseline_seconds, 5, 30)
        _bounded_int(
            "contingent_block_seconds",
            self.contingent_block_seconds,
            10,
            120,
        )
        _bounded_int("replay_block_seconds", self.replay_block_seconds, 10, 120)
        _bounded_int("reward_animation_ms", self.reward_animation_ms, 500, 3000)
        if not isinstance(self.sound_enabled, bool):
            raise TypeError("sound_enabled must be a boolean.")
        if not isinstance(self.show_gaze_cursor, bool):
            raise TypeError("show_gaze_cursor must be a boolean.")
        _optional_seed(self.randomization_seed)


class GardenBlockType(StrEnum):
    BASELINE = "baseline"
    CONTINGENT_1 = "contingent_1"
    REPLAY = "replay"
    CONTINGENT_2 = "contingent_2"


@dataclass(frozen=True, slots=True)
class GardenObject:
    object_id: str
    layout_slot: int
    x_normalized: float
    y_normalized: float


@dataclass(frozen=True, slots=True)
class GardenBlock:
    block_index: int
    block_type: GardenBlockType
    duration_ms: int
    gaze_contingent: bool


@dataclass(frozen=True, slots=True)
class GardenProtocol:
    randomization_seed: int
    objects: tuple[GardenObject, ...]
    blocks: tuple[GardenBlock, ...]


_LAYOUTS: dict[int, tuple[tuple[float, float], ...]] = {
    2: ((0.32, 0.55), (0.68, 0.55)),
    3: ((0.25, 0.60), (0.50, 0.32), (0.75, 0.60)),
    4: ((0.30, 0.30), (0.70, 0.30), (0.30, 0.70), (0.70, 0.70)),
    5: (
        (0.22, 0.30),
        (0.50, 0.30),
        (0.78, 0.30),
        (0.34, 0.70),
        (0.66, 0.70),
    ),
    6: (
        (0.20, 0.30),
        (0.50, 0.30),
        (0.80, 0.30),
        (0.20, 0.70),
        (0.50, 0.70),
        (0.80, 0.70),
    ),
}


def garden_protocol(config: GazeContingencyConfig) -> GardenProtocol:
    """Build the reproducible layout and four fixed protocol blocks."""
    seed = (
        config.randomization_seed if config.randomization_seed is not None else secrets.randbits(32)
    )
    slots = list(_LAYOUTS[config.object_count])
    random.Random(seed).shuffle(slots)
    objects = tuple(
        GardenObject(
            object_id=f"flower-{index + 1:02d}",
            layout_slot=index,
            x_normalized=coordinates[0],
            y_normalized=coordinates[1],
        )
        for index, coordinates in enumerate(slots)
    )
    blocks = (
        GardenBlock(
            block_index=0,
            block_type=GardenBlockType.BASELINE,
            duration_ms=config.baseline_seconds * 1000,
            gaze_contingent=False,
        ),
        GardenBlock(
            block_index=1,
            block_type=GardenBlockType.CONTINGENT_1,
            duration_ms=config.contingent_block_seconds * 1000,
            gaze_contingent=True,
        ),
        GardenBlock(
            block_index=2,
            block_type=GardenBlockType.REPLAY,
            duration_ms=config.replay_block_seconds * 1000,
            gaze_contingent=False,
        ),
        GardenBlock(
            block_index=3,
            block_type=GardenBlockType.CONTINGENT_2,
            duration_ms=config.contingent_block_seconds * 1000,
            gaze_contingent=True,
        ),
    )
    return GardenProtocol(
        randomization_seed=seed,
        objects=objects,
        blocks=blocks,
    )


@dataclass(frozen=True, slots=True)
class GardenRewardEvent:
    object_id: str
    offset_ms: int

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise ValueError("object_id cannot be empty.")
        _bounded_int("offset_ms", self.offset_ms, 0, 120_000)


@dataclass(frozen=True, slots=True)
class GardenReplaySchedule:
    replay_source: str
    events: tuple[GardenRewardEvent, ...]


def garden_replay_schedule(
    config: GazeContingencyConfig,
    protocol: GardenProtocol,
    contingent_1_events: Iterable[GardenRewardEvent],
) -> GardenReplaySchedule:
    """Pair replay to block one, or create three explicitly marked fallback events."""
    object_ids = {item.object_id for item in protocol.objects}
    recorded = tuple(
        sorted(
            (
                event
                for event in contingent_1_events
                if event.object_id in object_ids
                and event.offset_ms <= config.contingent_block_seconds * 1000
            ),
            key=lambda event: event.offset_ms,
        )
    )
    replay_duration_ms = config.replay_block_seconds * 1000

    if len(recorded) >= 2:
        contingent_duration_ms = config.contingent_block_seconds * 1000
        events = tuple(
            GardenRewardEvent(
                object_id=event.object_id,
                offset_ms=min(
                    replay_duration_ms - 1,
                    round(event.offset_ms * replay_duration_ms / contingent_duration_ms),
                ),
            )
            for event in recorded
        )
        return GardenReplaySchedule(
            replay_source="recorded_contingent_1",
            events=events,
        )

    rng = random.Random(protocol.randomization_seed ^ 0x47415244)
    fractions = (0.22, 0.50, 0.78)
    fallback = tuple(
        GardenRewardEvent(
            object_id=rng.choice(protocol.objects).object_id,
            offset_ms=round(
                replay_duration_ms * max(0.10, min(0.90, fraction + rng.uniform(-0.05, 0.05)))
            ),
        )
        for fraction in fractions
    )
    return GardenReplaySchedule(
        replay_source="seeded_fallback",
        events=tuple(sorted(fallback, key=lambda event: event.offset_ms)),
    )


@dataclass(frozen=True, slots=True)
class GardenBlockObservation:
    """GUI-independent measurements collected for one completed or partial block."""

    block_type: GardenBlockType
    sample_count: int
    valid_sample_count: int
    valid_duration_ms: float
    target_dwell_ms: float
    activation_latencies_ms: tuple[float, ...] = ()
    entered_object_ids: tuple[str, ...] = ()
    loss_and_reacquisition_count: int = 0
    replay_reward_count: int = 0
    replay_rewards_on_target: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_type", GardenBlockType(self.block_type))
        _bounded_int("sample_count", self.sample_count, 0, 2_000_000_000)
        _bounded_int(
            "valid_sample_count",
            self.valid_sample_count,
            0,
            self.sample_count,
        )
        _bounded_int(
            "loss_and_reacquisition_count",
            self.loss_and_reacquisition_count,
            0,
            2_000_000_000,
        )
        _bounded_int(
            "replay_reward_count",
            self.replay_reward_count,
            0,
            2_000_000_000,
        )
        _bounded_int(
            "replay_rewards_on_target",
            self.replay_rewards_on_target,
            0,
            self.replay_reward_count,
        )
        if self.valid_duration_ms < 0 or self.target_dwell_ms < 0:
            raise ValueError("Durations cannot be negative.")
        if any(value < 0 for value in self.activation_latencies_ms):
            raise ValueError("Activation latencies cannot be negative.")


def summarize_garden_observations(
    config: GazeContingencyConfig,
    observations: Iterable[GardenBlockObservation],
) -> dict[str, object]:
    """Return descriptive garden metrics without deriving a consciousness score."""
    blocks = tuple(observations)
    contingent_1 = tuple(
        block for block in blocks if block.block_type is GardenBlockType.CONTINGENT_1
    )
    contingent_2 = tuple(
        block for block in blocks if block.block_type is GardenBlockType.CONTINGENT_2
    )
    contingent = contingent_1 + contingent_2
    replay = tuple(block for block in blocks if block.block_type is GardenBlockType.REPLAY)
    sample_count = sum(block.sample_count for block in blocks)
    valid_sample_count = sum(block.valid_sample_count for block in blocks)
    activation_count = sum(len(block.activation_latencies_ms) for block in contingent)
    contingent_valid_ms = sum(block.valid_duration_ms for block in contingent)
    explored = {
        object_id for block in blocks for object_id in block.entered_object_ids if object_id
    }
    c1_latency = _median(
        latency for block in contingent_1 for latency in block.activation_latencies_ms
    )
    c2_latency = _median(
        latency for block in contingent_2 for latency in block.activation_latencies_ms
    )
    replay_reward_count = sum(block.replay_reward_count for block in replay)
    replay_rewards_on_target = sum(block.replay_rewards_on_target for block in replay)

    return {
        "valid_sample_ratio": _ratio(valid_sample_count, sample_count),
        "aoi_exploration_coverage": min(1.0, len(explored) / config.object_count),
        "contingent_activation_count": activation_count,
        "activation_rate_per_valid_minute": _ratio(
            activation_count,
            contingent_valid_ms / 60_000.0,
        ),
        "median_activation_latency_ms_c1": c1_latency,
        "median_activation_latency_ms_c2": c2_latency,
        "latency_change_ms": (
            c2_latency - c1_latency if c1_latency is not None and c2_latency is not None else None
        ),
        "contingent_target_dwell_ratio": _ratio(
            sum(block.target_dwell_ms for block in contingent),
            contingent_valid_ms,
        ),
        "replay_target_dwell_ratio": _ratio(
            sum(block.target_dwell_ms for block in replay),
            sum(block.valid_duration_ms for block in replay),
        ),
        "replay_reward_on_target_ratio": _ratio(
            replay_rewards_on_target,
            replay_reward_count,
        ),
        "loss_and_reacquisition_count": sum(block.loss_and_reacquisition_count for block in blocks),
        "interpretation": "descriptive_gaze_contingency_observation_only",
    }


class GazeContingencyTask(QWidget):
    """Render and record the fixed contingent–replay–contingent garden protocol."""

    protocol_completed = Signal()
    speech_requested = Signal(str)

    def __init__(
        self,
        config: GazeContingencyConfig,
        *,
        allow_mouse_fallback: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.allow_mouse_fallback = allow_mouse_fallback
        self.protocol = garden_protocol(config)
        self.setMinimumSize(800, 560)
        self.setMouseTracking(allow_mouse_fallback)

        if not allow_mouse_fallback:
            self.setCursor(Qt.CursorShape.BlankCursor)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.advance_time)
        self._reset_run_state()

    def _reset_run_state(self) -> None:
        self._running = False
        self._protocol_finished = False
        self._block_index = 0
        self._block_started_ns: int | None = None
        self._block_deadline_ns: int | None = None
        self._last_sample_timestamp_ns: int | None = None
        self._previous_valid = False
        self._previous_object_id: str | None = None
        self._last_gaze_normalized: tuple[float, float] | None = None
        self._last_animation_timestamp_ns: int | None = None
        self._last_valid_gaze_timestamp_ns: int | None = None
        self._flower_open_progress = {item.object_id: 0.0 for item in self.protocol.objects}
        self._completed_flower_ids: set[str] = set()
        self._garden_goal_reached = False
        self._celebration_deadline_ns: int | None = None
        self._sample_count = 0
        self._valid_sample_count = 0
        self._valid_duration_ms = 0.0
        self._target_dwell_ms = 0.0
        self._activation_latencies_ms: list[float] = []
        self._entered_object_ids: set[str] = set()
        self._activated_object_ids: set[str] = set()
        self._objects_left_after_activation: set[str] = set()
        self._loss_and_reacquisition_count = 0
        self._replay_reward_count = 0
        self._replay_rewards_on_target = 0
        self._block_observations: list[GardenBlockObservation] = []
        self._contingent_1_rewards: list[GardenRewardEvent] = []
        self._replay_schedule: GardenReplaySchedule | None = None
        self._next_replay_event_index = 0
        self._reward_until_ns: dict[str, int] = {}
        self._reward_count = 0
        self._recording_events: list[dict[str, object]] = []
        self._result_cache: dict[str, object] | None = None

    @property
    def current_block(self) -> GardenBlock:
        return self.protocol.blocks[min(self._block_index, len(self.protocol.blocks) - 1)]

    @property
    def phase(self) -> str:
        if self._protocol_finished:
            return "completed"
        if self._garden_goal_reached:
            return "celebrating"
        return self.current_block.block_type.value

    @property
    def flower_open_progress(self) -> dict[str, float]:
        return dict(self._flower_open_progress)

    @property
    def completed_flower_ids(self) -> frozenset[str]:
        return frozenset(self._completed_flower_ids)

    @property
    def sky_brightness(self) -> float:
        return len(self._completed_flower_ids) / len(self.protocol.objects)

    @property
    def block_deadline_ns(self) -> int | None:
        return self._block_deadline_ns

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

    def _block_payload(self, block: GardenBlock | None = None) -> dict[str, object]:
        active = block or self.current_block
        return {
            "game_mode": "garden",
            "block_index": active.block_index,
            "block_type": active.block_type.value,
            "duration_ms": active.duration_ms,
            "gaze_contingent": active.gaze_contingent,
            "randomization_seed": self.protocol.randomization_seed,
        }

    def _reset_block_measurements(self) -> None:
        self._last_sample_timestamp_ns = None
        self._last_valid_gaze_timestamp_ns = None
        self._previous_valid = False
        self._previous_object_id = None
        self._sample_count = 0
        self._valid_sample_count = 0
        self._valid_duration_ms = 0.0
        self._target_dwell_ms = 0.0
        self._activation_latencies_ms = []
        self._entered_object_ids = set()
        self._activated_object_ids = set()
        self._objects_left_after_activation = set()
        self._loss_and_reacquisition_count = 0
        self._replay_reward_count = 0
        self._replay_rewards_on_target = 0
        self._next_replay_event_index = 0

    def _begin_block(self, index: int, timestamp_ns: int) -> None:
        self._block_index = index
        self._reset_block_measurements()
        block = self.current_block
        self._block_started_ns = int(timestamp_ns)
        self._block_deadline_ns = int(timestamp_ns) + block.duration_ms * 1_000_000
        self._last_animation_timestamp_ns = int(timestamp_ns)
        self._queue_event(
            "block_started",
            timestamp_ns=timestamp_ns,
            payload=self._block_payload(block),
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
                "game_mode": "garden",
                "randomization_seed": self.protocol.randomization_seed,
                "object_count": self.config.object_count,
            },
        )

        if self.config.sound_enabled:
            self.speech_requested.emit("看看花朵")

        self._begin_block(0, started_ns)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._running = False

    def _advance_flower_animation(self, timestamp_ns: int) -> None:
        previous_ns = self._last_animation_timestamp_ns
        self._last_animation_timestamp_ns = timestamp_ns
        if previous_ns is None or timestamp_ns <= previous_ns:
            return

        delta_ms = min(250.0, (timestamp_ns - previous_ns) / 1_000_000.0)
        gaze_is_fresh = (
            self._last_valid_gaze_timestamp_ns is not None
            and timestamp_ns - self._last_valid_gaze_timestamp_ns <= 250_000_000
        )
        opening_object_id = (
            self._previous_object_id
            if self.current_block.gaze_contingent and self._previous_valid and gaze_is_fresh
            else None
        )
        newly_completed: list[str] = []
        close_duration_ms = self.config.dwell_time_ms * 2.0

        for item in self.protocol.objects:
            object_id = item.object_id
            if object_id in self._completed_flower_ids:
                self._flower_open_progress[object_id] = 1.0
                continue
            current = self._flower_open_progress[object_id]
            if object_id == opening_object_id:
                current += delta_ms / self.config.dwell_time_ms
            else:
                current -= delta_ms / close_duration_ms
            current = max(0.0, min(1.0, current))
            self._flower_open_progress[object_id] = current
            if current >= 1.0:
                newly_completed.append(object_id)

        for object_id in newly_completed:
            self._trigger_reward(object_id, timestamp_ns)

    def _reach_garden_goal(self, timestamp_ns: int) -> None:
        if (
            self._garden_goal_reached
            or self.current_block.block_type is not GardenBlockType.CONTINGENT_2
            or len(self._completed_flower_ids) != len(self.protocol.objects)
        ):
            return
        self._garden_goal_reached = True
        self._celebration_deadline_ns = timestamp_ns + 2_800_000_000
        self._finish_current_block(timestamp_ns, reason="all_flowers_opened")
        self._queue_event(
            "garden_completed",
            timestamp_ns=timestamp_ns,
            payload={
                **self._block_payload(),
                "completed_flower_ids": sorted(self._completed_flower_ids),
                "sky_brightness": self.sky_brightness,
            },
        )
        if self.config.sound_enabled:
            self.speech_requested.emit("恭喜您成功点亮花园")
        self.update()

    def _current_observation(self) -> GardenBlockObservation:
        return GardenBlockObservation(
            block_type=self.current_block.block_type,
            sample_count=self._sample_count,
            valid_sample_count=self._valid_sample_count,
            valid_duration_ms=self._valid_duration_ms,
            target_dwell_ms=self._target_dwell_ms,
            activation_latencies_ms=tuple(self._activation_latencies_ms),
            entered_object_ids=tuple(sorted(self._entered_object_ids)),
            loss_and_reacquisition_count=self._loss_and_reacquisition_count,
            replay_reward_count=self._replay_reward_count,
            replay_rewards_on_target=self._replay_rewards_on_target,
        )

    def _finish_current_block(self, timestamp_ns: int, *, reason: str) -> None:
        block = self.current_block
        observation = self._current_observation()
        self._block_observations.append(observation)
        self._queue_event(
            "block_finished",
            timestamp_ns=timestamp_ns,
            payload={
                **self._block_payload(block),
                "reason": reason,
                "sample_count": observation.sample_count,
                "valid_sample_count": observation.valid_sample_count,
                "activation_count": len(observation.activation_latencies_ms),
            },
        )

    def _finish_protocol(self, timestamp_ns: int) -> None:
        if self._protocol_finished:
            return

        self._protocol_finished = True
        self._running = False
        self._timer.stop()
        self._queue_event(
            "protocol_finished",
            timestamp_ns=timestamp_ns,
            payload={
                "game_mode": "garden",
                "randomization_seed": self.protocol.randomization_seed,
                "reward_count": self._reward_count,
                "completed_flower_ids": sorted(self._completed_flower_ids),
                "sky_brightness": self.sky_brightness,
                "garden_goal_reached": self._garden_goal_reached,
            },
        )
        self.update()
        self.protocol_completed.emit()

    def _present_due_replay_events(self, timestamp_ns: int) -> None:
        if (
            self.current_block.block_type is not GardenBlockType.REPLAY
            or self._replay_schedule is None
            or self._block_started_ns is None
        ):
            return

        offset_ms = max(0.0, (timestamp_ns - self._block_started_ns) / 1_000_000.0)
        events = self._replay_schedule.events

        while (
            self._next_replay_event_index < len(events)
            and events[self._next_replay_event_index].offset_ms <= offset_ms
        ):
            event = events[self._next_replay_event_index]
            self._next_replay_event_index += 1
            event_timestamp_ns = self._block_started_ns + event.offset_ms * 1_000_000
            self._reward_until_ns[event.object_id] = (
                event_timestamp_ns + self.config.reward_animation_ms * 1_000_000
            )
            self._reward_count += 1
            self._replay_reward_count += 1
            on_target = self._previous_object_id == event.object_id and self._previous_valid
            self._replay_rewards_on_target += int(on_target)
            self._queue_event(
                "replay_reward_presented",
                timestamp_ns=event_timestamp_ns,
                payload={
                    **self._block_payload(),
                    "object_id": event.object_id,
                    "scheduled_offset_ms": event.offset_ms,
                    "replay_source": self._replay_schedule.replay_source,
                    "gaze_aoi_id": self._previous_object_id,
                    "gaze_on_replayed_object": on_target,
                },
            )
        self.update()

    def advance_time(self, timestamp_ns: int | None = None) -> None:
        if not self._running or self._protocol_finished:
            return

        now_ns = monotonic_ns() if timestamp_ns is None else int(timestamp_ns)
        self._advance_flower_animation(now_ns)
        if self._garden_goal_reached:
            if (
                self._celebration_deadline_ns is not None
                and now_ns >= self._celebration_deadline_ns
            ):
                self._finish_protocol(self._celebration_deadline_ns)
            else:
                self.update()
            return
        self._present_due_replay_events(now_ns)

        while (
            self._running
            and self._block_deadline_ns is not None
            and now_ns >= self._block_deadline_ns
        ):
            boundary_ns = self._block_deadline_ns
            self._present_due_replay_events(boundary_ns)
            finished_block = self.current_block

            if finished_block.block_type is GardenBlockType.CONTINGENT_2 and len(
                self._completed_flower_ids
            ) < len(self.protocol.objects):
                self._block_deadline_ns = None
                self._queue_event(
                    "garden_goal_waiting",
                    timestamp_ns=boundary_ns,
                    payload={
                        **self._block_payload(finished_block),
                        "completed_flower_ids": sorted(self._completed_flower_ids),
                    },
                )
                self.update()
                return

            self._finish_current_block(boundary_ns, reason="block_duration_elapsed")

            if finished_block.block_type is GardenBlockType.CONTINGENT_1:
                self._replay_schedule = garden_replay_schedule(
                    self.config,
                    self.protocol,
                    self._contingent_1_rewards,
                )

            if self._block_index + 1 >= len(self.protocol.blocks):
                self._finish_protocol(boundary_ns)
                return

            self._begin_block(self._block_index + 1, boundary_ns)
            if self.current_block.block_type is GardenBlockType.CONTINGENT_2:
                self._reach_garden_goal(boundary_ns)
                if self._garden_goal_reached:
                    return
            self._present_due_replay_events(now_ns)

        self.update()

    def expire_current_block(self, *, timestamp_ns: int | None = None) -> None:
        """Advance exactly to the active block boundary for deterministic tests."""
        boundary = self._block_deadline_ns
        self.advance_time(
            timestamp_ns=(
                monotonic_ns()
                if timestamp_ns is None and boundary is None
                else boundary
                if timestamp_ns is None
                else timestamp_ns
            )
        )

    def _object_at(self, x: float, y: float) -> str | None:
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        radius_px = self.config.object_diameter_px / 2.0

        for item in self.protocol.objects:
            delta_x = (x - item.x_normalized) * width
            delta_y = (y - item.y_normalized) * height

            if delta_x * delta_x + delta_y * delta_y <= radius_px * radius_px:
                return item.object_id

        return None

    def _advance_previous_sample_interval(self, timestamp_ns: int) -> None:
        previous_timestamp_ns = self._last_sample_timestamp_ns

        if previous_timestamp_ns is None or timestamp_ns <= previous_timestamp_ns:
            return

        delta_ms = min(250.0, (timestamp_ns - previous_timestamp_ns) / 1_000_000.0)

        if self._previous_valid:
            self._valid_duration_ms += delta_ms

            if self._previous_object_id is not None:
                self._target_dwell_ms += delta_ms

    def _leave_object(self, timestamp_ns: int, *, reason: str) -> None:
        object_id = self._previous_object_id

        if object_id is None:
            return

        if object_id in self._activated_object_ids:
            self._objects_left_after_activation.add(object_id)

        self._queue_event(
            "dwell_reset",
            timestamp_ns=timestamp_ns,
            payload={
                **self._block_payload(),
                "object_id": object_id,
                "reason": reason,
            },
        )

    def _trigger_reward(self, object_id: str, timestamp_ns: int) -> None:
        if self._block_started_ns is None or object_id in self._completed_flower_ids:
            return

        offset_ms = max(0, round((timestamp_ns - self._block_started_ns) / 1_000_000.0))
        self._activation_latencies_ms.append(float(offset_ms))
        self._flower_open_progress[object_id] = 1.0
        self._completed_flower_ids.add(object_id)
        self._reward_until_ns[object_id] = (
            timestamp_ns + self.config.reward_animation_ms * 1_000_000
        )
        self._reward_count += 1

        if object_id in self._objects_left_after_activation:
            self._loss_and_reacquisition_count += 1
            self._objects_left_after_activation.discard(object_id)

        self._activated_object_ids.add(object_id)

        if self.current_block.block_type is GardenBlockType.CONTINGENT_1:
            self._contingent_1_rewards.append(
                GardenRewardEvent(
                    object_id=object_id,
                    offset_ms=min(
                        self.config.contingent_block_seconds * 1000,
                        offset_ms,
                    ),
                )
            )

        self._queue_event(
            "reward_triggered",
            timestamp_ns=timestamp_ns,
            payload={
                **self._block_payload(),
                "object_id": object_id,
                "latency_ms": offset_ms,
                "dwell_threshold_ms": self.config.dwell_time_ms,
                "gaze_valid": True,
                "completed_flower_count": len(self._completed_flower_ids),
                "sky_brightness": self.sky_brightness,
            },
        )

        if self.config.sound_enabled:
            self.speech_requested.emit("花开了")

        self._reach_garden_goal(timestamp_ns)
        self.update()

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        if not self._running or self._protocol_finished or self._garden_goal_reached:
            return

        timestamp_ns = sample.timestamp.monotonic_timestamp_ns

        if (
            self._sample_count == 0
            and self._block_started_ns is not None
            and timestamp_ns < self._block_started_ns
        ):
            difference_ns = self._block_started_ns - timestamp_ns
            self._block_started_ns = timestamp_ns

            if self._block_deadline_ns is not None:
                self._block_deadline_ns -= difference_ns

            for event in reversed(self._recording_events):
                if event.get("event_type") == "block_started":
                    event["monotonic_timestamp_ns"] = timestamp_ns
                    break

        self.advance_time(timestamp_ns)

        if not self._running or self._protocol_finished:
            return

        self._advance_previous_sample_interval(timestamp_ns)
        self._sample_count += 1
        gaze_x_value = sample.gaze_x_normalized
        gaze_y_value = sample.gaze_y_normalized
        valid = bool(sample.gaze_valid and gaze_x_value is not None and gaze_y_value is not None)

        if not valid:
            if self._previous_object_id is not None:
                self._leave_object(timestamp_ns, reason="invalid_gaze")

            self._previous_valid = False
            self._previous_object_id = None
            self._last_valid_gaze_timestamp_ns = None
            self._last_gaze_normalized = None
            self._last_sample_timestamp_ns = timestamp_ns
            self.update()
            return

        assert gaze_x_value is not None
        assert gaze_y_value is not None
        gaze_x = max(0.0, min(1.0, float(gaze_x_value)))
        gaze_y = max(0.0, min(1.0, float(gaze_y_value)))
        object_id = self._object_at(gaze_x, gaze_y)
        self._valid_sample_count += 1
        self._last_gaze_normalized = (gaze_x, gaze_y)

        if object_id != self._previous_object_id:
            if self._previous_object_id is not None:
                self._leave_object(timestamp_ns, reason="gaze_moved")

            if object_id is not None:
                self._entered_object_ids.add(object_id)
                self._queue_event(
                    "dwell_started",
                    timestamp_ns=timestamp_ns,
                    payload={
                        **self._block_payload(),
                        "object_id": object_id,
                    },
                )

        self._previous_valid = True
        self._previous_object_id = object_id
        self._last_valid_gaze_timestamp_ns = timestamp_ns
        self._last_sample_timestamp_ns = timestamp_ns
        self.update()

    def _object_aois(self) -> tuple[dict[str, object], ...]:
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        radius_x = self.config.object_diameter_px / 2.0 / width
        radius_y = self.config.object_diameter_px / 2.0 / height
        block = self.current_block
        aois: list[dict[str, object]] = []

        for item in self.protocol.objects:
            aois.append(
                {
                    "aoi_id": item.object_id,
                    "role": "target",
                    "left": max(0.0, item.x_normalized - radius_x),
                    "top": max(0.0, item.y_normalized - radius_y),
                    "right": min(1.0, item.x_normalized + radius_x),
                    "bottom": min(1.0, item.y_normalized + radius_y),
                    "label": "garden_flower",
                    "metadata": {
                        "object_id": item.object_id,
                        "block_type": block.block_type.value,
                        "layout_slot": item.layout_slot,
                    },
                }
            )

        aois.append(
            {
                "aoi_id": "garden-background",
                "role": "other",
                "left": 0.0,
                "top": 0.0,
                "right": 1.0,
                "bottom": 1.0,
                "label": "garden_background",
                "metadata": {"block_type": block.block_type.value},
            }
        )
        return tuple(aois)

    def recording_context_for_sample(
        self,
        _sample: EyeTrackerSample,
    ) -> dict[str, object]:
        return {
            "question_id": f"garden-block-{self.current_block.block_index}",
            "phase": self.phase,
            "aois": self._object_aois(),
            "question_metadata": self._block_payload(),
        }

    def drain_recording_events(self) -> tuple[dict[str, object], ...]:
        events = tuple(self._recording_events)
        self._recording_events.clear()
        return events

    def recording_result(self, reason: str) -> dict[str, object]:
        if self._result_cache is not None:
            return dict(self._result_cache)

        reason_text = reason.strip() if reason.strip() else "completed"
        observations = list(self._block_observations)

        if not self._protocol_finished and self._block_started_ns is not None:
            observations.append(self._current_observation())

        summary = summarize_garden_observations(self.config, observations)
        completed = self._protocol_finished and reason_text in {
            "completed",
            "protocol_completed",
            "test_complete",
        }
        result = {
            "task_kind": "gaze_games",
            "game_mode": "garden",
            "completion_status": "completed" if completed else "interrupted",
            "completion_reason": reason_text,
            "randomization_seed": self.protocol.randomization_seed,
            "configuration": {
                "object_count": self.config.object_count,
                "object_diameter_px": self.config.object_diameter_px,
                "dwell_time_ms": self.config.dwell_time_ms,
                "baseline_seconds": self.config.baseline_seconds,
                "contingent_block_seconds": self.config.contingent_block_seconds,
                "replay_block_seconds": self.config.replay_block_seconds,
                "reward_animation_ms": self.config.reward_animation_ms,
                "sound_enabled": self.config.sound_enabled,
                "show_gaze_cursor": self.config.show_gaze_cursor,
            },
            "objects": [
                {
                    "object_id": item.object_id,
                    "layout_slot": item.layout_slot,
                    "x_normalized": item.x_normalized,
                    "y_normalized": item.y_normalized,
                }
                for item in self.protocol.objects
            ],
            "replay_source": (
                self._replay_schedule.replay_source if self._replay_schedule is not None else None
            ),
            "reward_count": self._reward_count,
            "completed_flower_count": len(self._completed_flower_ids),
            "completed_flower_ids": sorted(self._completed_flower_ids),
            "flower_open_progress": dict(self._flower_open_progress),
            "sky_brightness": self.sky_brightness,
            "garden_goal_reached": self._garden_goal_reached,
            "blocks": [
                {
                    "block_type": observation.block_type.value,
                    "sample_count": observation.sample_count,
                    "valid_sample_count": observation.valid_sample_count,
                    "valid_duration_ms": observation.valid_duration_ms,
                    "target_dwell_ms": observation.target_dwell_ms,
                    "activation_latencies_ms": list(observation.activation_latencies_ms),
                    "entered_object_ids": list(observation.entered_object_ids),
                    "loss_and_reacquisition_count": (observation.loss_and_reacquisition_count),
                    "replay_reward_count": observation.replay_reward_count,
                    "replay_rewards_on_target": (observation.replay_rewards_on_target),
                }
                for observation in observations
            ],
            **summary,
        }
        self._result_cache = result
        return dict(result)

    def _flower_center(self, item: GardenObject) -> QPointF:
        return QPointF(
            item.x_normalized * self.width(),
            item.y_normalized * self.height(),
        )

    @staticmethod
    def _blend_color(start: str, finish: str, progress: float) -> QColor:
        lower = QColor(start)
        upper = QColor(finish)
        ratio = max(0.0, min(1.0, progress))
        return QColor(
            round(lower.red() + (upper.red() - lower.red()) * ratio),
            round(lower.green() + (upper.green() - lower.green()) * ratio),
            round(lower.blue() + (upper.blue() - lower.blue()) * ratio),
        )

    @staticmethod
    def _paint_lotus(
        painter: QPainter,
        *,
        center: QPointF,
        radius: float,
        progress: float,
        glowing: bool,
        now_ns: int,
    ) -> None:
        opening = max(0.0, min(1.0, progress))
        pulse = 1.0 + (0.05 * sin(now_ns / 650_000_000.0 * 2.0 * pi) if glowing else 0.0)
        radius *= pulse

        if glowing:
            painter.setPen(Qt.PenStyle.NoPen)
            for ring, alpha in ((1.05, 48), (0.88, 70), (0.72, 90)):
                glow = QColor("#fff4a8")
                glow.setAlpha(alpha)
                painter.setBrush(glow)
                painter.drawEllipse(center, radius * ring, radius * ring)

        spread = 0.18 * pi + opening * 1.82 * pi
        petal_distance = radius * (0.08 + 0.34 * opening)
        petal_width = radius * (0.20 + 0.08 * opening)
        petal_height = radius * (0.42 + 0.10 * opening)
        painter.setPen(QPen(QColor("#e58f45"), max(1.5, radius * 0.018)))

        for petal_index in range(8):
            fraction = petal_index / 7.0
            angle = -spread / 2.0 + spread * fraction
            painter.save()
            painter.translate(center)
            painter.rotate(angle * 180.0 / pi)
            painter.setBrush(
                QColor("#ffe066" if glowing else ("#f5a6b8" if petal_index % 2 else "#ffd1dc"))
            )
            painter.drawEllipse(
                QRectF(
                    -petal_width / 2.0,
                    -petal_distance - petal_height,
                    petal_width,
                    petal_height,
                )
            )
            painter.restore()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffb703" if glowing else "#e28b2d"))
        painter.drawEllipse(
            center,
            radius * (0.16 + opening * 0.04),
            radius * (0.16 + opening * 0.04),
        )

        if glowing:
            painter.setBrush(QColor("#fffbe0"))
            for star_index in range(6):
                angle = now_ns / 800_000_000.0 + star_index * pi / 3.0
                star_center = QPointF(
                    center.x() + cos(angle) * radius * 0.88,
                    center.y() + sin(angle) * radius * 0.88,
                )
                painter.drawEllipse(star_center, 5.0, 5.0)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(
            self.rect(),
            self._blend_color("#071f2a", "#75c9f1", self.sky_brightness),
        )

        if self._protocol_finished:
            painter.setPen(QColor("#f7f7d4"))
            font = painter.font()
            font.setFamily("Microsoft YaHei UI")
            font.setPointSize(38)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                f"花园完成\n点亮 {len(self._completed_flower_ids)} 朵",
            )
            painter.end()
            return

        now_ns = monotonic_ns()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._blend_color("#0d3f3a", "#3f8c57", self.sky_brightness))
        painter.drawRect(0, round(self.height() * 0.80), self.width(), self.height())
        base_radius = self.config.object_diameter_px / 2.0

        painter.setPen(QPen(QColor("#3f9258"), max(6, round(base_radius * 0.07))))

        for item in self.protocol.objects:
            center = self._flower_center(item)
            painter.drawLine(
                QPointF(center.x(), center.y() + base_radius * 0.34),
                QPointF(center.x(), self.height() * 0.86),
            )

        for item in self.protocol.objects:
            center = self._flower_center(item)
            completed = item.object_id in self._completed_flower_ids
            replay_open = now_ns < self._reward_until_ns.get(item.object_id, 0)
            progress = (
                1.0 if completed or replay_open else self._flower_open_progress[item.object_id]
            )
            self._paint_lotus(
                painter,
                center=center,
                radius=base_radius,
                progress=progress,
                glowing=completed or replay_open,
                now_ns=now_ns,
            )

        if self._garden_goal_reached:
            painter.setPen(QColor("#fffbe0"))
            font = painter.font()
            font.setFamily("Microsoft YaHei UI")
            font.setPointSize(28)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRectF(0.0, 18.0, float(self.width()), 70.0),
                Qt.AlignmentFlag.AlignCenter,
                "恭喜您成功点亮花园",
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
