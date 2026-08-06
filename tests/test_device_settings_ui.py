from pathlib import Path
from types import SimpleNamespace

from pytestqt.qtbot import QtBot

import oculidoc.ui.device_settings as device_settings_module
from oculidoc.config import GazeDeviceConfig, GazeDeviceConfigStore, Settings
from oculidoc.ui.device_settings import (
    DeviceSettingsDialog,
    find_tobii_experience_app_id,
    find_tobii_experience_shortcut,
    launch_tobii_experience,
)


def test_device_settings_keeps_native_stream_and_only_generic_sources(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="tobii_stream_engine")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)

    items = [
        (dialog.source_combo.itemText(index), dialog.source_combo.itemData(index))
        for index in range(dialog.source_combo.count())
    ]

    assert items == [
        ("Tobii 原生 Stream（推荐）", "tobii_stream_engine"),
        ("七鑫易维 aSee（本机 SDK 桥）", "seveninvensun_bridge"),
        ("工程模拟测试", "mock"),
        ("原监听兼容", "tobii_hospital_bridge"),
        ("Tobii DLL兼容", "just_need_to_see_bundle"),
        ("第三方兼容", "tobii_legacy_bridge"),
    ]
    assert dialog.source_combo.currentData() == "tobii_stream_engine"


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


def test_device_settings_modes_enable_only_their_controls(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="mock")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)

    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("tobii_hospital_bridge"))
    assert not dialog.bridge_host_edit.isEnabled()
    assert dialog.bridge_port_spin.isEnabled()
    assert not dialog.third_party_json_edit.isEnabled()
    assert not dialog.compatibility_dll_edit.isEnabled()

    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("tobii_legacy_bridge"))
    assert dialog.bridge_host_edit.isEnabled()
    assert dialog.bridge_port_spin.isEnabled()
    assert dialog.third_party_json_edit.isEnabled()

    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("seveninvensun_bridge"))
    assert dialog.bridge_host_edit.isEnabled()
    assert dialog.bridge_port_spin.isEnabled()
    assert not dialog.third_party_json_edit.isEnabled()
    assert not dialog.opoin_thesis_button.isEnabled()
    assert not dialog.open_tobii_button.isEnabled()
    assert "七鑫易维" in dialog.calibration_tip.text()

    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("just_need_to_see_bundle"))
    assert dialog.compatibility_dll_edit.isEnabled()
    assert not dialog.bridge_host_edit.isEnabled()


def test_seveninvensun_source_rejects_nonlocal_bridge(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="mock")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)
    warnings: list[str] = []
    monkeypatch.setattr(
        device_settings_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("seveninvensun_bridge"))
    dialog.bridge_host_edit.setText("192.168.1.20")

    assert not dialog._validate_config(dialog.build_config())
    assert warnings == ["七鑫易维 SDK 桥只允许使用 localhost、127.0.0.1 或 ::1。"]


def test_legacy_file_source_is_presented_as_third_party_compatibility(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        gaze_source="gaze_collect_legacy",
    )
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)

    assert dialog.source_combo.currentData() == "tobii_legacy_bridge"
    assert dialog.source_combo.currentText() == "第三方兼容"


def test_third_party_and_dll_compatibility_build_existing_config(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    third_party_root = tmp_path / "third-party"
    third_party_root.mkdir()
    compatibility_dll = tmp_path / "compat" / "tobii_stream_engine.dll"
    compatibility_dll.parent.mkdir()
    compatibility_dll.write_bytes(b"test")
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="mock")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)

    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("tobii_legacy_bridge"))
    dialog.third_party_json_edit.setText(str(third_party_root))
    assert dialog.build_config().gaze_collect_json_root == third_party_root

    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("just_need_to_see_bundle"))
    dialog.compatibility_dll_edit.setText(str(compatibility_dll))
    config = dialog.build_config()
    assert config.just_need_to_see_root == compatibility_dll.parent
    assert dialog._validate_config(config)


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


def test_self_check_uses_current_unsaved_source(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="tobii_stream_engine")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)
    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("mock"))
    calls: list[Settings] = []

    class StubSelfCheck:
        def __init__(
            self,
            selected: Settings,
            parent: object,
            **kwargs: object,
        ) -> None:
            assert parent is dialog
            assert "preflight_store" in kwargs
            calls.append(selected)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(device_settings_module, "GazeSelfCheckDialog", StubSelfCheck)

    dialog._open_self_check()

    assert len(calls) == 1
    assert calls[0].gaze_source == "mock"


def test_opoin_thesis_is_a_separate_subjective_view(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="mock")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)
    calls: list[Settings] = []

    class StubOpoinThesis:
        def __init__(
            self,
            selected: Settings,
            parent: object,
            **kwargs: object,
        ) -> None:
            assert parent is dialog
            assert "preflight_result" in kwargs
            calls.append(selected)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(
        device_settings_module,
        "OpoinThesisDialog",
        StubOpoinThesis,
    )

    dialog._open_opoin_thesis()

    assert dialog.opoin_thesis_button.text() == "打开 OpoinThesis 眼位监测"
    assert len(calls) == 1
    assert calls[0].gaze_source == "mock"


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


def test_find_tobii_experience_registered_app_id(monkeypatch) -> None:
    monkeypatch.setattr(device_settings_module.sys, "platform", "win32")
    monkeypatch.setattr(
        device_settings_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="TobiiAB.TobiiExperience_xyz!App\n",
        ),
    )

    assert find_tobii_experience_app_id() == "TobiiAB.TobiiExperience_xyz!App"


def test_launch_tobii_experience_uses_registered_app_when_shortcut_is_missing(
    monkeypatch,
) -> None:
    targets: list[object] = []

    def open_target(target: object) -> bool:
        targets.append(target)
        return True

    monkeypatch.setattr(device_settings_module, "find_tobii_experience_shortcut", lambda: None)
    monkeypatch.setattr(
        device_settings_module,
        "find_tobii_experience_app_id",
        lambda: "TobiiAB.TobiiExperience_xyz!App",
    )
    monkeypatch.setattr(
        device_settings_module,
        "_open_windows_shell_target",
        open_target,
    )

    assert launch_tobii_experience()
    assert targets == [r"shell:AppsFolder\TobiiAB.TobiiExperience_xyz!App"]
