"""Timestamp-aware paired camera and gaze acquisition."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from oculidoc.devices.contracts import (
    CameraDevice,
    CameraFramePacket,
    DeviceState,
    EyeTrackerDevice,
    EyeTrackerSample,
)
from oculidoc.devices.coordinator import DeviceCoordinator
from oculidoc.devices.errors import DeviceCoordinationError


@dataclass(frozen=True, slots=True)
class PairedAcquisitionPacket:
    """One camera frame paired with one gaze sample."""

    pair_index: int
    camera_frame: CameraFramePacket
    gaze_sample: EyeTrackerSample
    paired_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.pair_index < 0:
            raise ValueError("pair_index cannot be negative.")

        if self.paired_at.tzinfo is None:
            raise ValueError("paired_at must be timezone-aware.")

        object.__setattr__(
            self,
            "paired_at",
            self.paired_at.astimezone(UTC),
        )

    @property
    def host_skew_ns(self) -> int:
        """Return absolute host monotonic timestamp difference."""
        return abs(
            self.camera_frame.timestamp.monotonic_timestamp_ns
            - self.gaze_sample.timestamp.monotonic_timestamp_ns
        )

    @property
    def source_skew_ns(self) -> int | None:
        """Return source skew only for a shared clock domain."""
        camera_timestamp = self.camera_frame.timestamp
        gaze_timestamp = self.gaze_sample.timestamp

        camera_source_ns = camera_timestamp.source_timestamp_ns
        gaze_source_ns = gaze_timestamp.source_timestamp_ns
        camera_clock_id = camera_timestamp.source_clock_id
        gaze_clock_id = gaze_timestamp.source_clock_id

        if (
            camera_source_ns is None
            or gaze_source_ns is None
            or camera_clock_id is None
            or gaze_clock_id is None
            or camera_clock_id != gaze_clock_id
        ):
            return None

        return abs(camera_source_ns - gaze_source_ns)

    def to_summary_dict(self) -> dict[str, object]:
        """Return metadata without serializing image pixels."""
        return {
            "pair_index": self.pair_index,
            "paired_at": self.paired_at.isoformat(),
            "host_skew_ns": self.host_skew_ns,
            "source_skew_ns": self.source_skew_ns,
            "camera": {
                "sequence": (self.camera_frame.timestamp.sequence),
                "frame_index": (self.camera_frame.frame_index),
                "width_px": self.camera_frame.width_px,
                "height_px": self.camera_frame.height_px,
                "pixel_format": (self.camera_frame.pixel_format),
            },
            "gaze": {
                "sequence": (self.gaze_sample.timestamp.sequence),
                "valid": self.gaze_sample.gaze_valid,
                "x_normalized": (self.gaze_sample.gaze_x_normalized),
                "y_normalized": (self.gaze_sample.gaze_y_normalized),
                "left_eye_valid": (self.gaze_sample.left_eye_valid),
                "right_eye_valid": (self.gaze_sample.right_eye_valid),
                "left_pupil_diameter_mm": (self.gaze_sample.left_pupil_diameter_mm),
                "right_pupil_diameter_mm": (self.gaze_sample.right_pupil_diameter_mm),
            },
        }


class PairedAcquisitionRunner:
    """Collect paired camera and eye-tracker samples."""

    def __init__(
        self,
        camera: CameraDevice,
        eye_tracker: EyeTrackerDevice,
    ) -> None:
        self.camera = camera
        self.eye_tracker = eye_tracker
        self.coordinator = DeviceCoordinator([camera, eye_tracker])

    def collect(
        self,
        pair_count: int,
    ) -> tuple[PairedAcquisitionPacket, ...]:
        """Collect a finite set of paired samples."""
        if pair_count < 0:
            raise ValueError("pair_count cannot be negative.")

        if pair_count == 0:
            return ()

        packets: list[PairedAcquisitionPacket] = []

        try:
            self.coordinator.connect_and_start()

            for pair_index in range(pair_count):
                camera_frame = self.camera.read_frame()
                gaze_sample = self.eye_tracker.read_sample()

                packets.append(
                    PairedAcquisitionPacket(
                        pair_index=pair_index,
                        camera_frame=camera_frame,
                        gaze_sample=gaze_sample,
                    )
                )
        except Exception as primary_error:
            cleanup_errors: list[Exception] = []

            try:
                self.coordinator.stop_and_disconnect()
            except Exception as cleanup_error:
                cleanup_errors.append(cleanup_error)

            if cleanup_errors:
                raise DeviceCoordinationError(
                    "paired_acquisition",
                    primary_error,
                    cleanup_errors,
                ) from primary_error

            raise
        else:
            self.coordinator.stop_and_disconnect()

        return tuple(packets)

    @property
    def devices_disconnected(self) -> bool:
        """Return whether both managed devices are disconnected."""
        return all(
            device.state is DeviceState.DISCONNECTED
            for device in (
                self.camera,
                self.eye_tracker,
            )
        )
