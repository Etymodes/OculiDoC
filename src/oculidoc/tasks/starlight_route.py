"""Adaptive gaze game for learning a patient's reachable visual field."""

from __future__ import annotations

import math
import random
import secrets
from dataclasses import asdict, dataclass
from enum import StrEnum
from time import monotonic_ns

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from oculidoc.devices.contracts import EyeTrackerSample


def _bounded_int(name: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")


class ProbeEdge(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True, slots=True)
class StarlightRouteConfig:
    round_count: int = 24
    initial_level: int = 1
    dwell_time_ms: int = 900
    trial_duration_seconds: int = 8
    edge_probe_interval: int = 4
    sound_enabled: bool = True
    show_gaze_cursor: bool = False
    randomization_seed: int | None = None

    def __post_init__(self) -> None:
        _bounded_int("round_count", self.round_count, 6, 120)
        _bounded_int("initial_level", self.initial_level, 1, 10)
        _bounded_int("dwell_time_ms", self.dwell_time_ms, 250, 3000)
        _bounded_int("trial_duration_seconds", self.trial_duration_seconds, 3, 30)
        _bounded_int("edge_probe_interval", self.edge_probe_interval, 2, 10)
        if not isinstance(self.sound_enabled, bool):
            raise TypeError("sound_enabled must be a boolean.")
        if not isinstance(self.show_gaze_cursor, bool):
            raise TypeError("show_gaze_cursor must be a boolean.")
        if self.randomization_seed is not None:
            _bounded_int("randomization_seed", self.randomization_seed, 0, 0xFFFFFFFF)


@dataclass(frozen=True, slots=True)
class ReachableRegion:
    left: float = 0.30
    top: float = 0.25
    right: float = 0.70
    bottom: float = 0.75

    def __post_init__(self) -> None:
        if not (0.05 <= self.left < self.right <= 0.95):
            raise ValueError("Invalid horizontal reachable region.")
        if not (0.05 <= self.top < self.bottom <= 0.95):
            raise ValueError("Invalid vertical reachable region.")


@dataclass(frozen=True, slots=True)
class StarTarget:
    round_index: int
    x: float
    y: float
    radius_normalized: float
    level: int
    probe_edge: ProbeEdge | None = None


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    target: StarTarget
    status: str
    valid_sample_ratio: float
    response_ms: float | None
    score_after: int
    region_after: ReachableRegion


class StarlightAdaptiveModel:
    """Deterministic staircase that separates performance from signal quality."""

    def __init__(self, config: StarlightRouteConfig) -> None:
        self.config = config
        seed = config.randomization_seed
        self.randomization_seed = secrets.randbits(32) if seed is None else seed
        self._rng = random.Random(self.randomization_seed)
        self.level = config.initial_level
        self.score = 0
        self.region = ReachableRegion()
        self.success_streak = 0
        self.valid_miss_streak = 0
        self.completed_rounds = 0
        self.outcomes: list[RoundOutcome] = []
        self._edge_index = 0

    @property
    def star_radius(self) -> float:
        return max(0.035, 0.075 - (self.level - 1) * 0.004)

    def next_target(self) -> StarTarget:
        probe = (
            self.completed_rounds > 0
            and self.completed_rounds % self.config.edge_probe_interval == 0
        )
        edge: ProbeEdge | None = None
        margin = 0.07
        if probe:
            edge = tuple(ProbeEdge)[self._edge_index % len(ProbeEdge)]
            self._edge_index += 1
            if edge is ProbeEdge.LEFT:
                x, y = (
                    max(0.07, self.region.left - margin),
                    self._rng.uniform(self.region.top, self.region.bottom),
                )
            elif edge is ProbeEdge.RIGHT:
                x, y = (
                    min(0.93, self.region.right + margin),
                    self._rng.uniform(self.region.top, self.region.bottom),
                )
            elif edge is ProbeEdge.TOP:
                x, y = (
                    self._rng.uniform(self.region.left, self.region.right),
                    max(0.07, self.region.top - margin),
                )
            else:
                x, y = (
                    self._rng.uniform(self.region.left, self.region.right),
                    min(0.93, self.region.bottom + margin),
                )
        else:
            x = self._rng.uniform(self.region.left, self.region.right)
            y = self._rng.uniform(self.region.top, self.region.bottom)
        return StarTarget(
            round_index=self.completed_rounds,
            x=x,
            y=y,
            radius_normalized=self.star_radius,
            level=self.level,
            probe_edge=edge,
        )

    def record(
        self,
        target: StarTarget,
        *,
        acquired: bool,
        valid_sample_ratio: float,
        response_ms: float | None,
    ) -> RoundOutcome:
        if target.round_index != self.completed_rounds:
            raise ValueError("Target does not match the current round.")
        quality_valid = valid_sample_ratio >= 0.50
        status = "hit" if acquired and quality_valid else "miss" if quality_valid else "invalid"
        if status == "hit":
            self.score += 10 * self.level
            self.success_streak += 1
            self.valid_miss_streak = 0
            if target.probe_edge is not None:
                self.region = self._update_edge(target.probe_edge, target, success=True)
            if self.success_streak >= 3:
                self.level = min(10, self.level + 1)
                self.success_streak = 0
        elif status == "miss":
            self.success_streak = 0
            self.valid_miss_streak += 1
            if target.probe_edge is not None:
                self.region = self._update_edge(target.probe_edge, target, success=False)
            if self.valid_miss_streak >= 2:
                self.level = max(1, self.level - 1)
                self.valid_miss_streak = 0
        outcome = RoundOutcome(
            target=target,
            status=status,
            valid_sample_ratio=max(0.0, min(1.0, float(valid_sample_ratio))),
            response_ms=response_ms,
            score_after=self.score,
            region_after=self.region,
        )
        self.outcomes.append(outcome)
        self.completed_rounds += 1
        return outcome

    def _update_edge(
        self, edge: ProbeEdge, target: StarTarget, *, success: bool
    ) -> ReachableRegion:
        r = self.region
        if edge is ProbeEdge.LEFT:
            left = target.x if success else min(r.right - 0.10, r.left + 0.035)
            return ReachableRegion(left=max(0.05, left), top=r.top, right=r.right, bottom=r.bottom)
        if edge is ProbeEdge.RIGHT:
            right = target.x if success else max(r.left + 0.10, r.right - 0.035)
            return ReachableRegion(left=r.left, top=r.top, right=min(0.95, right), bottom=r.bottom)
        if edge is ProbeEdge.TOP:
            top = target.y if success else min(r.bottom - 0.10, r.top + 0.035)
            return ReachableRegion(left=r.left, top=max(0.05, top), right=r.right, bottom=r.bottom)
        bottom = target.y if success else max(r.top + 0.10, r.bottom - 0.035)
        return ReachableRegion(left=r.left, top=r.top, right=r.right, bottom=min(0.95, bottom))


class StarlightRouteTask(QWidget):
    """Patient screen with a breathing, slowly rotating gaze target."""

    protocol_completed = Signal()
    speech_requested = Signal(str)

    def __init__(self, config: StarlightRouteConfig, *, allow_mouse_fallback: bool = False) -> None:
        super().__init__()
        self.config = config
        self.model = StarlightAdaptiveModel(config)
        self.allow_mouse_fallback = allow_mouse_fallback
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._target = self.model.next_target()
        self._running = False
        self._finished = False
        self._round_started_ns: int | None = None
        self._last_sample_ns: int | None = None
        self._dwell_started_ns: int | None = None
        self._sample_count = 0
        self._valid_sample_count = 0
        self._last_gaze: tuple[float, float] | None = None
        self._collected_positions: list[tuple[float, float]] = []
        self._events: list[dict[str, object]] = []
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.advance_time)

    def start(self, timestamp_ns: int | None = None) -> None:
        now = monotonic_ns() if timestamp_ns is None else int(timestamp_ns)
        self._running = True
        self._round_started_ns = now
        self._queue("protocol_started", now)
        if self.config.sound_enabled:
            self.speech_requested.emit("请跟随星光")
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._running = False

    def _queue(self, event_type: str, timestamp_ns: int, **payload: object) -> None:
        self._events.append(
            {"event_type": event_type, "monotonic_timestamp_ns": timestamp_ns, "payload": payload}
        )

    def _inside_target(self, x: float, y: float) -> bool:
        dx = x - self._target.x
        dy = y - self._target.y
        return dx * dx + dy * dy <= self._target.radius_normalized**2

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        if not self._running:
            return
        now = sample.timestamp.monotonic_timestamp_ns
        self.advance_time(now)
        if not self._running:
            return
        self._sample_count += 1
        valid = bool(
            sample.gaze_valid
            and sample.gaze_x_normalized is not None
            and sample.gaze_y_normalized is not None
        )
        if not valid:
            self._dwell_started_ns = None
            self._last_gaze = None
            return
        assert sample.gaze_x_normalized is not None
        assert sample.gaze_y_normalized is not None
        self._valid_sample_count += 1
        x = max(0.0, min(1.0, float(sample.gaze_x_normalized)))
        y = max(0.0, min(1.0, float(sample.gaze_y_normalized)))
        self._last_gaze = (x, y)
        if not self._inside_target(x, y):
            self._dwell_started_ns = None
        elif self._dwell_started_ns is None:
            self._dwell_started_ns = now
        elif now - self._dwell_started_ns >= self.config.dwell_time_ms * 1_000_000:
            self._finish_round(now, acquired=True)
        self._last_sample_ns = now
        self.update()

    def advance_time(self, timestamp_ns: int | None = None) -> None:
        if not self._running or self._round_started_ns is None:
            return
        now = monotonic_ns() if timestamp_ns is None else int(timestamp_ns)
        if now - self._round_started_ns >= self.config.trial_duration_seconds * 1_000_000_000:
            self._finish_round(now, acquired=False)
        self.update()

    def _finish_round(self, now: int, *, acquired: bool) -> None:
        assert self._round_started_ns is not None
        ratio = self._valid_sample_count / self._sample_count if self._sample_count else 0.0
        response = (now - self._round_started_ns) / 1_000_000.0 if acquired else None
        outcome = self.model.record(
            self._target, acquired=acquired, valid_sample_ratio=ratio, response_ms=response
        )
        if outcome.status == "hit":
            self._collected_positions.append((self._target.x, self._target.y))
        self._queue(
            "star_round_finished",
            now,
            status=outcome.status,
            level=self._target.level,
            score=self.model.score,
        )
        if self.model.completed_rounds >= self.config.round_count:
            self._finished = True
            self.stop()
            self._queue("protocol_finished", now, score=self.model.score)
            self.protocol_completed.emit()
            return
        self._target = self.model.next_target()
        self._round_started_ns = now
        self._sample_count = 0
        self._valid_sample_count = 0
        self._dwell_started_ns = None
        if outcome.status == "hit" and self.config.sound_enabled:
            self.speech_requested.emit("收集到了")

    def drain_recording_events(self) -> tuple[dict[str, object], ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    def recording_context_for_sample(self, _sample: EyeTrackerSample) -> dict[str, object]:
        t = self._target
        return {
            "question_id": f"starlight-{t.round_index + 1:03d}",
            "phase": "edge_probe" if t.probe_edge else "route",
            "aois": (
                {
                    "aoi_id": "starlight-target",
                    "role": "target",
                    "left": t.x - t.radius_normalized,
                    "top": t.y - t.radius_normalized,
                    "right": t.x + t.radius_normalized,
                    "bottom": t.y + t.radius_normalized,
                    "label": "star",
                    "metadata": {
                        "level": t.level,
                        "probe_edge": t.probe_edge.value if t.probe_edge else None,
                    },
                },
            ),
            "question_metadata": {"game_mode": "starlight_route", "level": t.level},
        }

    def recording_result(self, reason: str) -> dict[str, object]:
        completed = self._finished and reason in {
            "completed",
            "protocol_completed",
            "test_complete",
        }
        hit_count = sum(item.status == "hit" for item in self.model.outcomes)
        miss_count = sum(item.status == "miss" for item in self.model.outcomes)
        invalid_count = sum(item.status == "invalid" for item in self.model.outcomes)
        valid_round_count = hit_count + miss_count
        return {
            "task_kind": "gaze_games",
            "game_mode": "starlight_route",
            "completion_status": "completed" if completed else "interrupted",
            "completion_reason": reason,
            "randomization_seed": self.model.randomization_seed,
            "score": self.model.score,
            "final_level": self.model.level,
            "hit_count": hit_count,
            "miss_count": miss_count,
            "invalid_round_count": invalid_count,
            "valid_round_count": valid_round_count,
            "hit_ratio_valid_rounds": (
                hit_count / valid_round_count if valid_round_count else None
            ),
            "reachable_region": asdict(self.model.region),
            "rounds": [
                {
                    "round_index": item.target.round_index,
                    "level": item.target.level,
                    "status": item.status,
                    "probe_edge": item.target.probe_edge.value if item.target.probe_edge else None,
                    "target_x": item.target.x,
                    "target_y": item.target.y,
                    "valid_sample_ratio": item.valid_sample_ratio,
                    "response_ms": item.response_ms,
                    "score_after": item.score_after,
                    "region_after": asdict(item.region_after),
                }
                for item in self.model.outcomes
            ],
        }

    @staticmethod
    def _star_polygon(center: QPointF, outer: float, rotation: float) -> QPolygonF:
        points: list[QPointF] = []
        for index in range(10):
            radius = outer if index % 2 == 0 else outer * 0.45
            angle = rotation - math.pi / 2 + index * math.pi / 5
            points.append(
                QPointF(
                    center.x() + math.cos(angle) * radius, center.y() + math.sin(angle) * radius
                )
            )
        return QPolygonF(points)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#06142b"))
        elapsed = monotonic_ns() / 1_000_000_000.0
        route_points = [
            QPointF(x * self.width(), y * self.height()) for x, y in self._collected_positions
        ]
        if route_points:
            painter.setPen(QPen(QColor(100, 205, 255, 120), 3))
            for start, end in zip(route_points, route_points[1:], strict=False):
                painter.drawLine(start, end)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#9de8ff"))
            for point in route_points:
                painter.drawEllipse(point, 6, 6)
        center = QPointF(self._target.x * self.width(), self._target.y * self.height())
        base = self._target.radius_normalized * min(self.width(), self.height())
        breath = 1.0 + 0.12 * math.sin(elapsed * math.tau / 1.8)
        glow = QColor(100, 205, 255, 65)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, base * 1.65 * breath, base * 1.65 * breath)
        painter.setBrush(QColor("#fff4a8"))
        painter.setPen(QPen(QColor("#ffffff"), 3))
        painter.drawPolygon(self._star_polygon(center, base * breath, elapsed * 0.35))
        painter.setPen(QColor("#d6eeff"))
        painter.drawText(24, 42, f"得分 {self.model.score}   等级 {self.model.level}")
        if self.config.show_gaze_cursor and self._last_gaze is not None:
            painter.setPen(QPen(QColor("#62ffb3"), 3))
            p = QPointF(self._last_gaze[0] * self.width(), self._last_gaze[1] * self.height())
            painter.drawEllipse(p, 10, 10)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.allow_mouse_fallback and self._running:

            class _Point:
                gaze_valid = True
                gaze_x_normalized = event.position().x() / max(1, self.width())
                gaze_y_normalized = event.position().y() / max(1, self.height())
                timestamp = type("Timestamp", (), {"monotonic_timestamp_ns": monotonic_ns()})()

            self.consume_sample(_Point())  # type: ignore[arg-type]

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            event.ignore()
            return
        super().keyPressEvent(event)
