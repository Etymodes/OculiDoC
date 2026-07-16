"""Extended SQLite patient repository tests."""

import pytest
from sqlalchemy import Engine

from oculidoc.domain import ClinicalDiagnosis, Patient
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
    Engine,
]:
    engine = create_sqlite_engine(":memory:")
    Base.metadata.create_all(engine)

    repository = SQLitePatientRepository(create_session_factory(engine))

    return repository, engine


def test_repository_gets_patient_by_code() -> None:
    repository, engine = create_repository()

    patient = Patient(
        patient_code="DOC-CODE-001",
        family_name="??",
    )
    repository.add(patient)

    loaded_patient = repository.get_by_code(" DOC-CODE-001 ")

    assert loaded_patient is not None
    assert loaded_patient.patient_id == patient.patient_id
    assert loaded_patient.patient_code == "DOC-CODE-001"

    engine.dispose()


def test_repository_returns_none_for_unknown_code() -> None:
    repository, engine = create_repository()

    assert repository.get_by_code("DOC-NOT-FOUND") is None

    engine.dispose()


def test_repository_lists_patients_in_code_order() -> None:
    repository, engine = create_repository()

    repository.add(
        Patient(
            patient_code="DOC-LIST-003",
            family_name="?",
        )
    )
    repository.add(
        Patient(
            patient_code="DOC-LIST-001",
            family_name="?",
        )
    )
    repository.add(
        Patient(
            patient_code="DOC-LIST-002",
            family_name="?",
        )
    )

    patients = repository.list_all()

    assert [patient.patient_code for patient in patients] == [
        "DOC-LIST-001",
        "DOC-LIST-002",
        "DOC-LIST-003",
    ]

    engine.dispose()


def test_repository_can_list_only_active_patients() -> None:
    repository, engine = create_repository()

    repository.add(
        Patient(
            patient_code="DOC-ACTIVE-001",
            family_name="??",
        )
    )
    repository.add(
        Patient(
            patient_code="DOC-ACTIVE-002",
            family_name="??",
            is_active=False,
        )
    )

    active_patients = repository.list_all(active_only=True)

    assert len(active_patients) == 1
    assert active_patients[0].patient_code == "DOC-ACTIVE-001"

    engine.dispose()


def test_repository_updates_existing_patient() -> None:
    repository, engine = create_repository()

    patient = Patient(
        patient_code="DOC-UPDATE-001",
        family_name="???",
        clinical_diagnosis=ClinicalDiagnosis.UNKNOWN,
    )
    repository.add(patient)

    patient.family_name = "???"
    patient.clinical_diagnosis = ClinicalDiagnosis.MCS_PLUS
    patient.diagnosis_details = "???????"
    patient.notes = "??????"
    patient.deactivate()

    updated_patient = repository.update(patient)
    loaded_patient = repository.get(patient.patient_id)

    assert updated_patient.family_name == "???"
    assert updated_patient.is_active is False

    assert loaded_patient is not None
    assert loaded_patient.family_name == "???"
    assert loaded_patient.clinical_diagnosis is ClinicalDiagnosis.MCS_PLUS
    assert loaded_patient.diagnosis_details == "???????"
    assert loaded_patient.notes == "??????"
    assert loaded_patient.is_active is False

    engine.dispose()


def test_repository_rejects_update_for_unknown_patient() -> None:
    repository, engine = create_repository()

    patient = Patient(
        patient_code="DOC-UPDATE-MISSING",
        family_name="???",
    )

    with pytest.raises(
        KeyError,
        match="Patient not found",
    ):
        repository.update(patient)

    engine.dispose()
