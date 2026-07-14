"""Common contracts for cameras and eye trackers."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray


class DeviceKind(StrEnum):
    """Supported acquisition device categories."""

    CAMERA = "camera"
    EYE_TRACKER = "eye_tracker"


class DeviceState(StrEnum):
    """Lifecycle states shared by acquisition devices."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Stable identity and descriptive device metadata."""

    device_id: str
    kind: DeviceKind
    name: str
    manufacturer: str
    model: str
    serial_number: str | None = None
    is_simulated: bool = False
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "device_id",
            "name",
            "manufacturer",
            "model",
        ):
            normalized = getattr(self, field_name).strip()

            if not normalized:
                raise ValueError(f"{field_name} cannot be empty.")

            object.__setattr__(
                self,
                field_name,
                normalized,
            )

        if self.serial_number is not None:
            object.__setattr__(
                self,
                "serial_number",
                self.serial_number.strip() or None,
            )

        object.__setattr__(
            self,
            "capabilities",
            tuple(capability.strip() for capability in self.capabilities if capability.strip()),
        )


@dataclass(frozen=True, slots=True)
class DeviceTimestamp:
    """Timestamp attached as close as possible to acquisition."""

    sequence: int
    monotonic_timestamp_ns: int
    utc_timestamp: datetime
    source_timestamp_ns: int | None = None

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence cannot be negative.")

        if self.monotonic_timestamp_ns < 0:
            raise ValueError("monotonic_timestamp_ns cannot be negative.")

        if self.source_timestamp_ns is not None and self.source_timestamp_ns < 0:
            raise ValueError("source_timestamp_ns cannot be negative.")

        if self.utc_timestamp.tzinfo is None:
            raise ValueError("utc_timestamp must be timezone-aware.")

        object.__setattr__(
            self,
            "utc_timestamp",
            self.utc_timestamp.astimezone(UTC),
        )


@dataclass(frozen=True, slots=True)
class CameraFramePacket:
    """One image captured from a camera device."""

    timestamp: DeviceTimestamp
    frame_index: int
    image: NDArray[np.uint8] = field(
        repr=False,
        compare=False,
    )
    pixel_format: str = "BGR8"

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise ValueError("frame_index cannot be negative.")

        if not isinstance(self.image, np.ndarray):
            raise TypeError("image must be a NumPy array.")

        if self.image.dtype != np.uint8:
            raise ValueError("Camera images must use uint8 pixels.")

        if self.image.ndim not in {2, 3}:
            raise ValueError("Camera images must have two or three dimensions.")

        if self.image.size == 0:
            raise ValueError("Camera image cannot be empty.")

        normalized_format = self.pixel_format.strip()

        if not normalized_format:
            raise ValueError("pixel_format cannot be empty.")

        object.__setattr__(
            self,
            "pixel_format",
            normalized_format,
        )

    @property
    def width_px(self) -> int:
        """Return image width."""
        return int(self.image.shape[1])

    @property
    def height_px(self) -> int:
        """Return image height."""
        return int(self.image.shape[0])

    @property
    def channel_count(self) -> int:
        """Return one for grayscale or the final image dimension."""
        if self.image.ndim == 2:
            return 1

        return int(self.image.shape[2])


def _validate_optional_number(
    value: float | None,
    *,
    field_name: str,
    minimum: float | None = None,
) -> None:
    if value is None:
        return

    if not isfinite(value):
        raise ValueError(f"{field_name} must be finite.")

    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}.")


@dataclass(frozen=True, slots=True)
class EyeTrackerSample:
    """One gaze sample produced by an eye tracker."""

    timestamp: DeviceTimestamp
    gaze_x_normalized: float | None
    gaze_y_normalized: float | None
    left_eye_valid: bool
    right_eye_valid: bool
    left_pupil_diameter_mm: float | None = None
    right_pupil_diameter_mm: float | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "gaze_x_normalized",
            "gaze_y_normalized",
        ):
            _validate_optional_number(
                getattr(self, field_name),
                field_name=field_name,
            )

        for field_name in (
            "left_pupil_diameter_mm",
            "right_pupil_diameter_mm",
        ):
            _validate_optional_number(
                getattr(self, field_name),
                field_name=field_name,
                minimum=0.0,
            )

    @property
    def gaze_valid(self) -> bool:
        """Return whether a usable combined gaze position exists."""
        return (
            self.gaze_x_normalized is not None
            and self.gaze_y_normalized is not None
            and (self.left_eye_valid or self.right_eye_valid)
        )


@runtime_checkable
class AcquisitionDevice(Protocol):
    """Shared lifecycle contract for acquisition hardware."""

    @property
    def info(self) -> DeviceInfo: ...

    @property
    def state(self) -> DeviceState: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def start_stream(self) -> None: ...

    def stop_stream(self) -> None: ...


@runtime_checkable
class CameraDevice(AcquisitionDevice, Protocol):
    """Camera device that produces image packets."""

    def read_frame(self) -> CameraFramePacket: ...


@runtime_checkable
class EyeTrackerDevice(AcquisitionDevice, Protocol):
    """Eye tracker that produces gaze samples."""

    def read_sample(self) -> EyeTrackerSample: ...
