"""Tests for report generation from session history."""

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


def _completed_patient(
    runtime: object,
) -> object:
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=("DOC-REPORT-UI"),
            family_name="Report",
        )
    )
    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="tracking_ball",
    )
    run_directory = launch.session_directory / "tasks" / "run-report-ui"
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
                    "sample_count": 5,
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
    return patient


def test_history_generates_and_opens_report(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = _completed_patient(runtime)
    report_path = tmp_path / "report.html"
    report_path.write_text(
        "<html></html>",
        encoding="utf-8",
    )
    generated: list[object] = []
    opened: list[object] = []

    def fake_generate(
        service: object,
        session_id: object,
    ) -> object:
        generated.append((service, session_id))
        return SimpleNamespace(html_path=report_path)

    monkeypatch.setattr(
        history_module,
        "generate_gaze_session_report",
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

    dialog = PatientSessionHistoryDialog(
        runtime.experiment_session_service,
        patient,
    )
    qtbot.addWidget(dialog)

    dialog.report_button.click()

    assert len(generated) == 1
    assert len(opened) == 1

    dialog.close()
    runtime.dispose()
