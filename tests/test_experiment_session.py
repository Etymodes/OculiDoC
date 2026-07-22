"""Experiment session lifecycle and persistence tests."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect

from oculidoc.application import (
    CreateExperimentSessionRequest,
    DuplicateSessionArtifactError,
    ExperimentSessionNotFoundError,
    InactivePatientError,
    RegisterPatientRequest,
    RegisterSessionArtifactRequest,
)
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
    SessionArtifactKind,
)
from oculidoc.infrastructure.database import initialize_database
from oculidoc.infrastructure.database.models import SessionArtifactRecord


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


def test_admin_can_correct_stale_running_session_to_terminal_status(
    tmp_path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    _, session = create_patient_and_session(runtime)
    running = runtime.experiment_session_service.start_session(session.session_id)

    corrected = runtime.experiment_session_service.correct_session_status(
        running.session_id,
        ExperimentSessionStatus.ABORTED,
        "程序异常退出后由管理员手动标记为已取消。",
    )

    assert corrected.status is ExperimentSessionStatus.ABORTED
    assert corrected.failure_reason == "程序异常退出后由管理员手动标记为已取消。"
    assert corrected.ended_at is None
    metadata_path = (
        runtime.experiment_session_service.resolve_session_directory(session.session_id)
        / "session.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "aborted"
    assert metadata["ended_at"] is None

    completed = runtime.experiment_session_service.correct_session_status(
        running.session_id,
        ExperimentSessionStatus.COMPLETED,
    )
    assert completed.failure_reason is None

    with pytest.raises(ValueError, match="terminal status"):
        runtime.experiment_session_service.correct_session_status(
            running.session_id,
            ExperimentSessionStatus.RUNNING,
        )

    runtime.dispose()


def test_delete_session_removes_database_record_and_archives_files(
    tmp_path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    _, session = create_patient_and_session(runtime)
    session_directory = runtime.experiment_session_service.resolve_session_directory(
        session.session_id
    )
    payload_path = session_directory / "payload.bin"
    payload_path.write_bytes(b"keep-recoverable")
    artifact = runtime.experiment_session_service.register_artifact(
        RegisterSessionArtifactRequest(
            session_id=session.session_id,
            kind=SessionArtifactKind.OTHER,
            relative_path="payload.bin",
            source="test",
        )
    )

    archived_directory = runtime.experiment_session_service.delete_session(
        session.session_id
    )

    assert archived_directory is not None
    assert not session_directory.exists()
    assert archived_directory.is_relative_to(tmp_path / "data" / "deleted_sessions")
    assert (archived_directory / "payload.bin").read_bytes() == b"keep-recoverable"

    with pytest.raises(ExperimentSessionNotFoundError):
        runtime.experiment_session_service.get_session(session.session_id)

    with runtime.session_factory() as database:
        assert database.get(SessionArtifactRecord, str(artifact.artifact_id)) is None

    runtime.dispose()


def test_delete_session_restores_files_if_database_delete_fails(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    _, session = create_patient_and_session(runtime)
    session_directory = runtime.experiment_session_service.resolve_session_directory(
        session.session_id
    )
    (session_directory / "payload.bin").write_bytes(b"restore-on-failure")

    def fail_delete(_session_id) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        runtime.experiment_session_repository,
        "delete",
        fail_delete,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        runtime.experiment_session_service.delete_session(session.session_id)

    assert (session_directory / "payload.bin").read_bytes() == b"restore-on-failure"
    assert runtime.experiment_session_service.get_session(session.session_id) is not None
    runtime.dispose()


def test_delete_session_refuses_non_exclusive_data_directory(
    tmp_path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-SESSION-BROAD-PATH",
            family_name="Broad",
        )
    )
    session = runtime.experiment_session_service.create_session(
        CreateExperimentSessionRequest(
            patient_id=patient.patient_id,
            module_id="gaze_assessment",
            data_directory="sessions",
        )
    )

    with pytest.raises(ValueError, match="non-exclusive"):
        runtime.experiment_session_service.delete_session(session.session_id)

    assert runtime.experiment_session_service.get_session(session.session_id) is not None
    assert (tmp_path / "data" / "sessions" / "session.json").exists()
    runtime.dispose()
