"""First-run camera configuration and optional ACIES installer discovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.webcam_gaze import read_webcam_config


def ensure_webcam_config(
    path: Path, camera_index: int | None = None, *, legacy_path: Path | None = None
) -> dict[str, Any]:
    """Create a missing config, preserving and validating every existing setting.

    An empty providers object records the camera choice without pretending that
    the optional vision runtime has been installed. Malformed files are never
    replaced by a default configuration.
    """
    if camera_index is not None and (type(camera_index) is not int or not 0 <= camera_index <= 16):
        raise ValueError("摄像头编号必须在 0 至 16 之间。")
    exists = path.exists()
    if exists:
        config = read_webcam_config(path)
    elif legacy_path is not None and legacy_path != path and legacy_path.exists():
        config = read_webcam_config(legacy_path)
    else:
        config = {"camera_index": 0, "camera_width": 1280, "camera_height": 720, "providers": {}}
    changed = not exists
    if camera_index is not None and config.get("camera_index", 0) != camera_index:
        config["camera_index"] = camera_index
        changed = True
    if not changed:
        return config
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return config


def acies_runtime_configured(path: Path) -> bool:
    """Check actual local files; importing optional native modules belongs in the worker."""
    try:
        provider = read_webcam_config(path)["providers"].get("acies")
        if not isinstance(provider, dict):
            return False
        for key in ("python", "face_model"):
            value = provider.get(key)
            if not isinstance(value, str) or not value:
                return False
            local_path = Path(value)
            if not local_path.is_absolute() or not local_path.is_file():
                return False
        return True
    except DeviceConnectionError:
        return False


def acies_installer_command(data_dir: Path, config_path: Path, camera_index: int) -> list[str]:
    """Resolve both a source checkout and packaged application without workstation paths."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        bundle_root = Path(frozen_root)
        script = bundle_root / "scripts" / "install_webcam_acies.ps1"
        source_dir = bundle_root / "oculidoc" / "devices"
    else:
        source_dir = Path(__file__).resolve().parent
        script = source_dir.parents[2] / "scripts" / "install_webcam_acies.ps1"
    if not script.is_file() or not (source_dir / "acies.py").is_file():
        raise DeviceConnectionError("此版本缺少 ACIES 安装资源，请更新完整 OculiDoC 安装包。")
    command = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-DataDir",
        str(data_dir.resolve()),
        "-ConfigPath",
        str(config_path.resolve()),
        "-RuntimeRoot",
        str((data_dir / "runtime" / "webcam" / "acies").resolve()),
        "-AciesSourceDir",
        str(source_dir),
        "-CameraIndex",
        str(camera_index),
    ]
    if frozen_root is None:
        command.extend(("-PythonCommand", sys.executable))
    return command
