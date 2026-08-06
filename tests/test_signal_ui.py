"""Qt smoke coverage for the neural-signal interaction surface."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QPushButton
from pytestqt.qtbot import QtBot

from oculidoc.config import Settings
from oculidoc.modules.registry import ALL_MODULES
from oculidoc.signal_tasks.config import SignalTaskConfig, SignalTaskKind
from oculidoc.signal_tasks.window import SignalTaskWindow
from oculidoc.signals.models import SignalParadigm, SignalSourceKind
from oculidoc.signals.profile import PatientSignalProfile
from oculidoc.ui.main_window import AdminMainWindow
from oculidoc.ui.signal_workbench import SignalParadigmSelector, SignalWorkbenchDialog


def test_homepage_selector_supports_multi_select_and_reserves_p300(qtbot: QtBot) -> None:
    selector = SignalParadigmSelector()
    qtbot.addWidget(selector)
    selector.set_patient_available(True)
    selector.set_selected((SignalParadigm.GAZE, SignalParadigm.SSVEP, SignalParadigm.MI))
    assert selector.selected_paradigms() == (
        SignalParadigm.GAZE,
        SignalParadigm.SSVEP,
        SignalParadigm.MI,
    )
    p300 = selector.findChild(QCheckBox, "signalParadigm_p300")
    assert p300 is not None
    assert not p300.isEnabled()


def test_signal_dialog_exposes_simulation_only_for_beta00(qtbot: QtBot) -> None:
    profile = PatientSignalProfile(patient_id="patient-a")
    beta = SignalWorkbenchDialog(
        profile,
        patient_code="Beta00",
        selected_paradigms=(SignalParadigm.SSVEP,),
    )
    qtbot.addWidget(beta)
    real = SignalWorkbenchDialog(
        profile,
        patient_code="REAL-001",
        selected_paradigms=(SignalParadigm.SSVEP,),
    )
    qtbot.addWidget(real)
    assert beta.source_combo.itemData(0) == "simulation"
    assert all(
        real.source_combo.itemData(index) != "simulation"
        for index in range(real.source_combo.count())
    )


def test_admin_homepage_registers_neural_module_without_breaking_gaze(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = AdminMainWindow(Settings(environment="test", data_dir=tmp_path))
    qtbot.addWidget(window)
    identifiers = {module.module_id for module in ALL_MODULES}
    assert "neural_interaction" in identifiers
    assert window.findChild(QPushButton, "moduleButton_tracking_ball") is not None
    assert window.findChild(QPushButton, "openSignalWorkbenchButton") is not None


def test_signal_stimulus_window_completes_off_main_thread(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    config = SignalTaskConfig(
        SignalTaskKind.SSVEP_SINGLE_TARGET,
        SignalSourceKind.SIMULATION,
        duration_seconds=0.5,
        frequencies_hz=(10.0,),
        trial_count=1,
    )
    window = SignalTaskWindow(config, tmp_path)
    qtbot.addWidget(window)
    with qtbot.waitSignal(window.completed, timeout=4_000) as completed:
        window.show()
    assert completed.args[0] == 0
    assert Path(completed.args[2]).name == "task_result.json"
