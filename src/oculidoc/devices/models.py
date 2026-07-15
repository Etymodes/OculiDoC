"""Device diagnostic result models."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ProbeStatus(StrEnum):
    """Result state for one hardware probe."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CameraProbeResult:
    """Result of probing one operating-system camera index."""

    index: int
    status: ProbeStatus
    backend: str | None = None
    width_px: int | None = None
    height_px: int | None = None
    fps: float | None = None
    frame_received: bool = False
    message: str | None = None

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("Camera index cannot be negative.")

        if self.width_px is not None and self.width_px <= 0:
            raise ValueError("Camera width must be positive.")

        if self.height_px is not None and self.height_px <= 0:
            raise ValueError("Camera height must be positive.")

        if self.fps is not None and self.fps <= 0:
            raise ValueError("Camera FPS must be positive.")

    @property
    def available(self) -> bool:
        """Return whether the camera opened and produced a frame."""
        return self.status is ProbeStatus.AVAILABLE and self.frame_received

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation."""
        return {
            "index": self.index,
            "status": self.status.value,
            "backend": self.backend,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "fps": self.fps,
            "frame_received": self.frame_received,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """System information relevant to acquisition readiness."""

    hostname: str
    operating_system: str
    operating_system_version: str
    machine: str
    processor: str
    python_version: str
    logical_cpu_count: int
    physical_cpu_count: int | None
    memory_total_bytes: int
    memory_available_bytes: int
    disk_free_bytes: int
    nvidia_gpu_names: tuple[str, ...] = ()
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware.")

        if self.logical_cpu_count <= 0:
            raise ValueError("logical_cpu_count must be positive.")

        for field_name in (
            "memory_total_bytes",
            "memory_available_bytes",
            "disk_free_bytes",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} cannot be negative.")

        object.__setattr__(
            self,
            "captured_at",
            self.captured_at.astimezone(UTC),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation."""
        return {
            "hostname": self.hostname,
            "operating_system": self.operating_system,
            "operating_system_version": (self.operating_system_version),
            "machine": self.machine,
            "processor": self.processor,
            "python_version": self.python_version,
            "logical_cpu_count": self.logical_cpu_count,
            "physical_cpu_count": self.physical_cpu_count,
            "memory_total_bytes": self.memory_total_bytes,
            "memory_available_bytes": (self.memory_available_bytes),
            "disk_free_bytes": self.disk_free_bytes,
            "nvidia_gpu_names": list(self.nvidia_gpu_names),
            "captured_at": self.captured_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class DeviceDiagnosticReport:
    """Complete hardware diagnostic report."""

    system: SystemSnapshot
    cameras: tuple[CameraProbeResult, ...] = ()
    schema_version: str = "1.0"
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.schema_version.strip():
            raise ValueError("schema_version cannot be empty.")

        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware.")

        object.__setattr__(
            self,
            "schema_version",
            self.schema_version.strip(),
        )
        object.__setattr__(
            self,
            "generated_at",
            self.generated_at.astimezone(UTC),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "system": self.system.to_dict(),
            "cameras": [camera.to_dict() for camera in self.cameras],
        }
