"""SQLite patient repository tests."""

from uuid import UUID

from oculidoc.domain import ClinicalDiagnosis, Patient, Sex
from oculidoc.infrastructure.database import (
    Base,
    create_session_factory,
    create_sqlite_engine,
)
from oculidoc.infrastructure.database.repositories import (
    SQLitePatientRepository,
)


def create_repository() -> tuple[
    SQLitePatientRepository,
    object,
]:
    engine = create_sqlite_engine(":memory:")
    Base.metadata.create_all(engine)

    repository = SQLitePatientRepository(create_session_factory(engine))

    return repository, engine


def test_repository_adds_and_returns_patient() -> None:
    repository, engine = create_repository()

    patient = Patient(
        patient_code="DOC-REP-001",
        family_name="??",
        sex=Sex.FEMALE,
        clinical_diagnosis=ClinicalDiagnosis.MCS_PLUS,
        diagnosis_details="??????",
    )

    stored_patient = repository.add(patient)

    assert stored_patient.patient_id == patient.patient_id
    assert stored_patient.patient_code == "DOC-REP-001"
    assert stored_patient.family_name == "??"
    assert stored_patient.sex is Sex.FEMALE
    assert stored_patient.clinical_diagnosis is ClinicalDiagnosis.MCS_PLUS
    assert stored_patient.diagnosis_details == "??????"

    engine.dispose()


def test_repository_gets_patient_by_uuid() -> None:
    repository, engine = create_repository()

    patient = Patient(
        patient_code="DOC-REP-002",
        family_name="??",
        clinical_diagnosis=ClinicalDiagnosis.UWS,
    )

    repository.add(patient)

    loaded_patient = repository.get(patient.patient_id)

    assert loaded_patient is not None
    assert loaded_patient.patient_id == patient.patient_id
    assert loaded_patient.patient_code == "DOC-REP-002"
    assert loaded_patient.family_name == "??"
    assert loaded_patient.clinical_diagnosis is ClinicalDiagnosis.UWS

    engine.dispose()


def test_repository_returns_none_for_unknown_uuid() -> None:
    repository, engine = create_repository()

    missing_patient = repository.get(UUID("11111111-1111-1111-1111-111111111111"))

    assert missing_patient is None

    engine.dispose()
