"""Configurable gaze-following target task."""

from dataclasses import dataclass
from enum import StrEnum
from math import cos, pi, sin
from pathlib import Path
from time import monotonic_ns

from PySide6.QtCore import (
    QElapsedTimer,
    QPointF,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.devices.contracts import (
    EyeTrackerSample,
)
from oculidoc.image_library import (
    IMAGE_UPLOAD_GUIDE,
    ImageAssetDialog,
    ImageLibraryDialog,
    ImageLibraryStore,
)
from oculidoc.tasks.tracking_dwell import (
    DwellPhase,
    DwellSnapshot,
    TrackingDwellController,
)


class TargetShape(StrEnum):
    CIRCLE = "circle"
    SQUARE = "square"
    DIAMOND = "diamond"
    STAR = "star"


class TargetEffect(StrEnum):
    NONE = "none"
    PULSE = "pulse"
    SPIN = "spin"


class TargetPath(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    CIRCLE = "circle"
    Z = "z"
    FIGURE_EIGHT = "figure_eight"
    RANDOM = "random"


@dataclass(frozen=True, slots=True)
class TrackingBallConfig:
    shape: TargetShape = TargetShape.CIRCLE
    effect: TargetEffect = TargetEffect.PULSE
    path: TargetPath = TargetPath.HORIZONTAL
    horizontal_position: str = "middle"
    vertical_position: str = "center"
    diameter_px: int = 300
    color: str = "#ffcc00"
    image_path: str | None = None
    period_seconds: float = 12.0
    duration_seconds: int = 60
    dwell_time_ms: int = 900
    dwell_feedback_color: str = "#35d07f"
    dwell_outline_color: str = "#ffffff"
    dwell_hit_radius_scale: float = 1.15
    background_color: str = "#071521"
    show_gaze_cursor: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "shape",
            TargetShape(self.shape),
        )
        object.__setattr__(
            self,
            "effect",
            TargetEffect(self.effect),
        )
        object.__setattr__(
            self,
            "path",
            TargetPath(self.path),
        )

        if self.horizontal_position not in {"top", "middle", "bottom"}:
            raise ValueError("horizontal_position must be top, middle, or bottom.")

        if self.vertical_position not in {"left", "center", "right"}:
            raise ValueError("vertical_position must be left, center, or right.")

        if not 16 <= self.diameter_px <= 600:
            raise ValueError("diameter_px must be between 16 and 600.")

        if not 1.0 <= self.period_seconds <= 120.0:
            raise ValueError("period_seconds must be between 1 and 120.")

        if not 5 <= self.duration_seconds <= 3_600:
            raise ValueError("duration_seconds must be between 5 and 3600.")

        if not 100 <= self.dwell_time_ms <= 10_000:
            raise ValueError("dwell_time_ms must be between 100 and 10000.")

        if not QColor(self.dwell_feedback_color).isValid():
            raise ValueError("dwell_feedback_color must be valid.")

        if not QColor(self.dwell_outline_color).isValid():
            raise ValueError("dwell_outline_color must be valid.")

        if not 0.5 <= self.dwell_hit_radius_scale <= 2.5:
            raise ValueError("dwell_hit_radius_scale must be between 0.5 and 2.5.")

        if not QColor(self.color).isValid():
            raise ValueError("color must be a valid Qt color.")

        if not QColor(self.background_color).isValid():
            raise ValueError("background_color must be valid.")

        if self.image_path is not None:
            normalized = self.image_path.strip()
            object.__setattr__(
                self,
                "image_path",
                normalized or None,
            )


class TrackingBallTask(QWidget):
    """Render a configurable target and live gaze marker."""

    def __init__(
        self,
        config: TrackingBallConfig,
        *,
        allow_mouse_fallback: bool = True,
    ) -> None:
        super().__init__()
        self.config = config
        self.allow_mouse_fallback = allow_mouse_fallback
        self.setMouseTracking(allow_mouse_fallback)

        if not allow_mouse_fallback:
            self.setCursor(Qt.CursorShape.BlankCursor)
        self.setMinimumSize(640, 480)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)

        self._elapsed = QElapsedTimer()
        self._last_gaze_normalized: tuple[float, float] | None = None
        self._valid_sample_count = 0
        self._invalid_sample_count = 0
        self._dwell = TrackingDwellController(
            dwell_time_ms=config.dwell_time_ms,
        )

        self._reset_recording_state()
        self._image = QPixmap()

        if config.image_path:
            image_path = Path(config.image_path).expanduser()

            if image_path.is_file():
                self._image = QPixmap(str(image_path))

    @property
    def last_gaze_normalized(
        self,
    ) -> tuple[float, float] | None:
        return self._last_gaze_normalized

    def _reset_recording_state(self) -> None:
        self._recording_events: list[dict[str, object]] = []
        self._tracking_started_monotonic_ns: int | None = None
        self._last_tracking_timestamp_ns: int | None = None
        self._tracking_sample_count = 0
        self._tracking_valid_sample_count = 0
        self._tracking_invalid_sample_count = 0
        self._tracking_inside_sample_count = 0
        self._tracking_valid_duration_ms = 0.0
        self._tracking_inside_duration_ms = 0.0
        self._tracking_current_run_ms = 0.0
        self._tracking_longest_run_ms = 0.0
        self._tracking_first_entry_ms: float | None = None
        self._tracking_first_acquired_ms: float | None = None
        self._tracking_loss_count = 0
        self._tracking_reacquisition_count = 0
        self._tracking_acquired = False
        self._tracking_had_acquisition = False
        self._tracking_previous_valid = False
        self._tracking_previous_inside = False
        self._tracking_previous_payload: dict[str, object] | None = None
        self._tracking_errors_normalized: list[float] = []
        self._tracking_errors_px: list[float] = []
        self._tracking_final_event_recorded = False

    def _queue_recording_event(
        self,
        event_type: str,
        *,
        monotonic_timestamp_ns: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        timestamp_ns = (
            monotonic_ns() if monotonic_timestamp_ns is None else int(monotonic_timestamp_ns)
        )

        if timestamp_ns < 0:
            raise ValueError("monotonic_timestamp_ns cannot be negative.")

        self._recording_events.append(
            {
                "event_type": event_type,
                "monotonic_timestamp_ns": timestamp_ns,
                "payload": dict(payload or {}),
            }
        )

    def _tracking_configuration_payload(
        self,
    ) -> dict[str, object]:
        return {
            "task_kind": "tracking_ball",
            "path": self.config.path.value,
            "shape": self.config.shape.value,
            "effect": self.config.effect.value,
            "diameter_px": self.config.diameter_px,
            "period_seconds": self.config.period_seconds,
            "configured_dwell_ms": (self.config.dwell_time_ms),
            "dwell_hit_radius_scale": (self.config.dwell_hit_radius_scale),
        }

    def _ensure_tracking_started(
        self,
        monotonic_timestamp_ns: int | None = None,
    ) -> int:
        timestamp_ns = (
            monotonic_ns() if monotonic_timestamp_ns is None else int(monotonic_timestamp_ns)
        )

        if timestamp_ns < 0:
            raise ValueError("monotonic_timestamp_ns cannot be negative.")

        if self._tracking_started_monotonic_ns is None:
            self._tracking_started_monotonic_ns = timestamp_ns
            self._queue_recording_event(
                "tracking_started",
                monotonic_timestamp_ns=timestamp_ns,
                payload=(self._tracking_configuration_payload()),
            )
        elif (
            self._tracking_sample_count == 0 and timestamp_ns < self._tracking_started_monotonic_ns
        ):
            self._tracking_started_monotonic_ns = timestamp_ns

            for event in reversed(self._recording_events):
                if event.get("event_type") == "tracking_started":
                    event["monotonic_timestamp_ns"] = timestamp_ns
                    break

        return self._tracking_started_monotonic_ns

    def drain_recording_events(
        self,
    ) -> tuple[
        dict[str, object],
        ...,
    ]:
        """Return and clear pending tracking events."""

        events = tuple(self._recording_events)
        self._recording_events.clear()
        return events

    @staticmethod
    def _percentile(
        values: list[float],
        percentile: float,
    ) -> float | None:
        if not values:
            return None

        ordered = sorted(values)
        rank = (len(ordered) - 1) * percentile / 100.0
        lower_index = int(rank)
        upper_index = min(
            len(ordered) - 1,
            lower_index + 1,
        )
        fraction = rank - lower_index

        return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction

    def _tracking_measurement(
        self,
        gaze_x: float,
        gaze_y: float,
    ) -> tuple[
        bool,
        float,
        float,
        dict[str, object],
    ]:
        width = max(
            1.0,
            float(self.width()),
        )
        height = max(
            1.0,
            float(self.height()),
        )
        phase = self._phase()
        target_x, target_y = self.target_center_normalized(phase)
        delta_x = gaze_x - target_x
        delta_y = gaze_y - target_y
        error_normalized = (delta_x * delta_x + delta_y * delta_y) ** 0.5
        delta_x_px = delta_x * width
        delta_y_px = delta_y * height
        error_px = (delta_x_px * delta_x_px + delta_y_px * delta_y_px) ** 0.5
        radius_px = self.config.diameter_px / 2.0 * self.config.dwell_hit_radius_scale
        inside_target = error_px <= radius_px
        payload = {
            "gaze_x_normalized": gaze_x,
            "gaze_y_normalized": gaze_y,
            "target_center_x_normalized": (target_x),
            "target_center_y_normalized": (target_y),
            "tracking_error_normalized": (error_normalized),
            "tracking_error_px": error_px,
            "inside_target": inside_target,
            "hit_radius_px": radius_px,
            "motion_phase_radians": phase,
            "path": self.config.path.value,
        }

        return (
            inside_target,
            error_normalized,
            error_px,
            payload,
        )

    def _acquire_tracking(
        self,
        *,
        timestamp_ns: int,
        payload: dict[str, object],
    ) -> None:
        if self._tracking_acquired:
            return

        event_payload = dict(payload)
        event_payload.update(
            {
                "continuous_inside_ms": (self._tracking_current_run_ms),
                "configured_dwell_ms": (self.config.dwell_time_ms),
            }
        )

        if self._tracking_had_acquisition:
            event_type = "tracking_resumed"
            self._tracking_reacquisition_count += 1
        else:
            event_type = "target_acquired"
            started_ns = self._tracking_started_monotonic_ns or timestamp_ns
            self._tracking_first_acquired_ms = max(
                0.0,
                (timestamp_ns - started_ns) / 1_000_000.0,
            )

        self._tracking_acquired = True
        self._tracking_had_acquisition = True
        self._queue_recording_event(
            event_type,
            monotonic_timestamp_ns=timestamp_ns,
            payload=event_payload,
        )

    def _advance_previous_interval(
        self,
        *,
        timestamp_ns: int,
        delta_ms: float,
    ) -> None:
        if self._tracking_previous_valid:
            self._tracking_valid_duration_ms += delta_ms

        if not self._tracking_previous_inside:
            return

        self._tracking_inside_duration_ms += delta_ms
        self._tracking_current_run_ms += delta_ms
        self._tracking_longest_run_ms = max(
            self._tracking_longest_run_ms,
            self._tracking_current_run_ms,
        )

        if (
            not self._tracking_acquired
            and self._tracking_current_run_ms >= self.config.dwell_time_ms
        ):
            self._acquire_tracking(
                timestamp_ns=timestamp_ns,
                payload=dict(self._tracking_previous_payload or {}),
            )

    def _leave_target(
        self,
        *,
        timestamp_ns: int,
        reason: str,
        payload: dict[str, object],
    ) -> None:
        if self._tracking_acquired:
            event_payload = dict(payload)
            event_payload.update(
                {
                    "reason": reason,
                    "continuous_inside_ms": (self._tracking_current_run_ms),
                }
            )
            self._queue_recording_event(
                "target_lost",
                monotonic_timestamp_ns=(timestamp_ns),
                payload=event_payload,
            )
            self._tracking_loss_count += 1

        self._tracking_longest_run_ms = max(
            self._tracking_longest_run_ms,
            self._tracking_current_run_ms,
        )
        self._tracking_current_run_ms = 0.0
        self._tracking_acquired = False

    def _observe_tracking_sample(
        self,
        sample: EyeTrackerSample,
    ) -> bool | None:
        timestamp_ns = sample.timestamp.monotonic_timestamp_ns
        self._ensure_tracking_started(timestamp_ns)

        if (
            self._last_tracking_timestamp_ns is None
            or timestamp_ns <= self._last_tracking_timestamp_ns
        ):
            delta_ms = 0.0
        else:
            delta_ms = min(
                250.0,
                (timestamp_ns - self._last_tracking_timestamp_ns) / 1_000_000.0,
            )

        self._advance_previous_interval(
            timestamp_ns=timestamp_ns,
            delta_ms=delta_ms,
        )
        self._tracking_sample_count += 1
        gaze_x_value = sample.gaze_x_normalized
        gaze_y_value = sample.gaze_y_normalized
        valid = bool(sample.gaze_valid and gaze_x_value is not None and gaze_y_value is not None)

        if not valid:
            self._tracking_invalid_sample_count += 1

            if self._tracking_previous_inside:
                self._leave_target(
                    timestamp_ns=timestamp_ns,
                    reason="invalid_gaze",
                    payload=dict(self._tracking_previous_payload or {}),
                )

            self._tracking_previous_valid = False
            self._tracking_previous_inside = False
            self._tracking_previous_payload = None
            self._last_tracking_timestamp_ns = timestamp_ns
            return None

        gaze_x = max(
            0.0,
            min(
                1.0,
                float(gaze_x_value),
            ),
        )
        gaze_y = max(
            0.0,
            min(
                1.0,
                float(gaze_y_value),
            ),
        )
        (
            inside_target,
            error_normalized,
            error_px,
            payload,
        ) = self._tracking_measurement(
            gaze_x,
            gaze_y,
        )
        self._tracking_valid_sample_count += 1
        self._tracking_errors_normalized.append(error_normalized)
        self._tracking_errors_px.append(error_px)

        if inside_target:
            self._tracking_inside_sample_count += 1

            if not self._tracking_previous_inside:
                started_ns = self._tracking_started_monotonic_ns or timestamp_ns

                if self._tracking_first_entry_ms is None:
                    self._tracking_first_entry_ms = max(
                        0.0,
                        (timestamp_ns - started_ns) / 1_000_000.0,
                    )

                self._tracking_current_run_ms = 0.0
                self._queue_recording_event(
                    "target_entered",
                    monotonic_timestamp_ns=(timestamp_ns),
                    payload=payload,
                )
        elif self._tracking_previous_inside:
            self._leave_target(
                timestamp_ns=timestamp_ns,
                reason="outside_target",
                payload=payload,
            )

        self._tracking_previous_valid = True
        self._tracking_previous_inside = inside_target
        self._tracking_previous_payload = payload
        self._last_tracking_timestamp_ns = timestamp_ns
        return inside_target

    def recording_result(
        self,
        reason: str,
    ) -> dict[str, object]:
        """Return tracking outcome metrics."""

        reason_text = reason.strip() if reason.strip() else "completed"
        final_timestamp_ns = self._last_tracking_timestamp_ns or monotonic_ns()
        started_ns = self._ensure_tracking_started(final_timestamp_ns)
        self._tracking_longest_run_ms = max(
            self._tracking_longest_run_ms,
            self._tracking_current_run_ms,
        )
        completed_reasons = {
            "completed",
            "test_complete",
            "timeout",
            "tracking_completed",
        }
        completion_status = "completed" if reason_text in completed_reasons else "interrupted"
        sample_count = self._tracking_sample_count
        valid_count = self._tracking_valid_sample_count
        inside_count = self._tracking_inside_sample_count
        error_count = len(self._tracking_errors_normalized)
        mean_error_normalized = (
            sum(self._tracking_errors_normalized) / error_count if error_count else None
        )
        mean_error_px = sum(self._tracking_errors_px) / error_count if error_count else None
        result = {
            **self._tracking_configuration_payload(),
            "completion_status": completion_status,
            "completion_reason": reason_text,
            "sample_count": sample_count,
            "valid_sample_count": valid_count,
            "invalid_sample_count": (self._tracking_invalid_sample_count),
            "valid_sample_ratio": (valid_count / sample_count if sample_count else 0.0),
            "target_inside_sample_count": (inside_count),
            "target_hit_ratio": (inside_count / valid_count if valid_count else 0.0),
            "valid_tracking_duration_ms": (self._tracking_valid_duration_ms),
            "target_inside_duration_ms": (self._tracking_inside_duration_ms),
            "target_hit_duration_ratio": (
                self._tracking_inside_duration_ms / self._tracking_valid_duration_ms
                if self._tracking_valid_duration_ms > 0
                else 0.0
            ),
            "recording_duration_ms": max(
                0.0,
                (final_timestamp_ns - started_ns) / 1_000_000.0,
            ),
            "first_target_entry_ms": (self._tracking_first_entry_ms),
            "first_target_acquired_ms": (self._tracking_first_acquired_ms),
            "longest_continuous_tracking_ms": (self._tracking_longest_run_ms),
            "target_loss_count": (self._tracking_loss_count),
            "target_reacquisition_count": (self._tracking_reacquisition_count),
            "target_acquired_at_finish": (self._tracking_acquired),
            "tracking_error_normalized": {
                "sample_count": error_count,
                "mean": mean_error_normalized,
                "median": self._percentile(
                    self._tracking_errors_normalized,
                    50.0,
                ),
                "p95": self._percentile(
                    self._tracking_errors_normalized,
                    95.0,
                ),
            },
            "tracking_error_px": {
                "sample_count": error_count,
                "mean": mean_error_px,
                "median": self._percentile(
                    self._tracking_errors_px,
                    50.0,
                ),
                "p95": self._percentile(
                    self._tracking_errors_px,
                    95.0,
                ),
            },
        }

        if not self._tracking_final_event_recorded:
            event_type = (
                "tracking_completed" if completion_status == "completed" else "tracking_interrupted"
            )
            self._queue_recording_event(
                event_type,
                monotonic_timestamp_ns=(final_timestamp_ns),
                payload=result,
            )
            self._tracking_final_event_recorded = True

        return result

    def start(self) -> None:
        self._dwell.reset()
        self._reset_recording_state()
        self._ensure_tracking_started(monotonic_ns())
        self._elapsed.start()
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def consume_sample(
        self,
        sample: EyeTrackerSample,
    ) -> None:
        inside_target = self._observe_tracking_sample(sample)

        if inside_target is None:
            self._invalid_sample_count += 1
            self._last_gaze_normalized = None
            self._dwell.observe(
                False,
                sample.timestamp.monotonic_timestamp_ns,
            )
            self.update()
            return

        gaze_x = max(
            0.0,
            min(
                1.0,
                float(sample.gaze_x_normalized),
            ),
        )
        gaze_y = max(
            0.0,
            min(
                1.0,
                float(sample.gaze_y_normalized),
            ),
        )

        self._valid_sample_count += 1
        self._last_gaze_normalized = (
            gaze_x,
            gaze_y,
        )
        self._dwell.observe(
            inside_target,
            sample.timestamp.monotonic_timestamp_ns,
        )
        self.update()

    @property
    def dwell_snapshot(self) -> DwellSnapshot:
        """Return current moving-target dwell state."""
        return self._dwell.snapshot

    def recording_context_for_sample(
        self,
        _sample: EyeTrackerSample,
    ) -> dict[str, object]:
        """Return the moving target AOI for recording."""

        width = max(
            1.0,
            float(self.width()),
        )
        height = max(
            1.0,
            float(self.height()),
        )
        phase = self._phase()
        center_x, center_y = self.target_center_normalized(phase)
        hit_radius_px = self.config.diameter_px / 2.0 * self.config.dwell_hit_radius_scale
        radius_x = hit_radius_px / width
        radius_y = hit_radius_px / height

        target_aoi = {
            "aoi_id": "moving_target",
            "role": "target",
            "left": max(
                0.0,
                center_x - radius_x,
            ),
            "top": max(
                0.0,
                center_y - radius_y,
            ),
            "right": min(
                1.0,
                center_x + radius_x,
            ),
            "bottom": min(
                1.0,
                center_y + radius_y,
            ),
            "label": "tracking_target",
            "metadata": {
                "center_x_normalized": center_x,
                "center_y_normalized": center_y,
                "hit_radius_px": hit_radius_px,
                "path": self.config.path.value,
                "motion_phase_radians": phase,
            },
        }

        return {
            "question_id": "tracking-target",
            "phase": (self._dwell.snapshot.phase.value),
            "aois": [target_aoi],
            "reference_aoi": target_aoi,
            "register_layout": False,
        }

    def _update_dwell(
        self,
        gaze_x: float,
        gaze_y: float,
        timestamp_ns: int,
    ) -> None:
        if self.width() <= 0 or self.height() <= 0:
            self._dwell.observe(
                False,
                timestamp_ns,
            )
            return

        phase = self._phase()
        target_x, target_y = self.target_center_normalized(phase)
        delta_x_px = (gaze_x - target_x) * self.width()
        delta_y_px = (gaze_y - target_y) * self.height()
        radius_px = self.config.diameter_px / 2.0 * self.config.dwell_hit_radius_scale
        inside_target = delta_x_px * delta_x_px + delta_y_px * delta_y_px <= radius_px * radius_px

        self._dwell.observe(
            inside_target,
            timestamp_ns,
        )

    def _paint_dwell_feedback(
        self,
        painter: QPainter,
        *,
        center: QPointF,
        diameter: float,
    ) -> None:
        snapshot = self._dwell.snapshot

        if snapshot.phase is DwellPhase.OUTSIDE:
            return

        margin = 14.0
        target_rect = QRectF(
            center.x() - diameter / 2.0,
            center.y() - diameter / 2.0,
            diameter,
            diameter,
        )
        progress_rect = QRectF(
            center.x() - diameter / 2.0 - margin,
            center.y() - diameter / 2.0 - margin,
            diameter + 2.0 * margin,
            diameter + 2.0 * margin,
        )

        painter.save()

        if snapshot.phase is DwellPhase.ACQUIRING:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(
                QPen(
                    QColor("#ffd54f"),
                    7,
                )
            )
            painter.drawEllipse(target_rect)
            painter.setPen(
                QPen(
                    QColor("#fff3a3"),
                    9,
                )
            )
            painter.drawArc(
                progress_rect,
                90 * 16,
                int(-360 * 16 * snapshot.progress),
            )
        else:
            feedback = QColor(self.config.dwell_feedback_color)
            feedback.setAlpha(210)
            painter.setBrush(feedback)
            painter.setPen(
                QPen(
                    QColor(self.config.dwell_outline_color),
                    11,
                )
            )
            painter.drawEllipse(target_rect)

        painter.restore()

    def _phase(self) -> float:
        if not self._elapsed.isValid():
            return 0.0

        elapsed_seconds = self._elapsed.elapsed() / 1_000.0

        return elapsed_seconds / self.config.period_seconds * 2.0 * pi

    def target_center_normalized(
        self,
        phase: float,
    ) -> tuple[float, float]:
        if self.config.path is TargetPath.HORIZONTAL:
            y_positions = {"top": 0.20, "middle": 0.50, "bottom": 0.80}
            return (
                0.5 + 0.38 * sin(phase),
                y_positions[self.config.horizontal_position],
            )

        if self.config.path is TargetPath.VERTICAL:
            x_positions = {"left": 0.20, "center": 0.50, "right": 0.80}
            return (
                x_positions[self.config.vertical_position],
                0.5 + 0.38 * sin(phase),
            )

        if self.config.path is TargetPath.CIRCLE:
            return (
                0.5 + 0.32 * cos(phase),
                0.5 + 0.32 * sin(phase),
            )

        if self.config.path is TargetPath.Z:
            cycle = (phase % (2.0 * pi)) / (2.0 * pi)
            path_progress = cycle * 2.0 if cycle <= 0.5 else (1.0 - cycle) * 2.0
            left = 0.15
            right = 0.85
            top = 0.20
            bottom = 0.80
            horizontal_length = right - left
            diagonal_length = ((right - left) ** 2 + (bottom - top) ** 2) ** 0.5
            segment_lengths = (
                horizontal_length,
                diagonal_length,
                horizontal_length,
            )
            distance = path_progress * sum(segment_lengths)

            if distance <= segment_lengths[0]:
                ratio = distance / segment_lengths[0]
                return (
                    left + (right - left) * ratio,
                    top,
                )

            distance -= segment_lengths[0]

            if distance <= segment_lengths[1]:
                ratio = distance / segment_lengths[1]
                return (
                    right + (left - right) * ratio,
                    top + (bottom - top) * ratio,
                )

            distance -= segment_lengths[1]
            ratio = min(
                1.0,
                distance / segment_lengths[2],
            )
            return (
                left + (right - left) * ratio,
                bottom,
            )

        if self.config.path is TargetPath.RANDOM:
            return (
                0.5 + 0.33 * sin(phase * 1.37 + 0.86 * sin(phase * 0.41)),
                0.5 + 0.31 * cos(phase * 1.73 + 0.79 * cos(phase * 0.53)),
            )

        return (
            0.5 + 0.36 * sin(phase),
            0.5 + 0.24 * sin(2.0 * phase),
        )

    def _target_path(
        self,
        diameter: float,
    ) -> QPainterPath:
        radius = diameter / 2.0
        rectangle = QRectF(
            -radius,
            -radius,
            diameter,
            diameter,
        )
        path = QPainterPath()

        if self.config.shape is TargetShape.CIRCLE:
            path.addEllipse(rectangle)
            return path

        if self.config.shape is TargetShape.SQUARE:
            path.addRoundedRect(
                rectangle,
                diameter * 0.08,
                diameter * 0.08,
            )
            return path

        if self.config.shape is TargetShape.DIAMOND:
            polygon = QPolygonF(
                [
                    QPointF(0.0, -radius),
                    QPointF(radius, 0.0),
                    QPointF(0.0, radius),
                    QPointF(-radius, 0.0),
                ]
            )
            path.addPolygon(polygon)
            path.closeSubpath()
            return path

        points: list[QPointF] = []

        for index in range(10):
            angle = -pi / 2.0 + index * pi / 5.0
            point_radius = radius if index % 2 == 0 else radius * 0.44
            points.append(
                QPointF(
                    point_radius * cos(angle),
                    point_radius * sin(angle),
                )
            )

        path.addPolygon(QPolygonF(points))
        path.closeSubpath()
        return path

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )
        painter.fillRect(
            self.rect(),
            QColor(self.config.background_color),
        )

        phase = self._phase()
        normalized_x, normalized_y = self.target_center_normalized(phase)
        center = QPointF(
            normalized_x * self.width(),
            normalized_y * self.height(),
        )

        scale = 1.0

        if self.config.effect is TargetEffect.PULSE:
            scale += 0.14 * sin(phase * 2.0)

        diameter = self.config.diameter_px * max(0.65, scale)
        path = self._target_path(diameter)

        painter.save()
        painter.translate(center)

        if self.config.effect is TargetEffect.SPIN:
            painter.rotate(phase * 180.0 / pi)

        painter.setPen(
            QPen(
                QColor("#ffffff"),
                max(2.0, diameter * 0.025),
            )
        )

        if not self._image.isNull():
            painter.save()
            painter.setClipPath(path)
            target_rectangle = QRectF(
                -diameter / 2.0,
                -diameter / 2.0,
                diameter,
                diameter,
            )
            painter.drawPixmap(
                target_rectangle,
                self._image,
                QRectF(self._image.rect()),
            )
            painter.restore()
            painter.drawPath(path)
        else:
            painter.fillPath(
                path,
                QColor(self.config.color),
            )
            painter.drawPath(path)

        painter.restore()

        self._paint_dwell_feedback(
            painter,
            center=center,
            diameter=diameter,
        )

        if self.config.show_gaze_cursor and self._last_gaze_normalized is not None:
            gaze_x, gaze_y = self._last_gaze_normalized
            gaze_point = QPointF(
                gaze_x * self.width(),
                gaze_y * self.height(),
            )
            painter.setBrush(QColor(255, 255, 255, 45))
            painter.setPen(
                QPen(
                    QColor("#40e0ff"),
                    4,
                )
            )
            painter.drawEllipse(
                gaze_point,
                18,
                18,
            )
            painter.drawLine(
                QPointF(
                    gaze_point.x() - 25,
                    gaze_point.y(),
                ),
                QPointF(
                    gaze_point.x() + 25,
                    gaze_point.y(),
                ),
            )
            painter.drawLine(
                QPointF(
                    gaze_point.x(),
                    gaze_point.y() - 25,
                ),
                QPointF(
                    gaze_point.x(),
                    gaze_point.y() + 25,
                ),
            )

        painter.setPen(QColor("#dcecff"))
        painter.drawText(
            20,
            30,
            (f"有效样本 {self._valid_sample_count} · 无效样本 {self._invalid_sample_count}"),
        )

    def mouseMoveEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        if not self.allow_mouse_fallback:
            event.ignore()
            return

        if self.width() <= 0 or self.height() <= 0:
            return

        position = event.position()
        self._last_gaze_normalized = (
            max(
                0.0,
                min(
                    1.0,
                    position.x() / self.width(),
                ),
            ),
            max(
                0.0,
                min(
                    1.0,
                    position.y() / self.height(),
                ),
            ),
        )
        self.update()


class TrackingBallSetupDialog(QDialog):
    """Configure tracking target appearance and motion."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        config: TrackingBallConfig | None = None,
        image_library_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        initial = config or TrackingBallConfig()
        self.image_store = ImageLibraryStore(
            image_library_path or (Path.home() / ".oculidoc" / "data" / "image_library")
        )
        self.setWindowTitle("追踪球设置")
        self.resize(560, 700)

        form = QFormLayout()

        self.shape_combo = QComboBox()
        self.shape_combo.addItem(
            "圆形",
            TargetShape.CIRCLE,
        )
        self.shape_combo.addItem(
            "方形",
            TargetShape.SQUARE,
        )
        self.shape_combo.addItem(
            "菱形",
            TargetShape.DIAMOND,
        )
        self.shape_combo.addItem(
            "星形",
            TargetShape.STAR,
        )
        self.shape_combo.setCurrentIndex(self.shape_combo.findData(initial.shape))
        form.addRow(
            "目标形状：",
            self.shape_combo,
        )

        self.path_combo = QComboBox()
        self.path_combo.addItem(
            "水平往返",
            TargetPath.HORIZONTAL,
        )
        self.path_combo.addItem(
            "垂直往返",
            TargetPath.VERTICAL,
        )
        self.path_combo.addItem(
            "圆周",
            TargetPath.CIRCLE,
        )
        self.path_combo.addItem(
            "Z 型轨迹",
            TargetPath.Z,
        )
        self.path_combo.addItem(
            "8 字轨迹",
            TargetPath.FIGURE_EIGHT,
        )
        self.path_combo.addItem(
            "平滑随机运动",
            TargetPath.RANDOM,
        )
        self.path_combo.setCurrentIndex(self.path_combo.findData(initial.path))
        form.addRow(
            "运动轨迹：",
            self.path_combo,
        )

        self.horizontal_position_combo = QComboBox()
        self.horizontal_position_combo.addItem("屏幕上方", "top")
        self.horizontal_position_combo.addItem("屏幕中间", "middle")
        self.horizontal_position_combo.addItem("屏幕下方", "bottom")
        self.horizontal_position_combo.setCurrentIndex(
            self.horizontal_position_combo.findData(initial.horizontal_position)
        )
        form.addRow("水平轨迹高度：", self.horizontal_position_combo)

        self.vertical_position_combo = QComboBox()
        self.vertical_position_combo.addItem("屏幕左侧", "left")
        self.vertical_position_combo.addItem("屏幕中间", "center")
        self.vertical_position_combo.addItem("屏幕右侧", "right")
        self.vertical_position_combo.setCurrentIndex(
            self.vertical_position_combo.findData(initial.vertical_position)
        )
        form.addRow("垂直轨迹位置：", self.vertical_position_combo)

        self.effect_combo = QComboBox()
        self.effect_combo.addItem(
            "无",
            TargetEffect.NONE,
        )
        self.effect_combo.addItem(
            "呼吸缩放",
            TargetEffect.PULSE,
        )
        self.effect_combo.addItem(
            "旋转",
            TargetEffect.SPIN,
        )
        self.effect_combo.setCurrentIndex(self.effect_combo.findData(initial.effect))
        form.addRow(
            "动画效果：",
            self.effect_combo,
        )

        self.diameter_spin = QSpinBox()
        self.diameter_spin.setRange(16, 600)
        self.diameter_spin.setValue(initial.diameter_px)
        self.diameter_spin.setSuffix(" px")
        form.addRow(
            "目标直径：",
            self.diameter_spin,
        )

        self.period_spin = QDoubleSpinBox()
        self.period_spin.setRange(1.0, 120.0)
        self.period_spin.setValue(initial.period_seconds)
        self.period_spin.setSingleStep(0.5)
        self.period_spin.setSuffix(" 秒/周期")
        form.addRow(
            "运动速度：",
            self.period_spin,
        )

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(
            5,
            3_600,
        )
        self.duration_spin.setValue(initial.duration_seconds)
        self.duration_spin.setSuffix(" 秒")
        form.addRow(
            "任务时长：",
            self.duration_spin,
        )

        self.dwell_time_spin = QSpinBox()
        self.dwell_time_spin.setRange(
            100,
            10_000,
        )
        self.dwell_time_spin.setValue(initial.dwell_time_ms)
        self.dwell_time_spin.setSingleStep(100)
        self.dwell_time_spin.setSuffix(" ms")
        form.addRow(
            "注视维持阈值：",
            self.dwell_time_spin,
        )

        color_row = QHBoxLayout()
        self.color_edit = QLineEdit(initial.color)
        color_button = QPushButton("选择颜色")
        color_button.clicked.connect(self._select_color)
        color_row.addWidget(self.color_edit, 1)
        color_row.addWidget(color_button)
        form.addRow(
            "填充颜色：",
            color_row,
        )

        image_row = QHBoxLayout()
        self.image_combo = QComboBox()
        image_button = QPushButton("上传到图片库…")
        library_button = QPushButton("管理图片库…")
        image_button.clicked.connect(self._select_image)
        library_button.clicked.connect(self._manage_images)
        image_row.addWidget(self.image_combo, 1)
        image_row.addWidget(image_button)
        image_row.addWidget(library_button)
        form.addRow(
            "填充图片：",
            image_row,
        )
        image_guide = QLabel(IMAGE_UPLOAD_GUIDE)
        image_guide.setWordWrap(True)
        image_guide.setStyleSheet("color:#365269; background:#eef7ff; padding:8px;")
        form.addRow("上传指引：", image_guide)
        self._reload_image_library(initial.image_path)

        self.hit_radius_spin = QDoubleSpinBox()
        self.hit_radius_spin.setRange(0.5, 2.5)
        self.hit_radius_spin.setSingleStep(0.05)
        self.hit_radius_spin.setValue(initial.dwell_hit_radius_scale)
        form.addRow("命中范围倍率：", self.hit_radius_spin)

        self.background_color_edit = QLineEdit(initial.background_color)
        form.addRow("背景颜色：", self.background_color_edit)

        self.feedback_color_edit = QLineEdit(initial.dwell_feedback_color)
        form.addRow("命中反馈颜色：", self.feedback_color_edit)

        self.outline_color_edit = QLineEdit(initial.dwell_outline_color)
        form.addRow("目标轮廓颜色：", self.outline_color_edit)

        self.show_gaze_cursor_check = QCheckBox("在任务中显示实时视线光标")
        self.show_gaze_cursor_check.setChecked(initial.show_gaze_cursor)
        form.addRow("视线反馈：", self.show_gaze_cursor_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addStretch(1)
        root.addWidget(buttons)

    def _select_color(self) -> None:
        selected = QColorDialog.getColor(
            QColor(self.color_edit.text()),
            self,
            "选择目标颜色",
        )

        if selected.isValid():
            self.color_edit.setText(selected.name())

    def _select_image(self) -> None:
        dialog = ImageAssetDialog(
            self,
            default_category="追踪球",
            default_style="自定义图片",
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        filename, label, category, style = dialog.values()

        try:
            asset = self.image_store.add_file(
                filename,
                label=label,
                category=category,
                style=style,
            )
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "无法保存追踪球图片", str(error))
            return

        path = self.image_store.resolve_path(asset)
        self._reload_image_library(str(path) if path is not None else None)

    def _manage_images(self) -> None:
        selected = self.image_combo.currentData()
        ImageLibraryDialog(self.image_store, self).exec()
        self._reload_image_library(str(selected) if selected else None)

    def _reload_image_library(self, selected_path: str | None = None) -> None:
        self.image_combo.clear()
        self.image_combo.addItem("不使用图片（显示形状和颜色）", None)
        matched = False

        for asset in self.image_store.load():
            path = self.image_store.resolve_path(asset)

            if path is None:
                continue

            path_text = str(path)
            self.image_combo.addItem(
                f"{asset.label} · {asset.category} · {asset.style}",
                path_text,
            )

            if selected_path and Path(path_text) == Path(selected_path):
                self.image_combo.setCurrentIndex(self.image_combo.count() - 1)
                matched = True

        if selected_path and not matched and Path(selected_path).is_file():
            self.image_combo.addItem("当前外部图片（旧设置）", selected_path)
            self.image_combo.setCurrentIndex(self.image_combo.count() - 1)

    def build_config(self) -> TrackingBallConfig:
        return TrackingBallConfig(
            shape=self.shape_combo.currentData(),
            effect=self.effect_combo.currentData(),
            path=self.path_combo.currentData(),
            horizontal_position=self.horizontal_position_combo.currentData(),
            vertical_position=self.vertical_position_combo.currentData(),
            diameter_px=self.diameter_spin.value(),
            color=self.color_edit.text(),
            image_path=(
                str(self.image_combo.currentData()) if self.image_combo.currentData() else None
            ),
            period_seconds=(self.period_spin.value()),
            duration_seconds=self.duration_spin.value(),
            dwell_time_ms=(self.dwell_time_spin.value()),
            dwell_feedback_color=self.feedback_color_edit.text(),
            dwell_outline_color=self.outline_color_edit.text(),
            dwell_hit_radius_scale=self.hit_radius_spin.value(),
            background_color=self.background_color_edit.text(),
            show_gaze_cursor=self.show_gaze_cursor_check.isChecked(),
        )
