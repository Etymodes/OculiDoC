"""Database runtime integration tests."""

from pathlib import Path

from sqlalchemy import inspect

from oculidoc.application import RegisterPatientRequest
from oculidoc.infrastructure.database import initialize_database


def test_initialize_database_creates_patient_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime" / "oculidoc.sqlite3"

    runtime = initialize_database(database_path)

    assert database_path.exists()
    assert "patients" in inspect(runtime.engine).get_table_names()

    runtime.dispose()


def test_database_runtime_exposes_patient_service(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "oculidoc.sqlite3"
    runtime = initialize_database(database_path)

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-RUNTIME-001",
            family_name="???",
        )
    )

    loaded_patient = runtime.patient_service.get_patient(patient.patient_id)

    assert loaded_patient.patient_id == patient.patient_id
    assert loaded_patient.patient_code == "DOC-RUNTIME-001"

    runtime.dispose()


def test_database_runtime_persists_between_restarts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "oculidoc.sqlite3"

    first_runtime = initialize_database(database_path)
    patient = first_runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-RUNTIME-002",
            family_name="???",
        )
    )
    patient_id = patient.patient_id
    first_runtime.dispose()

    second_runtime = initialize_database(database_path)
    loaded_patient = second_runtime.patient_service.get_patient(patient_id)

    assert loaded_patient.patient_code == "DOC-RUNTIME-002"
    assert loaded_patient.family_name == "???"

    second_runtime.dispose()


def test_initialize_database_is_idempotent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "oculidoc.sqlite3"

    first_runtime = initialize_database(database_path)
    first_runtime.dispose()

    second_runtime = initialize_database(database_path)

    assert "patients" in inspect(second_runtime.engine).get_table_names()

    second_runtime.dispose()
