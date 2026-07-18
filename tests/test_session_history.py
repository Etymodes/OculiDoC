"""Tests for patient experiment-session history."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from oculidoc.application import (
    RegisterPatientRequest,
)
from oculidoc.application.gaze_task_session import (
    create_gaze_task_launch,
    finalize_gaze_task_launch,
)
from oculidoc.application.session_history import (
    build_patient_session_history,
    export_session_zip,
)
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
)
from oculidoc.infrastructure.database import (
    initialize_database,
)


def create_completed_session(
    runtime: object,
    *,
    module_id: str,
) -> tuple[object, object]:
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=(f"DOC-HISTORY-{module_id}"),
            family_name="History",
        )
    )
    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id=module_id,
    )
    run_directory = launch.session_directory / "tasks" / "run-history"
    run_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    (run_directory / "gaze_events.parquet").write_bytes(b"parquet")
    (run_directory / "task_events.jsonl").write_text(
        "",
        encoding="utf-8",
    )
    (run_directory / "run_manifest.json").write_text(
        '{"status":"finished"}\n',
        encoding="utf-8",
    )
    (run_directory / "task_result.json").write_text(
        json.dumps(
            {
                "summary": {
                    "sample_count": 100,
                    "valid_sample_ratio": 0.82,
                    "dwell_by_role_ms": {
                        "target": 4200.0,
                        "non_option": 800.0,
                    },
                },
                "result": {
                    "recording_failed": False,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    finalize_gaze_task_launch(
        runtime.experiment_session_service,
        launch,
        exit_code=0,
    )

    return patient, launch


def test_history_summarizes_completed_task(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient, launch = create_completed_session(
        runtime,
        module_id="tracking_ball",
    )

    entries = build_patient_session_history(
        runtime.experiment_session_service,
        patient.patient_id,
    )

    assert len(entries) == 1
    entry = entries[0]

    assert entry.session_id == launch.session_id
    assert entry.status is ExperimentSessionStatus.COMPLETED
    assert entry.sample_count == 100
    assert entry.valid_sample_ratio == 0.82
    assert entry.dwell_by_role_ms["target"] == 4200.0
    assert entry.artifact_count >= 5
    assert entry.has_task_result is True
    assert entry.duration_seconds is not None

    runtime.dispose()


def test_history_includes_aborted_session(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=("DOC-HISTORY-ABORT"),
            family_name="Abort",
        )
    )
    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="binary_horizontal",
    )
    finalize_gaze_task_launch(
        runtime.experiment_session_service,
        launch,
        exit_code=0,
    )

    entries = build_patient_session_history(
        runtime.experiment_session_service,
        patient.patient_id,
    )

    assert len(entries) == 1
    assert entries[0].status is ExperimentSessionStatus.ABORTED
    assert entries[0].sample_count is None
    assert entries[0].failure_reason == ("Task setup was cancelled before recording started.")

    runtime.dispose()


def test_export_session_zip_preserves_layout(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient, launch = create_completed_session(
        runtime,
        module_id="binary_horizontal",
    )
    destination = tmp_path / "exports" / "session"

    archive_path = export_session_zip(
        runtime.experiment_session_service,
        launch.session_id,
        destination,
    )

    assert archive_path.suffix == ".zip"
    assert archive_path.is_file()

    with zipfile.ZipFile(
        archive_path,
    ) as archive:
        names = set(archive.namelist())

    assert "session.json" in names
    assert any(name.endswith("/task_result.json") for name in names)
    assert any(name.endswith("/gaze_events.parquet") for name in names)

    entries = build_patient_session_history(
        runtime.experiment_session_service,
        patient.patient_id,
    )
    assert len(entries) == 1

    runtime.dispose()
