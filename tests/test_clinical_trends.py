from __future__ import annotations

import json
import time
from pathlib import Path

from oculidoc.application import (
    RegisterPatientRequest,
)
from oculidoc.application.clinical_trends import (
    build_patient_trend_document,
    generate_patient_trend_report,
)
from oculidoc.application.gaze_task_session import (
    create_gaze_task_launch,
    finalize_gaze_task_launch,
)
from oculidoc.infrastructure.database import (
    initialize_database,
)


def write_tracking_result(
    launch: object,
    *,
    valid_sample_ratio: float,
    target_hit_ratio: float,
    target_hit_duration_ratio: float,
    first_target_acquired_ms: float,
    longest_continuous_tracking_ms: float,
    target_loss_count: int,
    target_reacquisition_count: int,
) -> None:
    run_directory = launch.session_directory / "tasks" / "run-trend"
    run_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    (run_directory / "gaze_events.parquet").write_bytes(b"parquet")
    (run_directory / "run_manifest.json").write_text(
        '{"status":"finished"}\n',
        encoding="utf-8",
    )
    (run_directory / "task_events.jsonl").write_text(
        ('{"event_type":"tracking_started"}\n{"event_type":"tracking_completed"}\n'),
        encoding="utf-8",
    )
    (run_directory / "task_result.json").write_text(
        json.dumps(
            {
                "run_id": (run_directory.name),
                "task_kind": "tracking_ball",
                "end_reason": "timeout",
                "summary": {
                    "sample_count": 100,
                    "valid_sample_ratio": (valid_sample_ratio),
                    "dwell_by_role_ms": {
                        "target": 4000.0,
                    },
                },
                "result": {
                    "completion_status": ("completed"),
                    "completion_reason": ("timeout"),
                    "sample_count": 100,
                    "valid_sample_ratio": (valid_sample_ratio),
                    "target_hit_ratio": (target_hit_ratio),
                    "target_hit_duration_ratio": (target_hit_duration_ratio),
                    "first_target_acquired_ms": (first_target_acquired_ms),
                    "longest_continuous_tracking_ms": (longest_continuous_tracking_ms),
                    "target_loss_count": (target_loss_count),
                    "target_reacquisition_count": (target_reacquisition_count),
                    "tracking_error_normalized": {
                        "mean": (0.10 - target_hit_ratio * 0.05),
                        "median": 0.04,
                        "p95": 0.12,
                    },
                    "recording_failed": False,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def create_tracking_session(
    runtime: object,
    patient_id: object,
    *,
    valid_sample_ratio: float,
    target_hit_ratio: float,
    target_hit_duration_ratio: float,
    first_target_acquired_ms: float,
    longest_continuous_tracking_ms: float,
    target_loss_count: int,
    target_reacquisition_count: int,
) -> object:
    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient_id,
        module_id="tracking_ball",
    )
    write_tracking_result(
        launch,
        valid_sample_ratio=(valid_sample_ratio),
        target_hit_ratio=(target_hit_ratio),
        target_hit_duration_ratio=(target_hit_duration_ratio),
        first_target_acquired_ms=(first_target_acquired_ms),
        longest_continuous_tracking_ms=(longest_continuous_tracking_ms),
        target_loss_count=(target_loss_count),
        target_reacquisition_count=(target_reacquisition_count),
    )
    finalize_gaze_task_launch(
        runtime.experiment_session_service,
        launch,
        exit_code=0,
    )
    return launch


def test_trend_document_compares_same_task_sessions(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=("DOC-M3D9-TREND"),
            family_name="Trend",
        )
    )
    first = create_tracking_session(
        runtime,
        patient.patient_id,
        valid_sample_ratio=0.50,
        target_hit_ratio=0.40,
        target_hit_duration_ratio=0.35,
        first_target_acquired_ms=900.0,
        longest_continuous_tracking_ms=1200.0,
        target_loss_count=4,
        target_reacquisition_count=2,
    )
    time.sleep(0.002)
    second = create_tracking_session(
        runtime,
        patient.patient_id,
        valid_sample_ratio=0.85,
        target_hit_ratio=0.65,
        target_hit_duration_ratio=0.60,
        first_target_acquired_ms=600.0,
        longest_continuous_tracking_ms=2200.0,
        target_loss_count=2,
        target_reacquisition_count=1,
    )

    document = build_patient_trend_document(
        runtime.experiment_session_service,
        patient.patient_id,
        anchor_session_id=(second.session_id),
    )

    assert document["session_count"] == 2
    assert document["point_count"] == 2
    assert document["quality_warning_counts"]["low_valid_sample_ratio"] == 1
    points = document["modules"]["tracking_ball"]["points"]
    assert [point["session_id"] for point in points] == [
        str(first.session_id),
        str(second.session_id),
    ]
    comparison = points[1]["comparison"]

    assert comparison["previous_session_id"] == str(first.session_id)
    assert comparison["delta"]["target_hit_ratio"] == 0.25
    assert comparison["delta"]["first_target_acquired_ms"] == -300.0
    assert document["interpretation_policy"]["automatic_improvement_label"] is False

    runtime.dispose()


def test_trend_report_generates_and_registers_artifacts(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=("DOC-M3D9-REPORT"),
            family_name="Report",
        )
    )
    launch = create_tracking_session(
        runtime,
        patient.patient_id,
        valid_sample_ratio=0.90,
        target_hit_ratio=0.70,
        target_hit_duration_ratio=0.65,
        first_target_acquired_ms=500.0,
        longest_continuous_tracking_ms=2500.0,
        target_loss_count=1,
        target_reacquisition_count=1,
    )

    artifacts = generate_patient_trend_report(
        runtime.experiment_session_service,
        launch.session_id,
    )

    assert artifacts.report_json_path.is_file()
    assert artifacts.html_path.is_file()
    assert artifacts.data_quality_path is not None
    assert artifacts.data_quality_path.is_file()
    assert artifacts.tracking_path is not None
    assert artifacts.tracking_path.is_file()
    assert artifacts.binary_path is None

    payload = json.loads(artifacts.report_json_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.0"
    assert payload["patient_id"] == str(patient.patient_id)
    assert payload["usable_point_count"] == 1

    html_text = artifacts.html_path.read_text(encoding="utf-8")

    assert "患者纵向趋势报告" in html_text
    assert "质量提示" in html_text
    assert "变化值不能单独解释" in html_text

    registered = {
        artifact.relative_path
        for artifact in runtime.experiment_session_service.list_artifacts(launch.session_id)
    }

    assert any(path.endswith("/trend_report.json") for path in registered)
    assert any(path.endswith("/trend_report.html") for path in registered)
    assert any(path.endswith("/tracking_trend.png") for path in registered)

    runtime.dispose()
