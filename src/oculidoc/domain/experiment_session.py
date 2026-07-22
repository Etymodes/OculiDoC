"""Experiment session and artifact domain models."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID, uuid4


class ExperimentSessionStatus(StrEnum):
    """Lifecycle states for one experiment session."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class SessionArtifactKind(StrEnum):
    """Known data products produced by one session."""

    GAZE = "gaze"
    CAMERA_VIDEO = "camera_video"
    CAMERA_FRAMES = "camera_frames"
    EVENTS = "events"
    SESSION_METADATA = "session_metadata"
    SYNC_REPORT = "sync_report"
    OTHER = "other"


STANDARD_SESSION_ARTIFACTS = (
    (
        SessionArtifactKind.GAZE,
        "gaze.parquet",
        "tobii",
    ),
    (
        SessionArtifactKind.CAMERA_VIDEO,
        "camera.mp4",
        "camera",
    ),
    (
        SessionArtifactKind.CAMERA_FRAMES,
        "camera_frames.parquet",
        "camera",
    ),
    (
        SessionArtifactKind.EVENTS,
        "events.parquet",
        "task",
    ),
    (
        SessionArtifactKind.SESSION_METADATA,
        "session.json",
        "system",
    ),
    (
        SessionArtifactKind.SYNC_REPORT,
        "sync_report.json",
        "system",
    ),
)


def _normalize_relative_path(
    value: str,
    *,
    field_name: str,
) -> str:
    """Normalize and validate a safe relative path."""
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)

    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise ValueError(f"{field_name} must be a safe relative path.")

    return path.as_posix()


def _require_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime."""
    if value.tzinfo is None:
        raise ValueError("Session timestamps must be timezone-aware.")

    return value.astimezone(UTC)


@dataclass(slots=True)
class ExperimentSession:
    """One acquisition, assessment, or training session."""

    patient_id: UUID
    module_id: str
    data_directory: str
    session_id: UUID = field(default_factory=uuid4)
    status: ExperimentSessionStatus = ExperimentSessionStatus.CREATED
    schema_version: str = "1.0"
    clock_origin_monotonic_ns: int | None = None
    clock_origin_utc: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.module_id = self.module_id.strip()
        self.data_directory = _normalize_relative_path(
            self.data_directory,
            field_name="data_directory",
        )
        self.schema_version = self.schema_version.strip()

        if not self.module_id:
            raise ValueError("module_id cannot be empty.")

        if not self.data_directory:
            raise ValueError("data_directory cannot be empty.")

        if not self.schema_version:
            raise ValueError("schema_version cannot be empty.")

    @property
    def is_terminal(self) -> bool:
        """Return whether the session can no longer run."""
        return self.status in {
            ExperimentSessionStatus.COMPLETED,
            ExperimentSessionStatus.ABORTED,
            ExperimentSessionStatus.FAILED,
        }

    def start(
        self,
        *,
        monotonic_timestamp_ns: int,
        utc_timestamp: datetime,
    ) -> None:
        """Start acquisition and establish the unified clock origin."""
        if self.status is not ExperimentSessionStatus.CREATED:
            raise ValueError("Only a created session can be started.")

        if monotonic_timestamp_ns < 0:
            raise ValueError("monotonic_timestamp_ns cannot be negative.")

        normalized_utc = _require_utc(utc_timestamp)

        self.status = ExperimentSessionStatus.RUNNING
        self.clock_origin_monotonic_ns = monotonic_timestamp_ns
        self.clock_origin_utc = normalized_utc
        self.started_at = normalized_utc
        self.updated_at = normalized_utc

    def complete(
        self,
        *,
        ended_at: datetime | None = None,
    ) -> None:
        """Mark a running session as completed."""
        if self.status is not ExperimentSessionStatus.RUNNING:
            raise ValueError("Only a running session can be completed.")

        self._finish(
            ExperimentSessionStatus.COMPLETED,
            ended_at=ended_at,
        )

    def abort(
        self,
        reason: str | None = None,
        *,
        ended_at: datetime | None = None,
    ) -> None:
        """Abort a created or running session."""
        if self.status not in {
            ExperimentSessionStatus.CREATED,
            ExperimentSessionStatus.RUNNING,
        }:
            raise ValueError("A terminal session cannot be aborted.")

        self._finish(
            ExperimentSessionStatus.ABORTED,
            reason=reason,
            ended_at=ended_at,
        )

    def fail(
        self,
        reason: str,
        *,
        ended_at: datetime | None = None,
    ) -> None:
        """Mark a created or running session as failed."""
        normalized_reason = reason.strip()

        if not normalized_reason:
            raise ValueError("A failed session requires a reason.")

        if self.status not in {
            ExperimentSessionStatus.CREATED,
            ExperimentSessionStatus.RUNNING,
        }:
            raise ValueError("A terminal session cannot be failed.")

        self._finish(
            ExperimentSessionStatus.FAILED,
            reason=normalized_reason,
            ended_at=ended_at,
        )

    def correct_terminal_status(
        self,
        status: ExperimentSessionStatus,
        reason: str | None = None,
        *,
        corrected_at: datetime | None = None,
    ) -> None:
        """Apply an administrator correction without inventing acquisition time."""
        if status not in {
            ExperimentSessionStatus.COMPLETED,
            ExperimentSessionStatus.ABORTED,
            ExperimentSessionStatus.FAILED,
        }:
            raise ValueError("A manual correction must use a terminal status.")

        normalized_reason = reason.strip() if reason is not None else ""

        if status is ExperimentSessionStatus.FAILED and not normalized_reason:
            raise ValueError("A failed session requires a reason.")

        self.status = status
        self.failure_reason = (
            normalized_reason
            if status in {
                ExperimentSessionStatus.ABORTED,
                ExperimentSessionStatus.FAILED,
            }
            else None
        ) or None
        self.updated_at = _require_utc(corrected_at or datetime.now(UTC))

    def _finish(
        self,
        status: ExperimentSessionStatus,
        *,
        reason: str | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        timestamp = _require_utc(ended_at or datetime.now(UTC))

        self.status = status
        self.failure_reason = reason.strip() if reason is not None and reason.strip() else None
        self.ended_at = timestamp
        self.updated_at = timestamp


@dataclass(frozen=True, slots=True)
class SessionArtifact:
    """One file produced by an experiment session."""

    session_id: UUID
    kind: SessionArtifactKind
    relative_path: str
    source: str
    artifact_id: UUID = field(default_factory=uuid4)
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        normalized_path = self.relative_path.replace(
            "\\",
            "/",
        ).strip()
        path = PurePosixPath(normalized_path)
        normalized_source = self.source.strip()

        if (
            not normalized_path
            or path.is_absolute()
            or ".." in path.parts
            or (path.parts and ":" in path.parts[0])
        ):
            raise ValueError("Artifact paths must be safe relative paths.")

        if not normalized_source:
            raise ValueError("Artifact source cannot be empty.")

        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("Artifact size cannot be negative.")

        object.__setattr__(
            self,
            "relative_path",
            path.as_posix(),
        )
        object.__setattr__(
            self,
            "source",
            normalized_source,
        )

        if self.mime_type is not None:
            object.__setattr__(
                self,
                "mime_type",
                self.mime_type.strip() or None,
            )

        if self.sha256 is not None:
            object.__setattr__(
                self,
                "sha256",
                self.sha256.strip().lower() or None,
            )
