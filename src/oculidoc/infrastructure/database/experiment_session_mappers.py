"""Experiment session database mappings."""

from datetime import UTC, datetime
from uuid import UUID

from oculidoc.domain.experiment_session import (
    ExperimentSession,
    ExperimentSessionStatus,
    SessionArtifact,
    SessionArtifactKind,
)
from oculidoc.infrastructure.database.models import (
    ExperimentSessionRecord,
    SessionArtifactRecord,
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def session_to_record(
    session: ExperimentSession,
) -> ExperimentSessionRecord:
    return ExperimentSessionRecord(
        session_id=str(session.session_id),
        patient_id=str(session.patient_id),
        module_id=session.module_id,
        status=session.status.value,
        data_directory=session.data_directory,
        schema_version=session.schema_version,
        clock_origin_monotonic_ns=(session.clock_origin_monotonic_ns),
        clock_origin_utc=session.clock_origin_utc,
        started_at=session.started_at,
        ended_at=session.ended_at,
        failure_reason=session.failure_reason,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def record_to_session(
    record: ExperimentSessionRecord,
) -> ExperimentSession:
    return ExperimentSession(
        session_id=UUID(record.session_id),
        patient_id=UUID(record.patient_id),
        module_id=record.module_id,
        status=ExperimentSessionStatus(record.status),
        data_directory=record.data_directory,
        schema_version=record.schema_version,
        clock_origin_monotonic_ns=(record.clock_origin_monotonic_ns),
        clock_origin_utc=_as_utc(record.clock_origin_utc),
        started_at=_as_utc(record.started_at),
        ended_at=_as_utc(record.ended_at),
        failure_reason=record.failure_reason,
        created_at=_as_utc(record.created_at),
        updated_at=_as_utc(record.updated_at),
    )


def artifact_to_record(
    artifact: SessionArtifact,
) -> SessionArtifactRecord:
    return SessionArtifactRecord(
        artifact_id=str(artifact.artifact_id),
        session_id=str(artifact.session_id),
        kind=artifact.kind.value,
        relative_path=artifact.relative_path,
        source=artifact.source,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        created_at=artifact.created_at,
    )


def record_to_artifact(
    record: SessionArtifactRecord,
) -> SessionArtifact:
    return SessionArtifact(
        artifact_id=UUID(record.artifact_id),
        session_id=UUID(record.session_id),
        kind=SessionArtifactKind(record.kind),
        relative_path=record.relative_path,
        source=record.source,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        created_at=_as_utc(record.created_at),
    )
