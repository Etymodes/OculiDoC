from pathlib import Path

from pytestqt.qtbot import QtBot

import oculidoc.ui.device_settings as device_settings_module
from oculidoc.config import GazeDeviceConfig, GazeDeviceConfigStore, Settings
from oculidoc.ui.device_settings import (
    DeviceSettingsDialog,
    find_tobii_experience_shortcut,
    find_tobii_ghost_shortcut,
)


def test_device_settings_dialog_saves_native_source(qtbot: QtBot, tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="mock")
    store = GazeDeviceConfigStore.for_settings(settings)
    dll_path = tmp_path / "tobii_stream_engine.dll"
    dll_path.write_bytes(b"test")
    dialog = DeviceSettingsDialog(settings, store)
    qtbot.addWidget(dialog)
    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("tobii_stream_engine"))
    dialog.dll_path_edit.setText(str(dll_path))
    dialog.preflight_seconds_spin.setValue(8)
    dialog.minimum_validity_spin.setValue(70)

    dialog._save()

    saved = store.load(GazeDeviceConfig.from_settings(settings))
    assert saved.gaze_source == "tobii_stream_engine"
    assert saved.tobii_stream_engine_dll == dll_path
    assert saved.gaze_preflight_seconds == 8
    assert saved.gaze_minimum_valid_ratio == 0.70


def test_device_settings_dialog_marks_mock_as_engineering_mode(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="mock")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)

    assert "仅工程测试" in dialog.source_combo.currentText()
    assert dialog.dll_path_edit.isEnabled() is False


def test_device_settings_dialog_wires_stream_engine_discovery(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="mock")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)
    dll_path = tmp_path / "tobii_stream_engine.dll"
    dll_path.write_bytes(b"test")
    monkeypatch.setattr(
        device_settings_module,
        "discover_tobii_stream_engine_dll",
        lambda explicit=None: dll_path,
    )

    dialog._discover_dll()

    assert dialog.dll_path_edit.text() == str(dll_path)


def test_find_tobii_experience_start_menu_shortcut(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_data = tmp_path / "AppData" / "Roaming"
    shortcut = (
        app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Tobii Experience.lnk"
    )
    shortcut.parent.mkdir(parents=True)
    shortcut.write_bytes(b"test")
    monkeypatch.setenv("APPDATA", str(app_data))
    monkeypatch.delenv("ProgramData", raising=False)

    assert find_tobii_experience_shortcut() == shortcut


def test_find_tobii_ghost_start_menu_shortcut(tmp_path: Path, monkeypatch) -> None:
    program_data = tmp_path / "ProgramData"
    shortcut = (
        program_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Tobii Ghost.lnk"
    )
    shortcut.parent.mkdir(parents=True)
    shortcut.write_bytes(b"test")
    monkeypatch.setenv("ProgramData", str(program_data))
    monkeypatch.delenv("APPDATA", raising=False)

    assert find_tobii_ghost_shortcut() == shortcut
