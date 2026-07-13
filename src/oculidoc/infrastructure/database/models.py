"""SQLAlchemy ORM models."""

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, String, Text
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
