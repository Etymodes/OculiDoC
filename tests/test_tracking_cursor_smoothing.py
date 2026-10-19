"""Display-filter behavior; synthetic motion does not establish human gaze accuracy."""

from datetime import UTC, datetime

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample
from oculidoc.tasks import gaze_cursor
from oculidoc.tasks.gaze_cursor import GazeCursorFilter
from oculidoc.tasks.tracking_ball import (
    TargetEffect,
    TargetPath,
    TrackingBallConfig,
    TrackingBallTask,
)
from oculidoc.tasks.tracking_dwell import DwellPhase


@pytest.fixture
def clock(monkeypatch):
    now = [10_000_000_000]
    monkeypatch.setattr(gaze_cursor, "monotonic_ns", lambda: now[0])
    return now


def sample(x, y, timestamp_ns, *, valid=True):
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=0,
            monotonic_timestamp_ns=timestamp_ns,
            utc_timestamp=datetime(2026, 10, 19, tzinfo=UTC),
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=valid,
        right_eye_valid=valid,
    )


def filtered(points, fps, clock):
    cursor = GazeCursorFilter()
    output = []
    for index, point in enumerate(points):
        timestamp_ns = 1_000_000_000 + round(index * 1e9 / fps)
        clock[0] = 10_000_000_000 + timestamp_ns
        cursor.consume_sample(sample(*point, timestamp_ns))
        output.append(cursor.position)
    return np.asarray(output)


@pytest.mark.parametrize("fps", [60, 120, 300])
def test_stationary_jitter_is_suppressed_at_hardware_sample_rates(fps, clock):
    rng = np.random.default_rng(20261019)
    target = np.array([0.45, 0.55])
    points = target + rng.normal(0, 0.025, (fps * 6, 2))
    output = filtered(points, fps, clock)
    raw_rms = np.sqrt(np.mean((points[fps:] - target) ** 2))
    display_rms = np.sqrt(np.mean((output[fps:] - target) ** 2))
    assert display_rms < 0.55 * raw_rms
    assert np.linalg.norm(output[fps:].mean(axis=0) - target) < 0.005


@pytest.mark.parametrize("fps", [60, 120, 300])
def test_isolated_spike_does_not_move_a_settled_cursor(fps, clock):
    points = np.tile([0.5, 0.5], (fps * 2, 1))
    points[fps] = [0.98, 0.02]
    output = filtered(points, fps, clock)
    assert np.max(np.abs(output - 0.5)) < 1e-10


@pytest.mark.parametrize("fps", [60, 120, 300])
def test_sustained_shift_arrives_promptly_without_overshoot(fps, clock):
    points = np.vstack([np.tile([0.2, 0.7], (fps, 1)), np.tile([0.8, 0.2], (fps, 1))])
    output = filtered(points, fps, clock)
    after = output[fps + round(0.15 * fps)]
    assert after[0] >= 0.74 and after[1] <= 0.25
    assert np.all((output[:, 0] >= 0.2) & (output[:, 0] <= 0.8))
    assert np.all((output[:, 1] >= 0.2) & (output[:, 1] <= 0.7))


@pytest.mark.parametrize("fps", [60, 120, 300])
def test_continuous_motion_remains_continuous_and_small_shifts_are_not_frozen(fps, clock):
    points = np.column_stack([np.linspace(0.2, 0.8, fps * 3)] * 2)
    output = filtered(points, fps, clock)
    assert np.all(np.diff(output[:, 0]) > 0)
    assert np.max(np.abs(output[fps:] - points[fps:])) < 0.035
    small_shift = np.vstack([np.tile([0.4, 0.4], (fps, 1)), np.tile([0.42, 0.42], (fps, 1))])
    assert filtered(small_shift, fps, clock)[-1] == pytest.approx([0.42, 0.42], abs=0.001)


@pytest.mark.parametrize("invalid", [(None, 0.5, True), (0.5, 0.5, False), (-0.1, 0.5, True)])
def test_invalid_sample_hides_cursor_and_next_valid_point_starts_fresh(invalid, clock):
    cursor = GazeCursorFilter()
    cursor.consume_sample(sample(0.2, 0.2, 1_000_000_000))
    x, y, valid = invalid
    cursor.consume_sample(sample(x, y, 1_010_000_000, valid=valid))
    assert cursor.position is None
    cursor.consume_sample(sample(0.8, 0.7, 1_020_000_000))
    assert cursor.position == (0.8, 0.7)


@pytest.mark.parametrize("read_before_resume", [False, True])
def test_missing_stream_expires_on_received_clock_and_resumes_without_old_history(
    read_before_resume, clock
):
    cursor = GazeCursorFilter()
    cursor.consume_sample(sample(0.2, 0.2, 1_000_000_000))
    clock[0] += 250_000_001
    if read_before_resume:
        assert cursor.position is None
    # Device time advances by only one sample: receiver silence must still reset it.
    cursor.consume_sample(sample(0.8, 0.7, 1_008_333_333))
    assert cursor.position == (0.8, 0.7)


@pytest.mark.parametrize("next_timestamp", [999_000_000, 1_000_000_000, 1_250_000_001])
def test_timestamp_regression_duplicate_or_long_gap_resets_filter(next_timestamp, clock):
    cursor = GazeCursorFilter()
    cursor.consume_sample(sample(0.2, 0.2, 1_000_000_000))
    cursor.consume_sample(sample(0.8, 0.7, next_timestamp))
    assert cursor.position == (0.8, 0.7)


def tracking_task(qtbot, monkeypatch, *, allow_mouse=True):
    task = TrackingBallTask(
        TrackingBallConfig(
            path=TargetPath.HORIZONTAL,
            effect=TargetEffect.NONE,
            diameter_px=100,
            period_seconds=12.0,
            dwell_time_ms=400,
            dwell_hit_radius_scale=1.0,
        ),
        allow_mouse_fallback=allow_mouse,
    )
    qtbot.addWidget(task)
    task.resize(1_000, 500)
    monkeypatch.setattr(task, "_phase", lambda: 0.0)
    return task


def test_display_rejects_spike_but_raw_tracking_dwell_events_and_results_keep_the_miss(
    qtbot, monkeypatch, clock
):
    task = tracking_task(qtbot, monkeypatch)
    for index, x in enumerate([0.5, 0.5, 0.5, 0.9]):
        timestamp_ns = 1_000_000_000 + index * 200_000_000
        clock[0] = 10_000_000_000 + timestamp_ns
        task.consume_sample(sample(x, 0.5, timestamp_ns))
    assert task._gaze_cursor.position == (0.5, 0.5)
    assert task.last_gaze_normalized == (0.9, 0.5)
    assert task.dwell_snapshot.phase is DwellPhase.OUTSIDE
    assert task.dwell_snapshot.success_count == 1
    events = task.drain_recording_events()
    assert [event["event_type"] for event in events] == [
        "tracking_started",
        "target_entered",
        "target_acquired",
        "target_lost",
    ]
    assert events[-1]["payload"]["gaze_x_normalized"] == 0.9
    result = task.recording_result("timeout")
    assert result["sample_count"] == result["valid_sample_count"] == 4
    assert result["target_inside_sample_count"] == 3
    assert result["target_hit_ratio"] == 0.75
    assert result["target_loss_count"] == 1
    assert result["tracking_error_normalized"]["mean"] == pytest.approx(0.1)


@pytest.mark.parametrize("allow_mouse", [False, True])
def test_mouse_demo_is_immediate_but_cannot_move_a_hardware_cursor(
    allow_mouse, qtbot, monkeypatch, clock
):
    task = tracking_task(qtbot, monkeypatch, allow_mouse=allow_mouse)
    task.consume_sample(sample(0.5, 0.5, 1_000_000_000))
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(800, 100),
        QPointF(800, 100),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    task.mouseMoveEvent(event)
    expected = (0.8, 0.2) if allow_mouse else (0.5, 0.5)
    assert task._gaze_cursor.position == expected
    assert task.last_gaze_normalized == expected
    assert task.recording_result("timeout")["sample_count"] == 1


def test_task_restart_and_stop_clear_display_history(qtbot, monkeypatch, clock):
    task = tracking_task(qtbot, monkeypatch)
    task.consume_sample(sample(0.2, 0.2, 1_000_000_000))
    task.start()
    assert task._gaze_cursor.position is None
    task.consume_sample(sample(0.8, 0.7, 1_010_000_000))
    assert task._gaze_cursor.position == (0.8, 0.7)
    task.stop()
    assert task._gaze_cursor.position is None


def test_painted_crosshair_uses_filtered_location_instead_of_raw_spike(qtbot, monkeypatch, clock):
    task = tracking_task(qtbot, monkeypatch)
    for index, point in enumerate([(0.25, 0.75)] * 3 + [(0.8, 0.2)]):
        task.consume_sample(sample(*point, 1_000_000_000 + index * 8_333_333))
    assert task.last_gaze_normalized == (0.8, 0.2)
    image = task.grab().toImage().scaled(task.size())

    def cyan_pixels(center_x, center_y):
        count = 0
        for y in range(center_y - 28, center_y + 29):
            for x in range(center_x - 28, center_x + 29):
                color = image.pixelColor(x, y)
                count += color.red() < 130 and color.green() > 180 and color.blue() > 180
        return count

    assert cyan_pixels(250, 375) > 300
    assert cyan_pixels(800, 100) == 0
