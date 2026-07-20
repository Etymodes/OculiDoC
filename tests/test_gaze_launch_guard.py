"""Tests for gaze-task duplicate launch protection."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QMessageBox
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

import oculidoc.ui.main_window as main_window_module
from oculidoc.application import RegisterPatientRequest
from oculidoc.config import Settings
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
)
from oculidoc.infrastructure.database import (
    initialize_database,
)
from oculidoc.lan_control import PatientDisplayMode
from oculidoc.modules.registry import DEFAULT_MODULES
from oculidoc.ui.main_window import AdminMainWindow


class StubSignal:
    def __init__(self) -> None:
        self._callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self._callbacks.append(callback)

    def emit(self, *args: object) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


class StubQProcess:
    class ProcessChannelMode:
        MergedChannels = object()

    instances: list[StubQProcess] = []
    start_succeeds = True

    def __init__(self, parent: object = None) -> None:
        self.parent = parent
        self.finished = StubSignal()
        self.environment = None
        self.program = ""
        self.arguments: list[str] = []
        self.channel_mode = None
        self.started = False
        self.output = b""
        type(self).instances.append(self)

    def setProcessEnvironment(self, environment: object) -> None:
        self.environment = environment

    def setProgram(self, program: str) -> None:
        self.program = program

    def setArguments(self, arguments: list[str]) -> None:
        self.arguments = arguments

    def setProcessChannelMode(self, mode: object) -> None:
        self.channel_mode = mode

    def start(self) -> None:
        self.started = True

    def waitForStarted(self, timeout_ms: int) -> bool:
        del timeout_ms
        return type(self).start_succeeds

    def errorString(self) -> str:
        return "stub process did not start"

    def readAllStandardOutput(self) -> bytes:
        return self.output


def write_completed_run(launch: object) -> None:
    run_directory = launch.session_directory / "tasks" / "run-guard-test"
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
                    "sample_count": 12,
                },
                "result": {
                    "recording_failed": False,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def prepare_window(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> tuple[
    AdminMainWindow,
    object,
    object,
    list[tuple[object, ...]],
]:
    StubQProcess.instances.clear()
    StubQProcess.start_succeeds = True

    settings = Settings(
        data_dir=tmp_path / "data",
    )
    runtime = initialize_database(
        settings.database_path,
        data_root=settings.data_dir,
    )
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-GAZE-GUARD",
            family_name="Guard",
        )
    )
    messages: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        main_window_module,
        "QProcess",
        StubQProcess,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append(args),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: messages.append(args),
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args: messages.append(args),
    )

    window = AdminMainWindow(
        settings,
        runtime.patient_service,
        runtime.experiment_session_service,
    )
    qtbot.addWidget(window)
    window._set_current_patient(patient)

    return window, runtime, patient, messages


def module_named(module_id: str) -> object:
    return next(module for module in DEFAULT_MODULES if module.module_id == module_id)


def test_duplicate_module_launch_is_blocked(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window, runtime, patient, messages = prepare_window(
        qtbot,
        tmp_path,
        monkeypatch,
    )
    module = module_named("tracking_ball")
    button = window.module_buttons["tracking_ball"]

    window._open_module(module)

    assert len(StubQProcess.instances) == 1
    assert len(window._gaze_processes) == 1
    assert button.isEnabled() is False
    assert button.text() == "任务运行中…"
    assert "tracking_ball" in window._active_gaze_module_ids

    window._open_module(module)

    sessions = runtime.experiment_session_service.list_sessions_for_patient(patient.patient_id)

    assert len(StubQProcess.instances) == 1
    assert len(sessions) == 1
    assert any("任务已在运行" in str(message) for call in messages for message in call)

    session_id, process = next(iter(window._gaze_processes.items()))
    launch = window._gaze_launches[session_id]

    window._lan_state_store.set_display(
        "准备",
        mode=PatientDisplayMode.READY,
        task_id=launch.module_id,
    )
    window._lan_state_store.set_display(
        "运行中",
        mode=PatientDisplayMode.RUNNING,
        task_id=launch.module_id,
    )
    write_completed_run(launch)
    process.finished.emit(0, None)

    completed = runtime.experiment_session_service.get_session(session_id)

    assert completed.status is ExperimentSessionStatus.COMPLETED
    assert button.isEnabled() is True
    assert button.text() == "打开项目"
    assert "tracking_ball" not in window._active_gaze_module_ids

    window.close()
    runtime.dispose()


def test_start_failure_releases_module_guard(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window, runtime, patient, messages = prepare_window(
        qtbot,
        tmp_path,
        monkeypatch,
    )
    StubQProcess.start_succeeds = False
    module = module_named("binary_horizontal")
    button = window.module_buttons["binary_horizontal"]

    window._open_module(module)

    sessions = runtime.experiment_session_service.list_sessions_for_patient(patient.patient_id)

    assert len(sessions) == 1
    assert sessions[0].status is ExperimentSessionStatus.FAILED
    assert not window._gaze_processes
    assert not window._gaze_launches
    assert "binary_horizontal" not in window._active_gaze_module_ids
    assert button.isEnabled() is True
    assert button.text() == "打开项目"
    assert any("无法启动眼动任务" in str(message) for call in messages for message in call)
    assert window._lan_state_store.load().mode is PatientDisplayMode.ERROR

    window.close()
    runtime.dispose()
