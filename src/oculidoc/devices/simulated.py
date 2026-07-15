"""Deterministic simulated acquisition devices."""

from datetime import UTC, datetime
from math import cos, pi, sin
from time import monotonic_ns, perf_counter, sleep

import numpy as np

from oculidoc.devices.contracts import (
    CameraFramePacket,
    DeviceInfo,
    DeviceKind,
    DeviceState,
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.devices.errors import (
    DeviceStreamEndedError,
    InvalidDeviceStateError,
)


class _SimulatedDeviceBase:
    """Shared lifecycle implementation for simulated devices."""

    def __init__(self, info: DeviceInfo) -> None:
        self._info = info
        self._state = DeviceState.DISCONNECTED

    @property
    def info(self) -> DeviceInfo:
        """Return stable simulated device metadata."""
        return self._info

    @property
    def state(self) -> DeviceState:
        """Return the current lifecycle state."""
        return self._state

    def connect(self) -> None:
        """Connect a previously disconnected device."""
        if self._state is not DeviceState.DISCONNECTED:
            raise InvalidDeviceStateError("Only a disconnected device can connect.")

        self._state = DeviceState.CONNECTED

    def disconnect(self) -> None:
        """Disconnect a non-streaming device."""
        if self._state is DeviceState.STREAMING:
            raise InvalidDeviceStateError("Stop streaming before disconnecting.")

        if self._state is not DeviceState.CONNECTED:
            raise InvalidDeviceStateError("Only a connected device can disconnect.")

        self._state = DeviceState.DISCONNECTED

    def start_stream(self) -> None:
        """Start a connected device stream."""
        if self._state is not DeviceState.CONNECTED:
            raise InvalidDeviceStateError("Only a connected device can start streaming.")

        self._state = DeviceState.STREAMING
        self._on_stream_started()

    def stop_stream(self) -> None:
        """Stop an active device stream."""
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("Only a streaming device can stop.")

        self._on_stream_stopped()
        self._state = DeviceState.CONNECTED

    def _require_streaming(self) -> None:
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("The device is not streaming.")

    def _on_stream_started(self) -> None:
        """Hook used by subclasses."""

    def _on_stream_stopped(self) -> None:
        """Hook used by subclasses."""


class SimulatedCameraDevice(_SimulatedDeviceBase):
    """Generate deterministic BGR frames without camera hardware."""

    def __init__(
        self,
        *,
        width_px: int = 640,
        height_px: int = 480,
        fps: float = 30.0,
        max_frames: int | None = None,
        realtime: bool = False,
        device_id: str = "sim-camera-0",
    ) -> None:
        if width_px <= 0 or height_px <= 0:
            raise ValueError("Camera dimensions must be positive.")

        if fps <= 0:
            raise ValueError("Camera FPS must be positive.")

        if max_frames is not None and max_frames < 0:
            raise ValueError("max_frames cannot be negative.")

        super().__init__(
            DeviceInfo(
                device_id=device_id,
                kind=DeviceKind.CAMERA,
                name="Simulated Camera",
                manufacturer="OculiDoC",
                model="Deterministic Camera",
                serial_number=device_id,
                is_simulated=True,
                capabilities=(
                    "bgr8",
                    "polling",
                    "finite_stream",
                ),
            )
        )

        self.width_px = width_px
        self.height_px = height_px
        self.fps = float(fps)
        self.max_frames = max_frames
        self.realtime = realtime

        self._sequence = 0
        self._stream_started_at: float | None = None
        self._interval_seconds = 1.0 / self.fps
        self._interval_ns = int(round(1_000_000_000 / self.fps))

    def _on_stream_started(self) -> None:
        self._sequence = 0
        self._stream_started_at = perf_counter()

    def _on_stream_stopped(self) -> None:
        self._stream_started_at = None

    def _wait_for_frame_time(self) -> None:
        if not self.realtime:
            return

        if self._stream_started_at is None:
            return

        target = self._stream_started_at + self._sequence * self._interval_seconds
        delay = target - perf_counter()

        if delay > 0:
            sleep(delay)

    def _create_image(
        self,
        sequence: int,
    ) -> np.ndarray:
        image = np.zeros(
            (
                self.height_px,
                self.width_px,
                3,
            ),
            dtype=np.uint8,
        )

        image[:, :, 1] = (sequence * 7) % 255

        x_position = sequence % self.width_px
        y_position = (sequence * 2) % self.height_px

        image[y_position, :, :] = 255
        image[:, x_position, :] = 255

        return image

    def read_frame(self) -> CameraFramePacket:
        """Generate the next deterministic camera frame."""
        self._require_streaming()

        if self.max_frames is not None and self._sequence >= self.max_frames:
            raise DeviceStreamEndedError("The simulated camera stream has ended.")

        self._wait_for_frame_time()

        sequence = self._sequence
        timestamp = DeviceTimestamp(
            sequence=sequence,
            source_timestamp_ns=(sequence * self._interval_ns),
            source_clock_id="simulated-shared-clock",
            monotonic_timestamp_ns=monotonic_ns(),
            utc_timestamp=datetime.now(UTC),
        )
        packet = CameraFramePacket(
            timestamp=timestamp,
            frame_index=sequence,
            image=self._create_image(sequence),
            pixel_format="BGR8",
        )

        self._sequence += 1

        return packet


class SimulatedEyeTrackerDevice(_SimulatedDeviceBase):
    """Generate deterministic gaze samples without eye hardware."""

    def __init__(
        self,
        *,
        sample_rate_hz: float = 60.0,
        path_period_samples: int = 120,
        invalid_every_n: int | None = None,
        max_samples: int | None = None,
        realtime: bool = False,
        device_id: str = "sim-eye-tracker-0",
    ) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive.")

        if path_period_samples <= 0:
            raise ValueError("path_period_samples must be positive.")

        if invalid_every_n is not None and invalid_every_n <= 0:
            raise ValueError("invalid_every_n must be positive.")

        if max_samples is not None and max_samples < 0:
            raise ValueError("max_samples cannot be negative.")

        super().__init__(
            DeviceInfo(
                device_id=device_id,
                kind=DeviceKind.EYE_TRACKER,
                name="Simulated Eye Tracker",
                manufacturer="OculiDoC",
                model="Deterministic Gaze Generator",
                serial_number=device_id,
                is_simulated=True,
                capabilities=(
                    "normalized_gaze",
                    "binocular_validity",
                    "pupil_diameter",
                    "polling",
                ),
            )
        )

        self.sample_rate_hz = float(sample_rate_hz)
        self.path_period_samples = path_period_samples
        self.invalid_every_n = invalid_every_n
        self.max_samples = max_samples
        self.realtime = realtime

        self._sequence = 0
        self._stream_started_at: float | None = None
        self._interval_seconds = 1.0 / self.sample_rate_hz
        self._interval_ns = int(round(1_000_000_000 / self.sample_rate_hz))

    def _on_stream_started(self) -> None:
        self._sequence = 0
        self._stream_started_at = perf_counter()

    def _on_stream_stopped(self) -> None:
        self._stream_started_at = None

    def _wait_for_sample_time(self) -> None:
        if not self.realtime:
            return

        if self._stream_started_at is None:
            return

        target = self._stream_started_at + self._sequence * self._interval_seconds
        delay = target - perf_counter()

        if delay > 0:
            sleep(delay)

    def _sample_is_valid(
        self,
        sequence: int,
    ) -> bool:
        if self.invalid_every_n is None:
            return True

        return (sequence + 1) % self.invalid_every_n != 0

    def read_sample(self) -> EyeTrackerSample:
        """Generate the next deterministic gaze sample."""
        self._require_streaming()

        if self.max_samples is not None and self._sequence >= self.max_samples:
            raise DeviceStreamEndedError("The simulated eye-tracker stream has ended.")

        self._wait_for_sample_time()

        sequence = self._sequence
        valid = self._sample_is_valid(sequence)
        phase = 2.0 * pi * sequence / self.path_period_samples

        gaze_x = 0.5 + 0.35 * sin(phase) if valid else None
        gaze_y = 0.5 + 0.25 * cos(phase * 0.5) if valid else None

        timestamp = DeviceTimestamp(
            sequence=sequence,
            source_timestamp_ns=(sequence * self._interval_ns),
            source_clock_id="simulated-shared-clock",
            monotonic_timestamp_ns=monotonic_ns(),
            utc_timestamp=datetime.now(UTC),
        )
        sample = EyeTrackerSample(
            timestamp=timestamp,
            gaze_x_normalized=gaze_x,
            gaze_y_normalized=gaze_y,
            left_eye_valid=valid,
            right_eye_valid=valid,
            left_pupil_diameter_mm=(3.2 + 0.1 * sin(phase) if valid else None),
            right_pupil_diameter_mm=(3.1 + 0.1 * cos(phase) if valid else None),
        )

        self._sequence += 1

        return sample
