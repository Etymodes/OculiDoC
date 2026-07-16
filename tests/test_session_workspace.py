"""Experiment session filesystem workspace tests."""

import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from oculidoc.application import (
    CreateExperimentSessionRequest,
    RegisterPatientRequest,
)
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
)
from oculidoc.infrastructure.database import initialize_database


def resolve_session_directory(
    data_root: Path,
    data_directory: str,
) -> Path:
    return data_root.joinpath(*PurePosixPath(data_directory).parts)


def test_create_session_initializes_workspace_and_metadata(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=data_root,
    )

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-WORKSPACE-001",
            family_name="Workspace",
        )
    )
    session = runtime.experiment_session_service.create_session(
        CreateExperimentSessionRequest(
            patient_id=patient.patient_id,
            module_id="gaze_assessment",
        )
    )

    session_directory = resolve_session_directory(
        data_root,
        session.data_directory,
    )
    metadata_path = session_directory / "session.json"

    assert session_directory.is_dir()
    assert metadata_path.is_file()

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert payload["session_id"] == str(session.session_id)
    assert payload["patient_id"] == str(patient.patient_id)
    assert payload["status"] == "created"
    assert payload["clock"]["origin_utc"] is None

    expected_paths = {item["relative_path"] for item in payload["expected_artifacts"]}

    assert expected_paths == {
        "gaze.parquet",
        "camera.mp4",
        "camera_frames.parquet",
        "events.parquet",
        "session.json",
        "sync_report.json",
    }

    artifacts = runtime.experiment_session_service.list_artifacts(session.session_id)

    assert [artifact.relative_path for artifact in artifacts] == ["session.json"]

    assert not list(session_directory.glob(".session.json.*.tmp"))

    runtime.dispose()


def test_session_lifecycle_updates_metadata(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=data_root,
    )

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-WORKSPACE-002",
            family_name="Clock",
        )
    )
    session = runtime.experiment_session_service.create_session(
        CreateExperimentSessionRequest(
            patient_id=patient.patient_id,
            module_id="gaze_assessment",
        )
    )

    origin = datetime(
        2026,
        7,
        13,
        15,
        30,
        tzinfo=UTC,
    )

    runtime.experiment_session_service.start_session(
        session.session_id,
        monotonic_timestamp_ns=987654321,
        utc_timestamp=origin,
    )

    metadata_path = (
        resolve_session_directory(
            data_root,
            session.data_directory,
        )
        / "session.json"
    )
    running_payload = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert running_payload["status"] == "running"
    assert running_payload["clock"]["origin_monotonic_ns"] == 987654321
    assert running_payload["clock"]["origin_utc"] == origin.isoformat()

    completed = runtime.experiment_session_service.complete_session(session.session_id)
    completed_payload = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert completed.status is ExperimentSessionStatus.COMPLETED
    assert completed_payload["status"] == "completed"
    assert completed_payload["ended_at"] is not None

    runtime.dispose()


def test_session_rejects_unsafe_data_directory(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-WORKSPACE-003",
            family_name="Unsafe",
        )
    )

    with pytest.raises(
        ValueError,
        match="safe relative path",
    ):
        runtime.experiment_session_service.create_session(
            CreateExperimentSessionRequest(
                patient_id=patient.patient_id,
                module_id="gaze_assessment",
                data_directory="../outside",
            )
        )

    runtime.dispose()
