"""Simulated camera and eye-tracker tests."""

import numpy as np
import pytest

from oculidoc.devices import (
    CameraDevice,
    DeviceKind,
    DeviceState,
    DeviceStreamEndedError,
    EyeTrackerDevice,
    InvalidDeviceStateError,
    SimulatedCameraDevice,
    SimulatedEyeTrackerDevice,
)


def test_simulated_camera_lifecycle() -> None:
    camera = SimulatedCameraDevice(
        width_px=320,
        height_px=240,
        fps=30.0,
    )

    assert isinstance(camera, CameraDevice)
    assert camera.info.kind is DeviceKind.CAMERA
    assert camera.info.is_simulated is True
    assert camera.state is DeviceState.DISCONNECTED

    camera.connect()
    assert camera.state is DeviceState.CONNECTED

    camera.start_stream()
    assert camera.state is DeviceState.STREAMING

    frame = camera.read_frame()

    assert frame.frame_index == 0
    assert frame.timestamp.sequence == 0
    assert frame.width_px == 320
    assert frame.height_px == 240
    assert frame.channel_count == 3
    assert frame.image.dtype == np.uint8
    assert frame.image.shape == (240, 320, 3)

    camera.stop_stream()
    assert camera.state is DeviceState.CONNECTED

    camera.disconnect()
    assert camera.state is DeviceState.DISCONNECTED


def test_device_rejects_invalid_state_transitions() -> None:
    camera = SimulatedCameraDevice()

    with pytest.raises(InvalidDeviceStateError):
        camera.read_frame()

    with pytest.raises(InvalidDeviceStateError):
        camera.start_stream()

    camera.connect()
    camera.start_stream()

    with pytest.raises(InvalidDeviceStateError):
        camera.disconnect()


def test_simulated_camera_sequence_and_finite_stream() -> None:
    camera = SimulatedCameraDevice(
        width_px=64,
        height_px=48,
        max_frames=2,
    )
    camera.connect()
    camera.start_stream()

    first = camera.read_frame()
    second = camera.read_frame()

    assert first.frame_index == 0
    assert second.frame_index == 1
    assert first.timestamp.source_timestamp_ns == 0
    assert second.timestamp.source_timestamp_ns > first.timestamp.source_timestamp_ns
    assert second.timestamp.monotonic_timestamp_ns >= first.timestamp.monotonic_timestamp_ns
    assert not np.array_equal(
        first.image,
        second.image,
    )

    with pytest.raises(DeviceStreamEndedError):
        camera.read_frame()


def test_simulated_eye_tracker_lifecycle_and_samples() -> None:
    tracker = SimulatedEyeTrackerDevice(
        sample_rate_hz=60.0,
        path_period_samples=20,
    )

    assert isinstance(tracker, EyeTrackerDevice)
    assert tracker.info.kind is DeviceKind.EYE_TRACKER

    tracker.connect()
    tracker.start_stream()

    first = tracker.read_sample()
    second = tracker.read_sample()

    assert first.timestamp.sequence == 0
    assert second.timestamp.sequence == 1
    assert first.gaze_valid is True
    assert second.gaze_valid is True

    assert first.gaze_x_normalized is not None
    assert first.gaze_y_normalized is not None
    assert 0.0 <= first.gaze_x_normalized <= 1.0
    assert 0.0 <= first.gaze_y_normalized <= 1.0

    tracker.stop_stream()
    tracker.disconnect()

    assert tracker.state is DeviceState.DISCONNECTED


def test_simulated_eye_tracker_can_generate_invalid_samples() -> None:
    tracker = SimulatedEyeTrackerDevice(
        invalid_every_n=3,
    )
    tracker.connect()
    tracker.start_stream()

    samples = [tracker.read_sample() for _ in range(6)]

    assert samples[0].gaze_valid is True
    assert samples[1].gaze_valid is True
    assert samples[2].gaze_valid is False
    assert samples[2].gaze_x_normalized is None
    assert samples[2].gaze_y_normalized is None

    assert samples[5].gaze_valid is False


def test_simulated_eye_tracker_finite_stream() -> None:
    tracker = SimulatedEyeTrackerDevice(
        max_samples=1,
    )
    tracker.connect()
    tracker.start_stream()

    tracker.read_sample()

    with pytest.raises(DeviceStreamEndedError):
        tracker.read_sample()


@pytest.mark.parametrize(
    ("factory", "error_message"),
    [
        (
            lambda: SimulatedCameraDevice(width_px=0),
            "dimensions",
        ),
        (
            lambda: SimulatedCameraDevice(fps=0),
            "FPS",
        ),
        (
            lambda: SimulatedEyeTrackerDevice(sample_rate_hz=0),
            "sample_rate_hz",
        ),
        (
            lambda: SimulatedEyeTrackerDevice(invalid_every_n=0),
            "invalid_every_n",
        ),
    ],
)
def test_simulated_devices_validate_configuration(
    factory,
    error_message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=error_message,
    ):
        factory()
