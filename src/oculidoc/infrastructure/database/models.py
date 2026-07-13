"""SQLAlchemy ORM models."""

from datetime import UTC, date, datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from oculidoc.domain import ClinicalDiagnosis, Sex
from oculidoc.infrastructure.database.base import Base


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


def _new_uuid_string() -> str:
    """Return a new UUID encoded as a database-safe string."""
    return str(uuid4())


class PatientRecord(Base):
    """Persistent patient record."""

    __tablename__ = "patients"

    patient_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid_string,
    )
    patient_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    family_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    sex: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=Sex.UNKNOWN.value,
    )
    date_of_birth: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    etiology: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    clinical_diagnosis: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ClinicalDiagnosis.UNKNOWN.value,
    )
    diagnosis_details: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    enrollment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
    )
    notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )


class PatientAuditRecord(Base):
    """Persistence model for immutable patient audit events."""

    __tablename__ = "patient_audit_events"

    event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    patient_id: Mapped[str] = mapped_column(
        ForeignKey("patients.patient_id"),
        index=True,
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    changed_fields: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    actor: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="local_admin",
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class ExperimentSessionRecord(Base):
    """Persistence model for one experiment session."""

    __tablename__ = "experiment_sessions"

    session_id: Mapped[str] = mapped_column(
        sa.String(36),
        primary_key=True,
    )
    patient_id: Mapped[str] = mapped_column(
        sa.ForeignKey("patients.patient_id"),
        index=True,
        nullable=False,
    )
    module_id: Mapped[str] = mapped_column(
        sa.String(128),
        index=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        sa.String(32),
        index=True,
        nullable=False,
    )
    data_directory: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
    )
    clock_origin_monotonic_ns: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        nullable=True,
    )
    clock_origin_utc: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(
        sa.Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )


class SessionArtifactRecord(Base):
    """Persistence model for one session data file."""

    __tablename__ = "session_artifacts"
    __table_args__ = (
        sa.UniqueConstraint(
            "session_id",
            "relative_path",
            name="uq_session_artifact_path",
        ),
    )

    artifact_id: Mapped[str] = mapped_column(
        sa.String(36),
        primary_key=True,
    )
    session_id: Mapped[str] = mapped_column(
        sa.ForeignKey("experiment_sessions.session_id"),
        index=True,
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        sa.String(32),
        index=True,
        nullable=False,
    )
    relative_path: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )
    mime_type: Mapped[str | None] = mapped_column(
        sa.String(128),
        nullable=True,
    )
    size_bytes: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        nullable=True,
    )
    sha256: Mapped[str | None] = mapped_column(
        sa.String(64),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
