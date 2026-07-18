"""Tests for patient session-history UI."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QMessageBox
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

import oculidoc.ui.main_window as main_window_module
from oculidoc.application import (
    RegisterPatientRequest,
)
from oculidoc.application.gaze_task_session import (
    create_gaze_task_launch,
    finalize_gaze_task_launch,
)
from oculidoc.config import Settings
from oculidoc.infrastructure.database import (
    initialize_database,
)
from oculidoc.ui.main_window import (
    AdminMainWindow,
)
from oculidoc.ui.session_history import (
    PatientSessionHistoryDialog,
)


def create_patient_and_sessions(
    runtime: object,
) -> object:
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=("DOC-HISTORY-UI"),
            family_name="History",
        )
    )

    completed_launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="tracking_ball",
    )
    run_directory = completed_launch.session_directory / "tasks" / "run-ui"
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
                    "sample_count": 25,
                    "valid_sample_ratio": 0.8,
                    "dwell_by_role_ms": {
                        "target": 1000.0,
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
        completed_launch,
        exit_code=0,
    )

    aborted_launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="binary_horizontal",
    )
    finalize_gaze_task_launch(
        runtime.experiment_session_service,
        aborted_launch,
        exit_code=0,
    )

    return patient


def test_history_dialog_lists_and_filters(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = create_patient_and_sessions(runtime)
    dialog = PatientSessionHistoryDialog(
        runtime.experiment_session_service,
        patient,
    )
    qtbot.addWidget(dialog)

    assert dialog.table.rowCount() == 2

    tracking_index = dialog.module_filter.findData("tracking_ball")
    dialog.module_filter.setCurrentIndex(tracking_index)

    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "追踪球"
    assert dialog.table.item(0, 4).text() == "25"
    assert dialog.table.item(0, 5).text() == "80.0%"
    assert "target" in (dialog.detail_label.text())

    dialog.close()
    runtime.dispose()


def test_main_window_opens_history_dialog(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
    )
    runtime = initialize_database(
        settings.database_path,
        data_root=settings.data_dir,
    )
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=("DOC-HISTORY-MAIN"),
            family_name="Main",
        )
    )
    opened: list[tuple[object, object]] = []

    class StubHistoryDialog:
        def __init__(
            self,
            service: object,
            selected_patient: object,
            parent: object = None,
        ) -> None:
            opened.append(
                (
                    service,
                    selected_patient,
                )
            )
            self.parent = parent

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(
        main_window_module,
        "PatientSessionHistoryDialog",
        StubHistoryDialog,
    )

    window = AdminMainWindow(
        settings,
        runtime.patient_service,
        runtime.experiment_session_service,
    )
    qtbot.addWidget(window)
    window._set_current_patient(patient)

    window.history_button.click()

    assert opened == [
        (
            runtime.experiment_session_service,
            patient,
        )
    ]

    window.close()
    runtime.dispose()


def test_history_requires_current_patient(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
    )
    runtime = initialize_database(
        settings.database_path,
        data_root=settings.data_dir,
    )
    messages: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append(args),
    )

    window = AdminMainWindow(
        settings,
        runtime.patient_service,
        runtime.experiment_session_service,
    )
    qtbot.addWidget(window)

    window.history_button.click()

    assert messages
    assert any("尚未选择患者" in str(value) for call in messages for value in call)

    window.close()
    runtime.dispose()
