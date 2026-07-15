"""OpenCV camera adapter tests."""

import cv2
import numpy as np
import pytest

from oculidoc.devices import (
    CameraDevice,
    DeviceConnectionError,
    DeviceReadError,
    DeviceState,
    InvalidDeviceStateError,
    OpenCVCameraDevice,
)


class FakeCapture:
    """Configurable cv2.VideoCapture replacement."""

    def __init__(
        self,
        *,
        opened: bool = True,
        read_ok: bool = True,
    ) -> None:
        self.opened = opened
        self.read_ok = read_ok
        self.released = False
        self.set_calls: list[tuple[int, float]] = []
        self.frame = np.zeros(
            (48, 64, 3),
            dtype=np.uint8,
        )
        self.values = {
            cv2.CAP_PROP_FRAME_WIDTH: 64.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 48.0,
            cv2.CAP_PROP_FPS: 30.0,
        }

    def isOpened(self) -> bool:
        return self.opened

    def read(self):
        if not self.read_ok:
            return False, None

        return True, self.frame.copy()

    def get(self, property_id: int) -> float:
        return self.values.get(
            property_id,
            0.0,
        )

    def set(
        self,
        property_id: int,
        value: float,
    ) -> bool:
        self.set_calls.append((property_id, value))
        return True

    def getBackendName(self) -> str:
        return "FAKE"

    def release(self) -> None:
        self.released = True


def test_opencv_camera_lifecycle_and_frame() -> None:
    capture = FakeCapture()
    camera = OpenCVCameraDevice(
        capture_factory=lambda index: capture,
    )

    assert isinstance(camera, CameraDevice)
    assert camera.state is DeviceState.DISCONNECTED

    camera.connect()

    assert camera.state is DeviceState.CONNECTED
    assert camera.backend_name == "FAKE"
    assert camera.actual_width_px == 64
    assert camera.actual_height_px == 48
    assert camera.actual_fps == 30.0

    camera.start_stream()
    first = camera.read_frame()
    second = camera.read_frame()

    assert first.frame_index == 0
    assert second.frame_index == 1
    assert first.timestamp.sequence == 0
    assert first.timestamp.source_timestamp_ns is None
    assert first.timestamp.source_clock_id is None
    assert first.image.shape == (48, 64, 3)

    camera.stop_stream()
    camera.disconnect()

    assert camera.state is DeviceState.DISCONNECTED
    assert capture.released is True


def test_camera_applies_requested_configuration() -> None:
    capture = FakeCapture()
    factory_calls: list[tuple[int, int]] = []

    def factory(index: int, backend: int):
        factory_calls.append((index, backend))
        return capture

    camera = OpenCVCameraDevice(
        index=2,
        backend=123,
        requested_width_px=1920,
        requested_height_px=1080,
        requested_fps=60.0,
        capture_factory=factory,
    )

    camera.connect()

    assert factory_calls == [(2, 123)]
    assert capture.set_calls == [
        (
            cv2.CAP_PROP_FRAME_WIDTH,
            1920.0,
        ),
        (
            cv2.CAP_PROP_FRAME_HEIGHT,
            1080.0,
        ),
        (
            cv2.CAP_PROP_FPS,
            60.0,
        ),
    ]

    camera.disconnect()


def test_open_failure_releases_capture() -> None:
    capture = FakeCapture(
        opened=False,
    )
    camera = OpenCVCameraDevice(
        capture_factory=lambda index: capture,
    )

    with pytest.raises(
        DeviceConnectionError,
        match="could not be opened",
    ):
        camera.connect()

    assert capture.released is True
    assert camera.state is DeviceState.DISCONNECTED


def test_read_failure_raises_device_error() -> None:
    capture = FakeCapture(
        read_ok=False,
    )
    camera = OpenCVCameraDevice(
        capture_factory=lambda index: capture,
    )

    camera.connect()
    camera.start_stream()

    with pytest.raises(
        DeviceReadError,
        match="valid frame",
    ):
        camera.read_frame()

    camera.stop_stream()
    camera.disconnect()


def test_camera_rejects_invalid_transitions() -> None:
    capture = FakeCapture()
    camera = OpenCVCameraDevice(
        capture_factory=lambda index: capture,
    )

    with pytest.raises(
        InvalidDeviceStateError,
    ):
        camera.start_stream()

    with pytest.raises(
        InvalidDeviceStateError,
    ):
        camera.read_frame()

    camera.connect()
    camera.start_stream()

    with pytest.raises(
        InvalidDeviceStateError,
    ):
        camera.disconnect()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"index": -1},
        {"requested_width_px": 0},
        {"requested_height_px": 0},
        {"requested_fps": 0},
    ],
)
def test_camera_validates_configuration(
    kwargs,
) -> None:
    with pytest.raises(ValueError):
        OpenCVCameraDevice(**kwargs)
