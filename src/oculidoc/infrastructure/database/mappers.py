"""Mappings between domain objects and ORM records."""

from datetime import UTC, datetime
from uuid import UUID

from oculidoc.domain import ClinicalDiagnosis, Patient, Sex
from oculidoc.infrastructure.database.models import PatientRecord


def _as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    SQLite can return naive datetimes even when a SQLAlchemy column is
    configured with timezone support. Stored naive values are interpreted
    as UTC because OculiDoC writes audit timestamps in UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def patient_to_record(patient: Patient) -> PatientRecord:
    """Convert a patient domain object into an ORM record."""
    return PatientRecord(
        patient_id=str(patient.patient_id),
        patient_code=patient.patient_code,
        family_name=patient.family_name,
        sex=patient.sex.value,
        date_of_birth=patient.date_of_birth,
        etiology=patient.etiology,
        clinical_diagnosis=patient.clinical_diagnosis.value,
        diagnosis_details=patient.diagnosis_details,
        enrollment_date=patient.enrollment_date,
        notes=patient.notes,
        is_active=patient.is_active,
        created_at=patient.created_at,
        updated_at=patient.updated_at,
    )


def record_to_patient(record: PatientRecord) -> Patient:
    """Convert an ORM patient record into a domain object."""
    return Patient(
        patient_id=UUID(record.patient_id),
        patient_code=record.patient_code,
        family_name=record.family_name,
        sex=Sex(record.sex),
        date_of_birth=record.date_of_birth,
        etiology=record.etiology,
        clinical_diagnosis=ClinicalDiagnosis(record.clinical_diagnosis),
        diagnosis_details=record.diagnosis_details,
        enrollment_date=record.enrollment_date,
        notes=record.notes,
        is_active=record.is_active,
        created_at=_as_utc(record.created_at),
        updated_at=_as_utc(record.updated_at),
    )
