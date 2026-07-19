from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import (
    QMessageBox,
)
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

import oculidoc.ui.session_history as history_module
from oculidoc.application import (
    RegisterPatientRequest,
)
from oculidoc.application.gaze_task_session import (
    create_gaze_task_launch,
    finalize_gaze_task_launch,
)
from oculidoc.infrastructure.database import (
    initialize_database,
)
from oculidoc.ui.session_history import (
    PatientSessionHistoryDialog,
)


def completed_patient(
    runtime: object,
) -> object:
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=("DOC-M3D9B-UI"),
            family_name="TrendUI",
        )
    )
    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="tracking_ball",
    )
    run_directory = launch.session_directory / "tasks" / "run-trend-ui"
    run_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    (run_directory / "gaze_events.parquet").write_bytes(b"parquet")
    (run_directory / "task_events.jsonl").write_text(
        ('{"event_type":"tracking_started"}\n{"event_type":"tracking_completed"}\n'),
        encoding="utf-8",
    )
    (run_directory / "run_manifest.json").write_text(
        '{"status":"finished"}\n',
        encoding="utf-8",
    )
    (run_directory / "task_result.json").write_text(
        json.dumps(
            {
                "run_id": "run-trend-ui",
                "task_kind": "tracking_ball",
                "end_reason": "timeout",
                "summary": {
                    "sample_count": 20,
                    "valid_sample_ratio": 0.8,
                },
                "result": {
                    "completion_status": ("completed"),
                    "completion_reason": ("timeout"),
                    "valid_sample_ratio": 0.8,
                    "target_hit_ratio": 0.7,
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
    return patient


def test_history_generates_and_opens_patient_trend(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = completed_patient(runtime)
    report_path = tmp_path / "trend_report.html"
    report_path.write_text(
        "<html></html>",
        encoding="utf-8",
    )
    generated: list[tuple[object, object]] = []
    opened: list[object] = []

    def fake_generate(
        service: object,
        session_id: object,
    ) -> object:
        generated.append(
            (
                service,
                session_id,
            )
        )
        return SimpleNamespace(html_path=report_path)

    monkeypatch.setattr(
        history_module,
        "generate_patient_trend_report",
        fake_generate,
    )
    monkeypatch.setattr(
        history_module.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url) or True,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: None,
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args: None,
    )

    dialog = PatientSessionHistoryDialog(
        runtime.experiment_session_service,
        patient,
    )
    qtbot.addWidget(dialog)

    assert dialog.trend_button.objectName() == "generatePatientTrendReportButton"

    selected = dialog._current_entry()

    assert selected is not None

    dialog.trend_button.click()

    assert generated == [
        (
            runtime.experiment_session_service,
            selected.session_id,
        )
    ]
    assert len(opened) == 1

    dialog.close()
    runtime.dispose()


def test_trend_report_failure_is_shown(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = completed_patient(runtime)
    messages: list[tuple[object, ...]] = []

    def fail_generate(
        service: object,
        session_id: object,
    ) -> object:
        del service, session_id
        raise RuntimeError("trend failed")

    monkeypatch.setattr(
        history_module,
        "generate_patient_trend_report",
        fail_generate,
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args: messages.append(args),
    )

    dialog = PatientSessionHistoryDialog(
        runtime.experiment_session_service,
        patient,
    )
    qtbot.addWidget(dialog)

    dialog.trend_button.click()

    assert messages
    assert any(
        "趋势报告生成失败" in str(value) or "trend failed" in str(value)
        for call in messages
        for value in call
    )

    dialog.close()
    runtime.dispose()
