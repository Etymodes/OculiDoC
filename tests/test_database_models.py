"""Patient ORM model tests."""

from datetime import date

from sqlalchemy import inspect, select

from oculidoc.domain import ClinicalDiagnosis, Sex
from oculidoc.infrastructure.database import (
    Base,
    create_session_factory,
    create_sqlite_engine,
)
from oculidoc.infrastructure.database.models import PatientRecord


def test_patient_table_is_created() -> None:
    engine = create_sqlite_engine(":memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)

    assert "patients" in inspector.get_table_names()

    columns = {column["name"]: column for column in inspector.get_columns("patients")}

    assert set(columns) == {
        "patient_id",
        "patient_code",
        "family_name",
        "sex",
        "date_of_birth",
        "etiology",
        "clinical_diagnosis",
        "diagnosis_details",
        "enrollment_date",
        "notes",
        "is_active",
        "created_at",
        "updated_at",
    }

    assert columns["patient_id"]["nullable"] is False
    assert columns["patient_code"]["nullable"] is False
    assert columns["family_name"]["nullable"] is False

    engine.dispose()


def test_patient_table_uses_patient_id_as_primary_key() -> None:
    engine = create_sqlite_engine(":memory:")
    Base.metadata.create_all(engine)

    primary_key = inspect(engine).get_pk_constraint("patients")

    assert primary_key["constrained_columns"] == ["patient_id"]

    engine.dispose()


def test_patient_record_can_be_saved_and_loaded() -> None:
    engine = create_sqlite_engine(":memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    record = PatientRecord(
        patient_code="DOC-ORM-001",
        family_name="??",
        sex=Sex.UNKNOWN.value,
        clinical_diagnosis=ClinicalDiagnosis.MCS_PLUS.value,
        enrollment_date=date(2026, 7, 13),
    )

    with session_factory() as session:
        session.add(record)
        session.commit()

        stored_record = session.scalar(
            select(PatientRecord).where(PatientRecord.patient_code == "DOC-ORM-001")
        )

        assert stored_record is not None
        assert stored_record.patient_id
        assert stored_record.family_name == "??"
        assert stored_record.clinical_diagnosis == ClinicalDiagnosis.MCS_PLUS.value
        assert stored_record.is_active is True

    engine.dispose()
