"""Tests for patient-scoped gaze task sessions."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault(
    "QT_QPA_PLATFORM",
    "offscreen",
)

from PySide6.QtWidgets import QMessageBox, QWidget
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
from oculidoc.devices.contracts import (
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
    SessionArtifactKind,
)
from oculidoc.experiments.task_runtime import (
    RecordedTaskRuntime,
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

    def __init__(
        self,
        parent: object = None,
    ) -> None:
        self.parent = parent
        self.finished = StubSignal()
        self.environment = None
        self.program = ""
        self.arguments: list[str] = []
        self.channel_mode = None
        self.started = False
        self.output = b""
        type(self).instances.append(self)

    def setProcessEnvironment(
        self,
        environment: object,
    ) -> None:
        self.environment = environment

    def setProgram(self, program: str) -> None:
        self.program = program

    def setArguments(
        self,
        arguments: list[str],
    ) -> None:
        self.arguments = arguments

    def setProcessChannelMode(
        self,
        mode: object,
    ) -> None:
        self.channel_mode = mode

    def start(self) -> None:
        self.started = True

    def waitForStarted(
        self,
        timeout_ms: int,
    ) -> bool:
        del timeout_ms
        return True

    def errorString(self) -> str:
        return "stub process error"

    def readAllStandardOutput(self) -> bytes:
        return self.output


class FakeTask(QWidget):
    def consume_sample(
        self,
        sample: EyeTrackerSample,
    ) -> None:
        del sample


def sample() -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=0,
            monotonic_timestamp_ns=(1_000_000_000),
            utc_timestamp=datetime(
                2026,
                7,
                17,
                12,
                0,
                tzinfo=UTC,
            ),
            source_timestamp_ns=(1_000_000_000),
            source_clock_id="binding-test",
        ),
        gaze_x_normalized=0.5,
        gaze_y_normalized=0.5,
        left_eye_valid=True,
        right_eye_valid=True,
    )


def create_patient(runtime):
    return runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-GAZE-SESSION",
            family_name="Gaze",
        )
    )


def write_completed_run(
    launch,
) -> Path:
    run_directory = launch.session_directory / "tasks" / "run-test"
    run_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    (run_directory / "gaze_events.parquet").write_bytes(b"parquet")
    (run_directory / "task_events.jsonl").write_text(
        '{"event_type":"closed"}\n',
        encoding="utf-8",
    )
    (run_directory / "run_manifest.json").write_text(
        '{"status":"finished"}\n',
        encoding="utf-8",
    )
    (run_directory / "task_result.json").write_text(
        json.dumps(
            {
                "result": {
                    "recording_failed": False,
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_directory


def test_binding_uses_patient_session_workspace(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = create_patient(runtime)

    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="tracking_ball",
    )

    session = runtime.experiment_session_service.get_session(launch.session_id)

    assert session.status is (ExperimentSessionStatus.RUNNING)
    assert launch.patient_id == (patient.patient_id)
    assert launch.command == "tracking"
    assert launch.session_directory == (
        runtime.experiment_session_service.resolve_session_directory(launch.session_id)
    )
    assert launch.process_environment["OCULIDOC_PATIENT_ID"] == str(patient.patient_id)
    assert launch.process_environment["OCULIDOC_SESSION_ID"] == str(launch.session_id)

    runtime.dispose()


def test_completed_run_registers_artifacts(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = create_patient(runtime)
    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="binary_horizontal",
    )
    write_completed_run(launch)

    status = finalize_gaze_task_launch(
        runtime.experiment_session_service,
        launch,
        exit_code=0,
    )

    assert status is (ExperimentSessionStatus.COMPLETED)

    artifacts = runtime.experiment_session_service.list_artifacts(launch.session_id)
    artifacts_by_name = {Path(artifact.relative_path).name: artifact for artifact in artifacts}

    assert {
        "session.json",
        "gaze_events.parquet",
        "task_events.jsonl",
        "run_manifest.json",
        "task_result.json",
    }.issubset(artifacts_by_name)
    assert artifacts_by_name["gaze_events.parquet"].kind is SessionArtifactKind.GAZE
    assert artifacts_by_name["task_events.jsonl"].kind is SessionArtifactKind.EVENTS
    assert artifacts_by_name["gaze_events.parquet"].sha256 is not None

    runtime.dispose()


def test_vertical_binary_uses_distinct_patient_session_command(tmp_path: Path) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = create_patient(runtime)

    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="binary_vertical",
    )

    assert launch.module_id == "binary_vertical"
    assert launch.command == "binary-vertical"
    runtime.dispose()


def test_multiple_choice_uses_distinct_patient_session_command(tmp_path: Path) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = create_patient(runtime)

    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="multiple_choice",
    )

    assert launch.module_id == "multiple_choice"
    assert launch.command == "multiple-choice"
    runtime.dispose()


def test_cancelled_setup_aborts_session(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(
        tmp_path / "oculidoc.sqlite3",
        data_root=tmp_path / "data",
    )
    patient = create_patient(runtime)
    launch = create_gaze_task_launch(
        runtime.experiment_session_service,
        patient_id=patient.patient_id,
        module_id="tracking_ball",
    )

    status = finalize_gaze_task_launch(
        runtime.experiment_session_service,
        launch,
        exit_code=0,
    )

    assert status is (ExperimentSessionStatus.ABORTED)

    runtime.dispose()


def test_runtime_honors_exact_session_directory(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    exact_directory = tmp_path / "data" / "sessions" / "patient" / "session"
    monkeypatch.setenv(
        "OCULIDOC_SESSION_DIRECTORY",
        str(exact_directory),
    )
    monkeypatch.setenv(
        "OCULIDOC_PATIENT_ID",
        "patient",
    )
    monkeypatch.setenv(
        "OCULIDOC_SESSION_ID",
        "session",
    )

    task = FakeTask()
    qtbot.addWidget(task)
    recording = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
    )
    recording.handle_sample(sample())
    recording.finish("test_complete")

    assert recording.run_directory is not None
    assert recording.run_directory.parent.parent == exact_directory.resolve()


def test_main_window_launches_patient_scoped_task(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    StubQProcess.instances.clear()
    settings = Settings(
        data_dir=tmp_path / "data",
    )
    runtime = initialize_database(
        settings.database_path,
        data_root=settings.data_dir,
    )
    patient = create_patient(runtime)

    monkeypatch.setattr(
        main_window_module,
        "QProcess",
        StubQProcess,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: None,
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: None,
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args: None,
    )

    window = AdminMainWindow(
        settings,
        runtime.patient_service,
        runtime.experiment_session_service,
    )
    qtbot.addWidget(window)
    window._set_current_patient(patient)

    module = next(item for item in DEFAULT_MODULES if item.module_id == "tracking_ball")
    window._open_module(module)

    assert len(window._gaze_processes) == 1
    session_id, process = next(iter(window._gaze_processes.items()))
    launch = window._gaze_launches[session_id]

    assert process.started is True
    assert process.program
    assert process.arguments == [
        "-m",
        "oculidoc.tasks",
        "tracking",
    ]
    assert process.environment.value("OCULIDOC_PATIENT_ID") == str(patient.patient_id)
    assert process.environment.value("OCULIDOC_SESSION_ID") == str(session_id)
    assert process.environment.value("OCULIDOC_SESSION_DIRECTORY") == str(launch.session_directory)

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
    assert completed.status is (ExperimentSessionStatus.COMPLETED)
    assert session_id not in (window._gaze_processes)
    assert window._lan_state_store.load().mode is PatientDisplayMode.RESULT

    window.close()
    runtime.dispose()
