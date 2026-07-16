"""Patient-to-eye-workbench integration tests."""

from collections.abc import Callable
from pathlib import Path

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
from oculidoc.modules.registry import DEFAULT_MODULES
from oculidoc.ui.main_window import AdminMainWindow


class StubSignal:
    def __init__(self) -> None:
        self._callbacks: list[Callable[..., None]] = []

    def connect(
        self,
        callback: Callable[..., None],
    ) -> None:
        self._callbacks.append(callback)

    def emit(
        self,
        *args: object,
    ) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


class StubCameraPreviewWindow:
    def __init__(
        self,
        *,
        patient_key: str,
        dataset_directory: str | Path,
    ) -> None:
        self.patient_key = patient_key
        self.dataset_directory = Path(dataset_directory).resolve()
        self.dataset_directory.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.artifacts_saved = StubSignal()
        self.workbench_closed = StubSignal()
        self.shown = False
        self.closed = False

    def show(self) -> None:
        self.shown = True

    def raise_(self) -> None:
        return None

    def activateWindow(self) -> None:
        return None

    def close(self) -> None:
        if self.closed:
            return

        self.closed = True
        self.workbench_closed.emit()


def test_patient_launches_session_scoped_eye_workbench(
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
            patient_code="DOC-EYE-001",
            family_name="Eye",
        )
    )

    monkeypatch.setattr(
        main_window_module,
        "CameraPreviewWindow",
        StubCameraPreviewWindow,
    )

    window = AdminMainWindow(
        settings,
        runtime.patient_service,
        runtime.experiment_session_service,
    )
    qtbot.addWidget(window)
    window._set_current_patient(patient)

    module = next(item for item in DEFAULT_MODULES if item.module_id == "eye_observation")

    window._open_module(module)

    assert len(window._eye_windows) == 1

    session_id, workbench = next(iter(window._eye_windows.items()))
    session = runtime.experiment_session_service.get_session(session_id)

    assert session.status is (ExperimentSessionStatus.RUNNING)
    assert workbench.shown is True
    assert workbench.patient_key == str(patient.patient_id)

    session_directory = runtime.experiment_session_service.resolve_session_directory(session_id)

    assert workbench.dataset_directory == (session_directory / "eye_observations")

    raw_path = workbench.dataset_directory / "eye_0001_raw.png"
    record_path = workbench.dataset_directory / "eye_0001.json"

    raw_path.write_bytes(b"png")
    record_path.write_text(
        "{}\n",
        encoding="utf-8",
    )

    workbench.artifacts_saved.emit(
        (
            raw_path,
            record_path,
        )
    )

    artifact_paths = {
        artifact.relative_path
        for artifact in (runtime.experiment_session_service.list_artifacts(session_id))
    }

    assert artifact_paths == {
        "session.json",
        "eye_observations/eye_0001_raw.png",
        "eye_observations/eye_0001.json",
    }

    workbench.close()

    completed = runtime.experiment_session_service.get_session(session_id)

    assert completed.status is (ExperimentSessionStatus.COMPLETED)
    assert session_id not in (window._eye_windows)

    window.close()
    runtime.dispose()
