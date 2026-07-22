from __future__ import annotations

import json
from pathlib import Path

from oculidoc.application import (
    RegisterPatientRequest,
)
from oculidoc.application.gaze_report import (
    _load_task_results,
    _task_result_sections,
)
from oculidoc.application.gaze_task_session import (
    create_gaze_task_launch,
    finalize_gaze_task_launch,
)
from oculidoc.application.session_history import (
    build_patient_session_history,
    format_task_result_lines,
)
from oculidoc.infrastructure.database import (
    initialize_database,
)


def write_result(
    run_directory: Path,
    *,
    task_kind: str,
    result: dict[str, object],
    event_types: tuple[str, ...],
) -> None:
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
        "".join(
            json.dumps(
                {
                    "event_type": event_type,
                    "payload": {},
                }
            )
            + "\n"
            for event_type in event_types
        ),
        encoding="utf-8",
    )
    (run_directory / "task_result.json").write_text(
        json.dumps(
            {
                "run_id": run_directory.name,
                "task_kind": task_kind,
                "end_reason": (
                    result.get(
                        "completion_reason",
                        "completed",
                    )
                ),
                "summary": {
                    "sample_count": 10,
                    "valid_sample_ratio": 0.8,
                    "dwell_by_role_ms": {
                        "target": 600.0,
                    },
                },
                "result": {
                    **result,
                    "recording_failed": False,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_report_loads_binary_and_tracking_results(
    tmp_path: Path,
) -> None:
    binary_directory = tmp_path / "tasks" / "run-binary"
    tracking_directory = tmp_path / "tasks" / "run-tracking"
    write_result(
        binary_directory,
        task_kind="binary_question",
        result={
            "completion_status": "answered",
            "completion_reason": "answered",
            "question": "北京是中国的首都吗？",
            "question_type": "yes_no",
            "selected_option_id": "option_1",
            "selected_side": "right",
            "selected_answer": "是",
            "is_scored": True,
            "correct": True,
            "reaction_time_ms": 420.0,
            "confirmation_dwell_ms": 400.0,
        },
        event_types=(
            "question_presented",
            "answer_committed",
            "task_completed",
        ),
    )
    write_result(
        tracking_directory,
        task_kind="tracking_ball",
        result={
            "completion_status": "completed",
            "completion_reason": "timeout",
            "valid_sample_ratio": 0.9,
            "target_hit_ratio": 0.75,
            "target_hit_duration_ratio": 0.7,
            "first_target_entry_ms": 100.0,
            "first_target_acquired_ms": 500.0,
            "longest_continuous_tracking_ms": 2400.0,
            "target_loss_count": 2,
            "target_reacquisition_count": 1,
            "tracking_error_normalized": {
                "mean": 0.04,
                "median": 0.03,
                "p95": 0.09,
            },
            "tracking_error_px": {
                "mean": 28.0,
                "median": 20.0,
                "p95": 64.0,
            },
        },
        event_types=(
            "tracking_started",
            "target_acquired",
            "tracking_completed",
        ),
    )

    records = _load_task_results(tmp_path)

    assert len(records) == 2
    binary = next(record for record in records if record["task_kind"] == "binary_question")
    tracking = next(record for record in records if record["task_kind"] == "tracking_ball")

    assert binary["result"]["selected_answer"] == "是"
    assert binary["event_counts"]["answer_committed"] == 1
    assert tracking["result"]["target_hit_ratio"] == 0.75

    rendered = _task_result_sections(records)

    assert "结构化任务结果" in rendered
    assert "北京是中国的首都吗？" in rendered
    assert "目标命中率" in rendered
    assert "75.0%" in rendered


def test_report_and_history_format_multiple_choice_without_scoring(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "tasks" / "run-multiple"
    write_result(
        run_directory,
        task_kind="multiple_choice",
        result={
            "completion_status": "selected",
            "completion_reason": "manual_exit",
            "question": "您现在需要什么？",
            "selected_option_ids": ["option_1", "option_3"],
            "selected_answers": ["喝水", "翻身"],
            "selected_count": 2,
            "toggle_count": 4,
            "layout": "grid",
            "is_scored": False,
            "first_selection_reaction_time_ms": 650.0,
        },
        event_types=("option_selected", "option_cancelled", "task_completed"),
    )

    records = _load_task_results(tmp_path)
    rendered = _task_result_sections(records)
    lines = format_task_result_lines(tuple(records))

    assert "您现在需要什么？" in rendered
    assert "喝水、翻身" in rendered
    assert "评分结果</th><td>不评分" in rendered
    assert "患者选择：喝水、翻身" in lines
    assert "首次选择反应时间：650 ms" in lines


def test_report_and_history_format_instruction_fixation_without_diagnosis(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "tasks" / "run-fixation"
    write_result(
        run_directory,
        task_kind="instruction_fixation",
        result={
            "completion_status": "completed",
            "completion_reason": "completed",
            "trial_count": 4,
            "completed_trial_count": 4,
            "target_present_trial_count": 3,
            "target_acquired_trial_count": 2,
            "target_acquisition_ratio": 2 / 3,
            "no_target_trial_count": 1,
            "no_target_false_fixation_count": 1,
            "distractor_fixation_count": 2,
            "valid_sample_ratio": 0.8,
            "mean_first_target_entry_ms": 420.0,
            "mean_first_target_acquired_ms": 1_280.0,
            "longest_continuous_target_fixation_ms": 2_000.0,
            "trials": [
                {
                    "trial_number": 1,
                    "condition": "target_only",
                    "outcome": "target_acquired",
                    "first_target_entry_ms": 300.0,
                    "first_target_acquired_ms": 1_200.0,
                }
            ],
        },
        event_types=("stimulus_presented", "selection_committed", "trial_completed"),
    )

    records = _load_task_results(tmp_path)
    rendered = _task_result_sections(records)
    lines = format_task_result_lines(tuple(records))

    assert "目标稳定注视比例" in rendered
    assert "66.7%" in rendered
    assert "不自动判定意识状态" in rendered
    assert "目标稳定注视比例：66.7%" in lines
    assert "无目标试次干扰稳定注视：1/1" in lines


def test_history_exposes_structured_result_lines(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-M3D8-HISTORY",
            family_name="M3D8",
        )
    )
    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="tracking_ball",
    )
    write_result(
        launch.session_directory / "tasks" / "run-history",
        task_kind="tracking_ball",
        result={
            "completion_status": "completed",
            "completion_reason": "timeout",
            "target_hit_ratio": 0.75,
            "target_hit_duration_ratio": 0.7,
            "first_target_acquired_ms": 500.0,
            "longest_continuous_tracking_ms": 2400.0,
            "target_loss_count": 2,
            "target_reacquisition_count": 1,
        },
        event_types=(
            "tracking_started",
            "target_acquired",
            "tracking_completed",
        ),
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
    entry = entries[0]

    assert len(entry.task_results) == 1
    result = entry.task_results[0]["result"]

    assert result["target_hit_ratio"] == 0.75
    lines = format_task_result_lines(entry.task_results)
    text = "\n".join(lines)

    assert "目标命中率：75.0%" in text
    assert "首次稳定获得：500 ms" in text
    assert "目标丢失/重新获得：2/1" in text

    runtime.dispose()
