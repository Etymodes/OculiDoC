"""Experiment session lifecycle and persistence tests."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect

from oculidoc.application import (
    CreateExperimentSessionRequest,
    DuplicateSessionArtifactError,
    InactivePatientError,
    RegisterPatientRequest,
    RegisterSessionArtifactRequest,
)
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
    SessionArtifactKind,
)
from oculidoc.infrastructure.database import initialize_database


def create_patient_and_session(runtime):
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-SESSION-001",
            family_name="Session",
        )
    )
    session = runtime.experiment_session_service.create_session(
        CreateExperimentSessionRequest(
            patient_id=patient.patient_id,
            module_id="gaze_assessment",
        )
    )

    return patient, session


def test_runtime_creates_session_tables() -> None:
    runtime = initialize_database(":memory:")

    table_names = inspect(runtime.engine).get_table_names()

    assert "experiment_sessions" in table_names
    assert "session_artifacts" in table_names

    runtime.dispose()


def test_session_uses_safe_default_data_directory() -> None:
    runtime = initialize_database(":memory:")
    patient, session = create_patient_and_session(runtime)

    assert session.patient_id == patient.patient_id
    assert session.status is ExperimentSessionStatus.CREATED
    assert session.data_directory == (f"sessions/{patient.patient_id}/{session.session_id}")

    runtime.dispose()


def test_inactive_patient_cannot_create_session() -> None:
    runtime = initialize_database(":memory:")

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-INACTIVE-SESSION",
            family_name="Inactive",
        )
    )
    runtime.patient_service.deactivate_patient(patient.patient_id)

    with pytest.raises(InactivePatientError):
        runtime.experiment_session_service.create_session(
            CreateExperimentSessionRequest(
                patient_id=patient.patient_id,
                module_id="gaze_assessment",
            )
        )

    runtime.dispose()


def test_session_lifecycle_records_clock_origin() -> None:
    runtime = initialize_database(":memory:")
    _, session = create_patient_and_session(runtime)

    utc_origin = datetime(
        2026,
        7,
        13,
        12,
        30,
        tzinfo=UTC,
    )

    running = runtime.experiment_session_service.start_session(
        session.session_id,
        monotonic_timestamp_ns=123456789,
        utc_timestamp=utc_origin,
    )

    assert running.status is ExperimentSessionStatus.RUNNING
    assert running.clock_origin_monotonic_ns == 123456789
    assert running.clock_origin_utc == utc_origin

    completed = runtime.experiment_session_service.complete_session(session.session_id)

    assert completed.status is ExperimentSessionStatus.COMPLETED
    assert completed.ended_at is not None

    runtime.dispose()


def test_session_manifest_registers_artifacts() -> None:
    runtime = initialize_database(":memory:")
    _, session = create_patient_and_session(runtime)

    service = runtime.experiment_session_service

    service.register_artifact(
        RegisterSessionArtifactRequest(
            session_id=session.session_id,
            kind=SessionArtifactKind.GAZE,
            relative_path="gaze.parquet",
            source="tobii",
            mime_type="application/vnd.apache.parquet",
            size_bytes=2048,
        )
    )
    service.register_artifact(
        RegisterSessionArtifactRequest(
            session_id=session.session_id,
            kind=SessionArtifactKind.CAMERA_VIDEO,
            relative_path="camera.mp4",
            source="camera",
            mime_type="video/mp4",
        )
    )

    artifacts = service.list_artifacts(session.session_id)

    assert [artifact.relative_path for artifact in artifacts] == [
        "gaze.parquet",
        "camera.mp4",
    ]

    with pytest.raises(DuplicateSessionArtifactError):
        service.register_artifact(
            RegisterSessionArtifactRequest(
                session_id=session.session_id,
                kind=SessionArtifactKind.GAZE,
                relative_path="gaze.parquet",
                source="tobii",
            )
        )

    runtime.dispose()


def test_sessions_persist_between_restarts(
    tmp_path,
) -> None:
    database_path = tmp_path / "oculidoc.sqlite3"

    first_runtime = initialize_database(database_path)
    patient, session = create_patient_and_session(first_runtime)
    first_runtime.dispose()

    second_runtime = initialize_database(database_path)
    sessions = second_runtime.experiment_session_service.list_sessions_for_patient(
        patient.patient_id
    )

    assert len(sessions) == 1
    assert sessions[0].session_id == session.session_id
    assert sessions[0].module_id == "gaze_assessment"

    second_runtime.dispose()
