"""Tests for gaze report and heatmap generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from oculidoc.application import (
    RegisterPatientRequest,
)
from oculidoc.application.gaze_report import (
    generate_gaze_session_report,
)
from oculidoc.application.gaze_task_session import (
    create_gaze_task_launch,
    finalize_gaze_task_launch,
)
from oculidoc.infrastructure.database import (
    initialize_database,
)


def _write_run(
    launch: object,
    rows: list[dict[str, object]],
) -> None:
    run_directory = launch.session_directory / "tasks" / "run-report"
    run_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    pq.write_table(
        pa.Table.from_pylist(rows),
        run_directory / "gaze_events.parquet",
    )
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
                    "sample_count": len(rows),
                },
                "result": {
                    "recording_failed": False,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _runtime_and_launch(
    tmp_path: Path,
    *,
    module_id: str,
) -> tuple[object, object]:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=(f"DOC-REPORT-{module_id}"),
            family_name="Report",
        )
    )
    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id=module_id,
    )
    return runtime, launch


def test_tracking_report_generates_heatmap_and_error(
    tmp_path: Path,
) -> None:
    runtime, launch = _runtime_and_launch(
        tmp_path,
        module_id="tracking_ball",
    )
    rows = [
        {
            "monotonic_timestamp_ns": 1_000_000_000,
            "analysis_valid": True,
            "gaze_x_normalized": 0.50,
            "gaze_y_normalized": 0.50,
            "duration_ms": 100.0,
            "aoi_role": "target",
            "question_id": "tracking-target",
            "reference_aoi_left": 0.40,
            "reference_aoi_top": 0.40,
            "reference_aoi_right": 0.60,
            "reference_aoi_bottom": 0.60,
        },
        {
            "monotonic_timestamp_ns": 1_500_000_000,
            "analysis_valid": True,
            "gaze_x_normalized": 0.80,
            "gaze_y_normalized": 0.50,
            "duration_ms": 100.0,
            "aoi_role": "non_option",
            "question_id": "tracking-target",
            "reference_aoi_left": 0.40,
            "reference_aoi_top": 0.40,
            "reference_aoi_right": 0.60,
            "reference_aoi_bottom": 0.60,
        },
        {
            "monotonic_timestamp_ns": 2_000_000_000,
            "analysis_valid": False,
            "gaze_x_normalized": None,
            "gaze_y_normalized": None,
            "duration_ms": 100.0,
            "aoi_role": None,
            "question_id": "tracking-target",
            "reference_aoi_left": 0.60,
            "reference_aoi_top": 0.40,
            "reference_aoi_right": 0.80,
            "reference_aoi_bottom": 0.60,
        },
    ]
    _write_run(launch, rows)
    finalize_gaze_task_launch(
        runtime.experiment_session_service,
        launch,
        exit_code=0,
    )

    artifacts = generate_gaze_session_report(
        runtime.experiment_session_service,
        launch.session_id,
    )

    assert artifacts.html_path.is_file()
    assert artifacts.screen_heatmap_path.is_file()
    assert artifacts.semantic_aoi_path.is_file()
    assert artifacts.tracking_error_path is not None
    assert artifacts.tracking_error_path.is_file()
    assert artifacts.tracking_error_timeline_path is not None
    assert artifacts.tracking_error_timeline_path.is_file()

    payload = json.loads(artifacts.report_json_path.read_text(encoding="utf-8"))
    metrics = payload["metrics"]

    assert payload["patient_name"] == "Report"
    assert payload["patient_display_label"] == "Report患者（DOC-REPORT-tracking_ball）"
    assert metrics["sample_count"] == 3
    assert metrics["valid_sample_count"] == 2
    assert metrics["valid_sample_ratio"] == 2 / 3
    assert metrics["tracking"]["sample_count"] == 2
    assert metrics["tracking"]["target_reference_sample_count"] == 3
    assert metrics["tracking"]["target_hit_ratio"] == 0.5
    assert metrics["tracking"]["rmse_normalized"] == pytest.approx(0.3 / 2**0.5)

    html_text = artifacts.html_path.read_text(encoding="utf-8")
    assert "tracking_error_timeline.png" in html_text
    assert "Report患者（DOC-REPORT-tracking_ball）" in html_text
    assert str(cast(Any, launch).patient_id) not in html_text
    assert html_text.count("简要分析") >= 5

    registered_paths = {
        artifact.relative_path
        for artifact in (runtime.experiment_session_service.list_artifacts(launch.session_id))
    }
    assert any(path.endswith("/report.html") for path in registered_paths)
    assert any(path.endswith("/screen_heatmap.png") for path in registered_paths)
    assert any(path.endswith("/tracking_error_timeline.png") for path in registered_paths)

    runtime.dispose()


def test_binary_report_summarizes_semantic_dwell(
    tmp_path: Path,
) -> None:
    runtime, launch = _runtime_and_launch(
        tmp_path,
        module_id=("binary_horizontal"),
    )
    rows = [
        {
            "analysis_valid": True,
            "gaze_x_normalized": 0.20,
            "gaze_y_normalized": 0.50,
            "duration_ms": 300.0,
            "aoi_role": "correct_option",
            "question_id": "q1",
            "reference_aoi_left": None,
            "reference_aoi_top": None,
            "reference_aoi_right": None,
            "reference_aoi_bottom": None,
        },
        {
            "analysis_valid": True,
            "gaze_x_normalized": 0.80,
            "gaze_y_normalized": 0.50,
            "duration_ms": 100.0,
            "aoi_role": ("incorrect_option"),
            "question_id": "q1",
            "reference_aoi_left": None,
            "reference_aoi_top": None,
            "reference_aoi_right": None,
            "reference_aoi_bottom": None,
        },
        {
            "analysis_valid": True,
            "gaze_x_normalized": 0.50,
            "gaze_y_normalized": 0.05,
            "duration_ms": 100.0,
            "aoi_role": "non_option",
            "question_id": "q1",
            "reference_aoi_left": None,
            "reference_aoi_top": None,
            "reference_aoi_right": None,
            "reference_aoi_bottom": None,
        },
    ]
    _write_run(launch, rows)
    finalize_gaze_task_launch(
        runtime.experiment_session_service,
        launch,
        exit_code=0,
    )

    artifacts = generate_gaze_session_report(
        runtime.experiment_session_service,
        launch.session_id,
    )
    payload = json.loads(artifacts.report_json_path.read_text(encoding="utf-8"))
    binary = payload["metrics"]["binary"]

    assert binary["correct_option_dwell_ms"] == 300.0
    assert binary["incorrect_option_dwell_ms"] == 100.0
    assert binary["non_option_dwell_ms"] == 100.0
    assert binary["correct_option_share"] == 0.75
    assert artifacts.tracking_error_path is None
    assert artifacts.tracking_error_timeline_path is None

    runtime.dispose()
