"""SQLite-backed repositories."""

from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from oculidoc.domain import Patient
from oculidoc.infrastructure.database.mappers import (
    patient_to_record,
    record_to_patient,
)
from oculidoc.infrastructure.database.models import (
    ExperimentSessionRecord,
    PatientAuditRecord,
    PatientRecord,
)


def _apply_patient_to_record(
    patient: Patient,
    record: PatientRecord,
) -> None:
    """Copy mutable patient fields into an existing ORM record."""
    record.patient_code = patient.patient_code
    record.family_name = patient.family_name
    record.sex = patient.sex.value
    record.date_of_birth = patient.date_of_birth
    record.etiology = patient.etiology
    record.clinical_diagnosis = patient.clinical_diagnosis.value
    record.diagnosis_details = patient.diagnosis_details
    record.enrollment_date = patient.enrollment_date
    record.notes = patient.notes
    record.is_active = patient.is_active
    record.created_at = patient.created_at
    record.updated_at = patient.updated_at


class SQLitePatientRepository:
    """Store and retrieve patients using SQLAlchemy and SQLite."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._session_factory = session_factory

    def add(self, patient: Patient) -> Patient:
        """Persist a new patient and return the stored domain object."""
        record = patient_to_record(patient)

        with self._session_factory() as session:
            session.add(record)
            session.commit()
            session.refresh(record)

            return record_to_patient(record)

    def get(self, patient_id: UUID) -> Patient | None:
        """Return one patient by UUID, or None when not found."""
        with self._session_factory() as session:
            record = session.get(
                PatientRecord,
                str(patient_id),
            )

            if record is None:
                return None

            return record_to_patient(record)

    def get_by_code(
        self,
        patient_code: str,
    ) -> Patient | None:
        """Return one patient by anonymous patient code."""
        normalized_code = patient_code.strip()

        with self._session_factory() as session:
            record = session.scalar(
                select(PatientRecord).where(PatientRecord.patient_code == normalized_code)
            )

            if record is None:
                return None

            return record_to_patient(record)

    def list_all(
        self,
        *,
        active_only: bool = False,
    ) -> list[Patient]:
        """Return patients ordered by anonymous patient code."""
        statement = select(PatientRecord).order_by(PatientRecord.patient_code)

        if active_only:
            statement = statement.where(PatientRecord.is_active.is_(True))

        with self._session_factory() as session:
            records = session.scalars(statement).all()

            return [record_to_patient(record) for record in records]

    def update(self, patient: Patient) -> Patient:
        """Update an existing patient.

        Raises:
            KeyError: If the patient UUID does not exist.
        """
        with self._session_factory() as session:
            record = session.get(
                PatientRecord,
                str(patient.patient_id),
            )

            if record is None:
                raise KeyError(f"Patient not found: {patient.patient_id}")

            _apply_patient_to_record(patient, record)

            session.commit()
            session.refresh(record)

            return record_to_patient(record)

    def merge(self, source_patient_id: UUID, target: Patient) -> Patient:
        """Atomically move source sessions/audit history into target."""
        if source_patient_id == target.patient_id:
            raise ValueError("Cannot merge a patient into itself.")

        with self._session_factory() as session:
            source = session.get(PatientRecord, str(source_patient_id))
            target_record = session.get(PatientRecord, str(target.patient_id))
            if source is None or target_record is None:
                raise KeyError("Patient not found.")

            duplicate_code = session.scalar(
                select(PatientRecord).where(
                    PatientRecord.patient_code == target.patient_code,
                    PatientRecord.patient_id != str(target.patient_id),
                    PatientRecord.patient_id != str(source_patient_id),
                )
            )
            if duplicate_code is not None:
                raise ValueError(f"Patient code already exists: {target.patient_code}")

            _apply_patient_to_record(target, target_record)
            session.execute(
                update(ExperimentSessionRecord)
                .where(ExperimentSessionRecord.patient_id == str(source_patient_id))
                .values(patient_id=str(target.patient_id))
            )
            session.execute(
                update(PatientAuditRecord)
                .where(PatientAuditRecord.patient_id == str(source_patient_id))
                .values(patient_id=str(target.patient_id))
            )
            session.execute(
                delete(PatientRecord).where(
                    PatientRecord.patient_id == str(source_patient_id)
                )
            )
            session.commit()
            session.refresh(target_record)
            return record_to_patient(target_record)
