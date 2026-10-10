from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from pytestqt.qtbot import QtBot

import oculidoc.config as config_module
import oculidoc.devices.webcam_setup as webcam_setup_module
import oculidoc.ui.acies_setup as setup_ui
from oculidoc.config import GazeDeviceConfigStore, Settings
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.webcam_gaze import calibration_identity, read_webcam_config
from oculidoc.devices.webcam_setup import (
    acies_installer_command,
    acies_runtime_configured,
    ensure_webcam_config,
)
from oculidoc.ui.acies_setup import AciesSetupDialog
from oculidoc.ui.device_settings import DeviceSettingsDialog


@pytest.fixture(autouse=True)
def isolated_application_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, "application_root", lambda: tmp_path / "application")


def test_default_config_is_in_writable_data_directory(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path / "user-data")

    assert settings.webcam_providers_path == tmp_path / "user-data/runtime/webcam_providers.json"


def test_legacy_config_discovery_is_read_only_then_gui_migrates_identical_contents(
    qtbot: QtBot, tmp_path: Path
) -> None:
    legacy = tmp_path / "application/data/runtime/webcam_providers.json"
    legacy.parent.mkdir(parents=True)
    config = {
        "camera_index": 2,
        "providers": {"acies": {"python": "C:/old/python.exe", "face_model": "C:/old/model.task"}},
        "custom": {"preserve": True},
    }
    legacy.write_text(json.dumps(config), encoding="utf-8")
    original_bytes = legacy.read_bytes()
    data_dir = tmp_path / "user-data"
    settings = Settings(environment="test", data_dir=data_dir, gaze_source="webcam")
    assert settings.webcam_providers_path == legacy
    assert not data_dir.exists()
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)
    assert dialog.camera_combo.currentData() == 2

    dialog._save()

    destination = data_dir / "runtime/webcam_providers.json"
    assert settings.webcam_providers_path == destination
    assert read_webcam_config(destination) == config
    assert legacy.read_bytes() == original_bytes
    assert calibration_identity(config, "acies", "patient", ("screen", 1920, 1080)) == (
        calibration_identity(
            read_webcam_config(destination), "acies", "patient", ("screen", 1920, 1080)
        )
    )
    assert not acies_runtime_configured(destination)


def test_explicit_config_does_not_copy_legacy_installation(qtbot: QtBot, tmp_path: Path) -> None:
    legacy = tmp_path / "application/data/runtime/webcam_providers.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"providers":{"acies":{"python":"old"}},"camera_index":2}')
    explicit = tmp_path / "custom/providers.json"
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "user-data",
        gaze_source="webcam",
        webcam_config_path=explicit,
    )
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)

    dialog._save()

    assert read_webcam_config(explicit)["providers"] == {}
    assert settings.webcam_providers_path == explicit


def test_invalid_legacy_config_is_preserved_without_creating_new_config(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"providers":[]}', encoding="utf-8")
    destination = tmp_path / "new/providers.json"

    with pytest.raises(DeviceConnectionError):
        ensure_webcam_config(destination, 2, legacy_path=legacy)

    assert not destination.exists()
    assert legacy.read_text(encoding="utf-8") == '{"providers":[]}'


def test_first_run_camera_selection_creates_honest_config(qtbot: QtBot, tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        gaze_source="webcam",
        webcam_config_path=tmp_path / "runtime" / "webcam_providers.json",
    )
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)
    dialog.camera_combo.addItem("摄像头 2", 2)
    dialog.camera_combo.setCurrentIndex(dialog.camera_combo.findData(2))

    dialog._save()

    config = json.loads(settings.webcam_providers_path.read_text(encoding="utf-8"))
    assert config["camera_index"] == 2
    assert config["camera_width"] == 1280
    assert config["providers"] == {}
    assert not acies_runtime_configured(settings.webcam_providers_path)


def test_default_camera_also_saves_first_run_config(qtbot: QtBot, tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        gaze_source="webcam",
        webcam_config_path=tmp_path / "runtime" / "webcam_providers.json",
    )
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)

    assert dialog._persist_webcam_camera_selection()

    assert ensure_webcam_config(settings.webcam_providers_path)["camera_index"] == 0


def test_invalid_existing_config_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "webcam.json"
    original = '{"providers": [], "camera_index": 2}'
    path.write_text(original, encoding="utf-8")

    with pytest.raises(DeviceConnectionError):
        ensure_webcam_config(path, 1)

    assert path.read_text(encoding="utf-8") == original


def test_camera_setup_preserves_every_provider_and_custom_setting(tmp_path: Path) -> None:
    path = tmp_path / "webcam.json"
    provider = {"python": "C:/missing/python.exe", "face_model": "C:/missing/model.task"}
    path.write_text(
        json.dumps({"providers": {"other": provider}, "extra": {"value": 3}}),
        encoding="utf-8",
    )

    config = ensure_webcam_config(path, 2)

    assert config["providers"] == {"other": provider}
    assert config["extra"] == {"value": 3}
    assert config["camera_index"] == 2


def test_copied_machine_paths_are_not_considered_installed(tmp_path: Path) -> None:
    path = tmp_path / "webcam.json"
    path.write_text(
        json.dumps({"providers": {"acies": {"python": "missing", "face_model": "missing"}}}),
        encoding="utf-8",
    )

    assert not acies_runtime_configured(path)


def test_frozen_installer_uses_packaged_resources_and_local_data(
    tmp_path: Path, monkeypatch
) -> None:
    bundle = tmp_path / "bundle"
    script = bundle / "scripts" / "install_webcam_acies.ps1"
    source = bundle / "oculidoc" / "devices" / "acies.py"
    for resource in (script, source):
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_text("", encoding="utf-8")
    monkeypatch.setattr(webcam_setup_module.sys, "_MEIPASS", str(bundle), raising=False)
    data = tmp_path / "本机数据"

    command = acies_installer_command(data, data / "providers.json", 2)

    assert str(script) in command
    assert str(source.parent) in command
    assert str(data / "runtime" / "webcam" / "acies") in command
    assert "-PythonCommand" not in command


def test_setup_process_keeps_event_loop_responsive_and_checks_real_files(
    qtbot: QtBot, tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "runtime" / "webcam_providers.json"
    model = tmp_path / "model.task"
    model.write_bytes(b"test model")
    config = {"providers": {"acies": {"python": sys.executable, "face_model": str(model)}}}
    code = (
        "import pathlib,time; print('install progress',flush=True); time.sleep(.4); "
        f"pathlib.Path({str(config_path)!r}).write_text({json.dumps(config)!r},encoding='utf-8')"
    )
    monkeypatch.setattr(
        setup_ui, "acies_installer_command", lambda *_: [sys.executable, "-c", code]
    )
    dialog = AciesSetupDialog(tmp_path, config_path, 2)
    qtbot.addWidget(dialog)
    ticks: list[bool] = []
    timer = QTimer(dialog)
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(dialog._running))
    timer.start()

    dialog._start()

    assert dialog._running
    assert not dialog.start_button.isEnabled()
    qtbot.waitUntil(lambda: not dialog._running, timeout=10000)
    assert any(ticks)
    assert dialog.succeeded
    assert "通过依赖与模型初始化检查" in dialog.status_label.text()
    assert "install progress" in dialog.output.toPlainText()
    assert list((tmp_path / "runtime" / "webcam").glob("setup-*.log"))


@pytest.mark.parametrize("exit_code", [0, 3])
def test_setup_failure_does_not_claim_optional_provider_is_installed(
    qtbot: QtBot, tmp_path: Path, monkeypatch, exit_code: int
) -> None:
    config_path = tmp_path / "runtime" / "providers.json"
    monkeypatch.setattr(
        setup_ui,
        "acies_installer_command",
        lambda *_: [sys.executable, "-c", f"import sys; sys.exit({exit_code})"],
    )
    dialog = AciesSetupDialog(tmp_path, config_path, 2)
    qtbot.addWidget(dialog)

    dialog._start()
    qtbot.waitUntil(lambda: not dialog._running, timeout=10000)

    assert not dialog.succeeded
    assert dialog.start_button.isEnabled()
    assert dialog.close_button.isEnabled()
    assert "安装未完成" in dialog.status_label.text()
    assert json.loads(config_path.read_text(encoding="utf-8"))["providers"] == {}


def test_setup_start_failure_restores_controls(qtbot: QtBot, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        setup_ui, "acies_installer_command", lambda *_: ["missing-oculidoc-program-123"]
    )
    dialog = AciesSetupDialog(tmp_path, tmp_path / "providers.json", 2)
    qtbot.addWidget(dialog)

    dialog._start()
    qtbot.waitUntil(lambda: not dialog._running)

    assert not dialog.succeeded
    assert dialog.start_button.isEnabled()
    assert "无法启动安装程序" in dialog.status_label.text()


def _installer_text() -> str:
    path = Path(__file__).parents[1] / "scripts" / "install_webcam_acies.ps1"
    return path.read_text(encoding="utf-8-sig")


def _powershell_result(code: str, *, environment: dict[str, str] | None = None) -> object:
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=environment,
    )
    return json.loads(completed.stdout)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer probe")
def test_installer_invalid_executable_returns_false_and_bad_models_are_not_reused(
    tmp_path: Path,
) -> None:
    invalid_python = tmp_path / "invalid.exe"
    invalid_python.write_bytes(b"not a Windows executable")
    bad_model = tmp_path / "model.task"
    bad_model.write_bytes(b"not an official Face Landmarker model")
    text = _installer_text()
    functions = (
        "function Test-AciesRuntime"
        + text.split("function Test-AciesRuntime", 1)[1].split("$providerPython = ''", 1)[0]
    )
    python_literal = "'" + str(invalid_python).replace("'", "''") + "'"
    model_literal = "'" + str(bad_model).replace("'", "''") + "'"
    result = _powershell_result(
        "$ErrorActionPreference='Stop'; "
        + functions
        + f"; $runtime = Test-AciesRuntime {python_literal} {model_literal}; "
        + f"$model = Test-OfficialFaceModel {model_literal}; "
        + "@{runtime=$runtime;model=$model}|ConvertTo-Json -Compress"
    )

    assert result == {"runtime": False, "model": False}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer identity preservation")
@pytest.mark.parametrize("existing_basis", [False, True])
def test_installer_snapshots_old_providers_without_mutating_existing_identity(
    existing_basis: bool,
) -> None:
    text = _installer_text()
    snapshot = text.split("if ($config.providers.ContainsKey('acies')", 1)[1].split(
        "$config.providers['acies'] =", 1
    )[0]
    snapshot = "if ($config.providers.ContainsKey('acies')" + snapshot
    code = (
        "$config=@{providers=@{acies=@{python='old'};other=@{python='other'}}}; "
        + (
            "$config['calibration_identity_providers']=@{acies=@{python='original'}}; "
            if existing_basis
            else ""
        )
        + snapshot
        + "; $config.providers['acies']=@{python='new'}; $config|ConvertTo-Json -Depth 8 -Compress"
    )
    result = _powershell_result(code)

    assert isinstance(result, dict)
    assert result["providers"]["acies"]["python"] == "new"
    basis = result["calibration_identity_providers"]
    assert basis["acies"]["python"] == ("original" if existing_basis else "old")
    if not existing_basis:
        assert basis["other"]["python"] == "other"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer identity preservation")
def test_first_install_does_not_add_identity_provider_snapshot() -> None:
    text = _installer_text()
    snapshot = text.split("if ($config.providers.ContainsKey('acies')", 1)[1].split(
        "$config.providers['acies'] =", 1
    )[0]
    snapshot = "if ($config.providers.ContainsKey('acies')" + snapshot

    result = _powershell_result(
        "$config=@{providers=@{}}; " + snapshot + "; $config|ConvertTo-Json -Depth 8 -Compress"
    )

    assert result == {"providers": {}}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer config preservation")
@pytest.mark.parametrize(
    ("previous_camera", "selected_camera", "expected_camera"),
    [(None, 0, None), (None, 2, 2), (1, 1, 1), (1, 2, 2)],
)
def test_installer_preserves_omitted_camera_defaults_but_applies_camera_changes(
    tmp_path: Path, previous_camera: int | None, selected_camera: int, expected_camera: int | None
) -> None:
    config: dict[str, object] = {"providers": {"acies": {"python": "old"}}}
    if previous_camera is not None:
        config["camera_index"] = previous_camera
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    text = _installer_text()
    fragment = text.split("# Validate an existing configuration", 1)[1].split(
        "New-Item -ItemType Directory -Path $RuntimeRoot", 1
    )[0]
    fragment = "# Validate an existing configuration" + fragment
    path_literal = "'" + str(path).replace("'", "''") + "'"

    result = _powershell_result(
        f"$ErrorActionPreference='Stop'; $ConfigPath={path_literal}; "
        f"$CameraIndex={selected_camera}; "
        + fragment
        + "; $config|ConvertTo-Json -Depth 8 -Compress"
    )

    expected = dict(config)
    if expected_camera is not None:
        expected["camera_index"] = expected_camera
    assert result == expected
    assert isinstance(result, dict)
    assert "camera_width" not in result and "camera_height" not in result


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell module isolation")
def test_installer_uses_its_own_modules_when_parent_passes_core_module_paths(
    tmp_path: Path,
) -> None:
    # A shadow Utility module models the incompatible PowerShell 7 module path.
    shadow = tmp_path / "parent-modules" / "Microsoft.PowerShell.Utility"
    shadow.mkdir(parents=True)
    (shadow / "Microsoft.PowerShell.Utility.psm1").write_text(
        "function Get-FileHash { throw 'incompatible parent module' }; "
        "Export-ModuleMember -Function Get-FileHash",
        encoding="utf-8",
    )
    file = tmp_path / "payload.bin"
    file.write_bytes(b"official model hash check smoke")
    text = _installer_text()
    initialization = text.split("# Python/Qt launched", 1)[1].split("$ProgressPreference", 1)[0]
    initialization = "# Python/Qt launched" + initialization
    path_literal = "'" + str(file).replace("'", "''") + "'"

    result = _powershell_result(
        initialization
        + f"; @{{hash=(Get-FileHash -LiteralPath {path_literal} -Algorithm SHA256).Hash; "
        "proxy=$env:OCULIDOC_SMOKE_PROXY}|ConvertTo-Json -Compress",
        environment={
            **os.environ,
            "PSModulePath": str(shadow.parent),
            "OCULIDOC_SMOKE_PROXY": "preserved",
        },
    )

    import hashlib

    assert result == {
        "hash": hashlib.sha256(file.read_bytes()).hexdigest().upper(),
        "proxy": "preserved",
    }
