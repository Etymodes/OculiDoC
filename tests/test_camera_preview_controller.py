"""Camera preview controller tests."""

from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns

import cv2
import numpy as np
import pytest

from oculidoc.devices import (
    CameraFramePacket,
    DeviceState,
    DeviceTimestamp,
    InvalidDeviceStateError,
)
from oculidoc.vision import (
    CameraPreviewController,
    EyeBoundingBox,
    EyeObservation,
    EyeOpeningState,
    EyeSide,
    bgr_frame_to_qimage,
)


class FakePreviewCamera:
    """Hardware-free camera used by preview tests."""

    def __init__(self) -> None:
        self._state = DeviceState.DISCONNECTED
        self.backend_name = "FAKE"
        self.actual_width_px = 64
        self.actual_height_px = 48
        self.actual_fps = 30.0
        self.sequence = 0

    @property
    def state(self) -> DeviceState:
        return self._state

    def connect(self) -> None:
        self._state = DeviceState.CONNECTED

    def disconnect(self) -> None:
        self._state = DeviceState.DISCONNECTED

    def start_stream(self) -> None:
        self._state = DeviceState.STREAMING

    def stop_stream(self) -> None:
        self._state = DeviceState.CONNECTED

    def read_frame(self) -> CameraFramePacket:
        image = np.zeros(
            (48, 64, 3),
            dtype=np.uint8,
        )
        image[:, :, 1] = self.sequence

        packet = CameraFramePacket(
            timestamp=DeviceTimestamp(
                sequence=self.sequence,
                monotonic_timestamp_ns=(monotonic_ns()),
                utc_timestamp=datetime.now(UTC),
            ),
            frame_index=self.sequence,
            image=image,
        )
        self.sequence += 1

        return packet


def build_controller():
    camera = FakePreviewCamera()
    calls: list[tuple[int, int | None]] = []

    def factory(
        index: int,
        backend: int | None,
    ):
        calls.append((index, backend))
        return camera

    controller = CameraPreviewController(camera_factory=factory)

    return controller, camera, calls


def test_preview_controller_lifecycle() -> None:
    controller, camera, calls = build_controller()

    controller.start(
        index=2,
        backend=123,
    )

    assert calls == [(2, 123)]
    assert controller.running is True
    assert camera.state is DeviceState.STREAMING
    assert controller.backend_name == "FAKE"
    assert controller.reported_mode == (
        64,
        48,
        30.0,
    )

    packet, rendered = controller.read_next_frame()

    assert packet.frame_index == 0
    assert rendered.shape == (48, 64, 3)
    assert controller.latest_packet is packet

    controller.stop()

    assert controller.running is False
    assert camera.state is DeviceState.DISCONNECTED


def test_preview_rejects_double_start() -> None:
    controller, _, _ = build_controller()

    controller.start(
        index=0,
        backend=None,
    )

    with pytest.raises(
        InvalidDeviceStateError,
        match="already active",
    ):
        controller.start(
            index=1,
            backend=None,
        )

    controller.stop()


def test_preview_read_requires_running_camera() -> None:
    controller, _, _ = build_controller()

    with pytest.raises(
        InvalidDeviceStateError,
        match="not running",
    ):
        controller.read_next_frame()


def test_preview_stop_is_idempotent() -> None:
    controller, _, _ = build_controller()

    controller.stop()
    controller.stop()

    assert controller.running is False


def test_preview_can_render_eye_observations() -> None:
    controller, _, _ = build_controller()
    controller.set_observations(
        [
            EyeObservation(
                side=EyeSide.LEFT,
                box=EyeBoundingBox(
                    x_px=10,
                    y_px=10,
                    width_px=20,
                    height_px=10,
                ),
                opening_state=(EyeOpeningState.OPEN),
            )
        ]
    )
    controller.start(
        index=0,
        backend=None,
    )

    packet, rendered = controller.read_next_frame()

    assert not np.array_equal(
        rendered,
        packet.image,
    )

    controller.clear_observations()
    controller.stop()


def test_preview_saves_latest_snapshot(
    tmp_path: Path,
) -> None:
    controller, _, _ = build_controller()
    controller.start(
        index=0,
        backend=None,
    )
    controller.read_next_frame()

    output_path = tmp_path / "preview.png"
    saved_path = controller.save_snapshot(output_path)

    assert saved_path == output_path
    assert output_path.exists()

    saved_image = cv2.imread(str(output_path))
    assert saved_image is not None
    assert saved_image.shape == (
        48,
        64,
        3,
    )

    controller.stop()


def test_bgr_frame_converts_to_owned_qimage() -> None:
    image = np.zeros(
        (48, 64, 3),
        dtype=np.uint8,
    )
    image[:, :, 2] = 255

    qimage = bgr_frame_to_qimage(image)

    assert qimage.width() == 64
    assert qimage.height() == 48
    assert qimage.format() == (qimage.Format.Format_RGB888)
