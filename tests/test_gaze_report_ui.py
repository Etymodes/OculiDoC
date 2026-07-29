"""Tests for report generation from session history."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import (
    QInputDialog,
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
        history_module,
        "generate_patient_trend_report",
        fake_generate,
    )

    class StubWebView(history_module.QWidget):
        def setUrl(self, url: object) -> None:
            opened.append(url)

    monkeypatch.setattr(
        history_module,
        "QWebEngineView",
        StubWebView,
    )
    monkeypatch.setattr(
        history_module.QDialog,
        "exec",
        lambda _self: 0,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: None,
    )
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda _parent, _title, _prompt, items, _current, _editable: (items[0], True),
    )

    dialog = PatientSessionHistoryDialog(
        runtime.experiment_session_service,
        patient,
    )
    qtbot.addWidget(dialog)

    assert dialog.report_button.text() == "生成单次报告…"
    selected = dialog._current_entry()

    assert selected is not None

    dialog.summary_button.click()

    assert len(generated) == 2
    assert len(opened) == 1

    summary_path = selected.session_directory / "reports" / "report_summary.html"

    assert summary_path.is_file()
    assert Path(opened[0].toLocalFile()).resolve() == summary_path.resolve()

    dialog.close()
    runtime.dispose()


def test_single_report_popup_selects_exact_session_without_table_preselection(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = _completed_patient(runtime)
    second_launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="binary_horizontal",
    )
    finalize_gaze_task_launch(
        runtime.experiment_session_service,
        second_launch,
        exit_code=0,
    )
    report_path = tmp_path / "selected-report.html"
    report_path.write_text("<html></html>", encoding="utf-8")
    generated: list[object] = []

    monkeypatch.setattr(
        history_module,
        "generate_gaze_session_report",
        lambda _service, session_id: (
            generated.append(session_id) or SimpleNamespace(html_path=report_path)
        ),
    )
    monkeypatch.setattr(
        history_module.QDesktopServices,
        "openUrl",
        lambda _url: True,
    )

    dialog = PatientSessionHistoryDialog(
        runtime.experiment_session_service,
        patient,
    )
    qtbot.addWidget(dialog)
    options = dialog._single_report_options()
    target_entry, target_label = next(
        option for option in options if option[0].session_id != second_launch.session_id
    )

    dialog.table.clearSelection()
    dialog.table.setCurrentCell(-1, -1)
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda _parent, _title, _prompt, items, _current, _editable: (
            target_label if target_label in items else "",
            target_label in items,
        ),
    )

    dialog.report_button.click()

    assert generated == [target_entry.session_id]
    assert "第 1 次" in target_label

    dialog.close()
    runtime.dispose()
