"""Camera preview controller and Qt image conversion."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage

from oculidoc.devices.contracts import (
    CameraFramePacket,
    DeviceState,
)
from oculidoc.devices.errors import (
    InvalidDeviceStateError,
)
from oculidoc.devices.opencv_camera import (
    OpenCVCameraDevice,
)
from oculidoc.vision.eye_observation import (
    EyeObservation,
)
from oculidoc.vision.overlay import (
    draw_eye_observations,
)


class PreviewCameraProtocol(Protocol):
    """Camera operations required by the preview controller."""

    @property
    def state(self) -> DeviceState: ...

    @property
    def backend_name(self) -> str | None: ...

    @property
    def actual_width_px(self) -> int | None: ...

    @property
    def actual_height_px(self) -> int | None: ...

    @property
    def actual_fps(self) -> float | None: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def start_stream(self) -> None: ...

    def stop_stream(self) -> None: ...

    def read_frame(self) -> CameraFramePacket: ...


CameraFactory = Callable[
    [int, int | None],
    PreviewCameraProtocol,
]


def bgr_frame_to_qimage(
    image: NDArray[np.uint8],
) -> QImage:
    """Convert a BGR uint8 frame into an owned RGB QImage."""
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array.")

    if image.dtype != np.uint8:
        raise ValueError("image must use uint8 pixels.")

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a three-channel BGR frame.")

    if image.size == 0:
        raise ValueError("image cannot be empty.")

    rgb_image = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2RGB,
    )
    height_px, width_px, channel_count = rgb_image.shape
    bytes_per_line = width_px * channel_count

    return QImage(
        rgb_image.data,
        width_px,
        height_px,
        bytes_per_line,
        QImage.Format.Format_RGB888,
    ).copy()


class CameraPreviewController:
    """Lifecycle and frame handling for one preview camera."""

    def __init__(
        self,
        *,
        camera_factory: CameraFactory | None = None,
    ) -> None:
        self._camera_factory = camera_factory or self._default_factory
        self._camera: PreviewCameraProtocol | None = None
        self._latest_packet: CameraFramePacket | None = None
        self._observations: tuple[
            EyeObservation,
            ...,
        ] = ()

    @staticmethod
    def _default_factory(
        index: int,
        backend: int | None,
    ) -> OpenCVCameraDevice:
        return OpenCVCameraDevice(
            index=index,
            backend=backend,
        )

    @property
    def running(self) -> bool:
        """Return whether the camera stream is active."""
        return self._camera is not None and self._camera.state is DeviceState.STREAMING

    @property
    def latest_packet(
        self,
    ) -> CameraFramePacket | None:
        """Return the most recently acquired raw frame."""
        return self._latest_packet

    @property
    def backend_name(self) -> str | None:
        """Return the active OpenCV backend name."""
        if self._camera is None:
            return None

        return self._camera.backend_name

    @property
    def reported_mode(
        self,
    ) -> tuple[int | None, int | None, float | None]:
        """Return width, height, and FPS reported by OpenCV."""
        if self._camera is None:
            return None, None, None

        return (
            self._camera.actual_width_px,
            self._camera.actual_height_px,
            self._camera.actual_fps,
        )

    def set_observations(
        self,
        observations: Sequence[EyeObservation],
    ) -> None:
        """Set eye observations drawn over future frames."""
        self._observations = tuple(observations)

    def clear_observations(self) -> None:
        """Remove all preview overlays."""
        self._observations = ()

    def start(
        self,
        *,
        index: int,
        backend: int | None,
    ) -> None:
        """Create, connect, and start one camera."""
        if self._camera is not None:
            raise InvalidDeviceStateError("The preview camera is already active.")

        camera = self._camera_factory(
            index,
            backend,
        )

        try:
            camera.connect()
            camera.start_stream()
        except Exception:
            if camera.state is DeviceState.STREAMING:
                camera.stop_stream()

            if camera.state is DeviceState.CONNECTED:
                camera.disconnect()

            raise

        self._camera = camera
        self._latest_packet = None

    def stop(self) -> None:
        """Best-effort stop and release of the active camera."""
        camera = self._camera

        if camera is None:
            return

        try:
            if camera.state is DeviceState.STREAMING:
                camera.stop_stream()
        finally:
            if camera.state is DeviceState.CONNECTED:
                camera.disconnect()

            self._camera = None
            self._latest_packet = None

    def read_next_frame(
        self,
    ) -> tuple[CameraFramePacket, NDArray[np.uint8]]:
        """Read one frame and return raw and rendered images."""
        if not self.running or self._camera is None:
            raise InvalidDeviceStateError("The preview camera is not running.")

        packet = self._camera.read_frame()
        self._latest_packet = packet

        if self._observations:
            rendered = draw_eye_observations(
                packet.image,
                self._observations,
            )
        else:
            rendered = packet.image.copy()

        return packet, rendered

    def render_latest_frame(
        self,
    ) -> NDArray[np.uint8]:
        """Render the latest frame with current observations."""
        if self._latest_packet is None:
            raise InvalidDeviceStateError("No camera frame is available to render.")

        if self._observations:
            return draw_eye_observations(
                self._latest_packet.image,
                self._observations,
            )

        return self._latest_packet.image.copy()

    def save_snapshot(
        self,
        output_path: str | Path,
        *,
        rendered: bool = False,
    ) -> Path:
        """Save the latest raw or rendered camera frame."""
        if self._latest_packet is None:
            raise InvalidDeviceStateError("No camera frame is available to save.")

        path = Path(output_path)
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        image = self.render_latest_frame() if rendered else self._latest_packet.image

        write_ok = cv2.imwrite(
            str(path),
            image,
        )

        if not write_ok:
            raise RuntimeError(f"Could not save snapshot: {path}")

        return path
