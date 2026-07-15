"""OpenCV-backed camera acquisition device."""

from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic_ns
from typing import Any, Protocol

import cv2
import numpy as np

from oculidoc.devices.contracts import (
    CameraFramePacket,
    DeviceInfo,
    DeviceKind,
    DeviceState,
    DeviceTimestamp,
)
from oculidoc.devices.errors import (
    DeviceConnectionError,
    DeviceReadError,
    InvalidDeviceStateError,
)


class CaptureProtocol(Protocol):
    """Minimal OpenCV capture interface used by the adapter."""

    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, Any]: ...

    def get(self, property_id: int) -> float: ...

    def set(
        self,
        property_id: int,
        value: float,
    ) -> bool: ...

    def getBackendName(self) -> str: ...

    def release(self) -> None: ...


CaptureFactory = Callable[..., CaptureProtocol]


def _positive_integer(value: float) -> int | None:
    if not np.isfinite(value) or value <= 0:
        return None

    return int(round(value))


def _positive_float(value: float) -> float | None:
    if not np.isfinite(value) or value <= 0:
        return None

    return float(value)


class OpenCVCameraDevice:
    """CameraDevice implementation backed by cv2.VideoCapture."""

    def __init__(
        self,
        *,
        index: int = 0,
        backend: int | None = None,
        requested_width_px: int | None = None,
        requested_height_px: int | None = None,
        requested_fps: float | None = None,
        device_id: str | None = None,
        capture_factory: CaptureFactory | None = None,
    ) -> None:
        if index < 0:
            raise ValueError("Camera index cannot be negative.")

        if requested_width_px is not None and requested_width_px <= 0:
            raise ValueError("requested_width_px must be positive.")

        if requested_height_px is not None and requested_height_px <= 0:
            raise ValueError("requested_height_px must be positive.")

        if requested_fps is not None and requested_fps <= 0:
            raise ValueError("requested_fps must be positive.")

        resolved_device_id = device_id or f"opencv-camera-{index}"

        self._info = DeviceInfo(
            device_id=resolved_device_id,
            kind=DeviceKind.CAMERA,
            name=f"OpenCV Camera {index}",
            manufacturer="OpenCV",
            model="VideoCapture",
            serial_number=None,
            is_simulated=False,
            capabilities=(
                "bgr8",
                "polling",
                "host_timestamp",
            ),
        )
        self._index = index
        self._backend = backend
        self._requested_width_px = requested_width_px
        self._requested_height_px = requested_height_px
        self._requested_fps = requested_fps
        self._capture_factory = capture_factory or cv2.VideoCapture

        self._state = DeviceState.DISCONNECTED
        self._capture: CaptureProtocol | None = None
        self._backend_name: str | None = None
        self._frame_index = 0

    @property
    def info(self) -> DeviceInfo:
        """Return stable camera metadata."""
        return self._info

    @property
    def state(self) -> DeviceState:
        """Return the camera lifecycle state."""
        return self._state

    @property
    def index(self) -> int:
        """Return the operating-system camera index."""
        return self._index

    @property
    def backend_name(self) -> str | None:
        """Return the backend reported by OpenCV."""
        return self._backend_name

    def _require_capture(self) -> CaptureProtocol:
        if self._capture is None:
            raise InvalidDeviceStateError("The camera is not connected.")

        return self._capture

    def _create_capture(self) -> CaptureProtocol:
        try:
            if self._backend is None:
                return self._capture_factory(self._index)

            return self._capture_factory(
                self._index,
                self._backend,
            )
        except Exception as error:
            raise DeviceConnectionError(
                f"Could not create camera index {self._index}: {error}"
            ) from error

    def _apply_requested_configuration(
        self,
        capture: CaptureProtocol,
    ) -> None:
        settings = (
            (
                cv2.CAP_PROP_FRAME_WIDTH,
                self._requested_width_px,
            ),
            (
                cv2.CAP_PROP_FRAME_HEIGHT,
                self._requested_height_px,
            ),
            (
                cv2.CAP_PROP_FPS,
                self._requested_fps,
            ),
        )

        for property_id, value in settings:
            if value is not None:
                capture.set(
                    property_id,
                    float(value),
                )

    def connect(self) -> None:
        """Open the configured operating-system camera."""
        if self._state is not DeviceState.DISCONNECTED:
            raise InvalidDeviceStateError("Only a disconnected camera can connect.")

        capture = self._create_capture()

        if not capture.isOpened():
            try:
                capture.release()
            finally:
                raise DeviceConnectionError(f"Camera index {self._index} could not be opened.")

        try:
            self._apply_requested_configuration(capture)

            try:
                backend_name = capture.getBackendName()
            except (
                AttributeError,
                cv2.error,
            ):
                backend_name = None
        except Exception:
            capture.release()
            raise

        self._capture = capture
        self._backend_name = backend_name
        self._frame_index = 0
        self._state = DeviceState.CONNECTED

    def disconnect(self) -> None:
        """Release a connected, non-streaming camera."""
        if self._state is DeviceState.STREAMING:
            raise InvalidDeviceStateError("Stop streaming before disconnecting.")

        if self._state is not DeviceState.CONNECTED:
            raise InvalidDeviceStateError("Only a connected camera can disconnect.")

        capture = self._require_capture()

        try:
            capture.release()
        finally:
            self._capture = None
            self._backend_name = None
            self._state = DeviceState.DISCONNECTED

    def start_stream(self) -> None:
        """Enter the streaming state."""
        if self._state is not DeviceState.CONNECTED:
            raise InvalidDeviceStateError("Only a connected camera can start.")

        self._state = DeviceState.STREAMING

    def stop_stream(self) -> None:
        """Leave the streaming state without releasing hardware."""
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("Only a streaming camera can stop.")

        self._state = DeviceState.CONNECTED

    def read_frame(self) -> CameraFramePacket:
        """Read one BGR image and attach host timestamps."""
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("The camera is not streaming.")

        capture = self._require_capture()

        try:
            read_ok, image = capture.read()
        except Exception as error:
            raise DeviceReadError(
                f"Camera index {self._index} raised while reading: {error}"
            ) from error

        if not read_ok or not isinstance(image, np.ndarray) or image.size == 0:
            raise DeviceReadError(f"Camera index {self._index} did not return a valid frame.")

        if image.dtype != np.uint8:
            raise DeviceReadError("OpenCV camera frames must use uint8 pixels.")

        frame_index = self._frame_index
        timestamp = DeviceTimestamp(
            sequence=frame_index,
            monotonic_timestamp_ns=monotonic_ns(),
            utc_timestamp=datetime.now(UTC),
            source_timestamp_ns=None,
            source_clock_id=None,
        )
        packet = CameraFramePacket(
            timestamp=timestamp,
            frame_index=frame_index,
            image=image,
            pixel_format="BGR8",
        )

        self._frame_index += 1

        return packet

    @property
    def actual_width_px(self) -> int | None:
        """Return the width currently reported by OpenCV."""
        if self._capture is None:
            return None

        return _positive_integer(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))

    @property
    def actual_height_px(self) -> int | None:
        """Return the height currently reported by OpenCV."""
        if self._capture is None:
            return None

        return _positive_integer(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

    @property
    def actual_fps(self) -> float | None:
        """Return the frame rate currently reported by OpenCV."""
        if self._capture is None:
            return None

        return _positive_float(self._capture.get(cv2.CAP_PROP_FPS))
