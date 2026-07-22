"""Launch optional tools bundled with the hospital's legacy Tobii system."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _open_windows_executable(
    executable: str | Path,
    *,
    label: str,
    arguments: tuple[str, ...] = (),
) -> subprocess.Popen[bytes]:
    if os.name != "nt":
        raise RuntimeError(f"{label} requires Windows.")
    path = Path(executable).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} executable not found: {path}")
    return subprocess.Popen([str(path), *arguments], cwd=str(path.parent))


def open_eye_position(
    executable: str | Path = r"D:\EyePosition\TobiiDynavox.EyeAssist.Smorgasbord.exe",
) -> subprocess.Popen[bytes]:
    """Open the old EyeAssist track-status UI; this is not an acquisition source."""
    return _open_windows_executable(
        executable,
        label="EyePosition",
        arguments=("--showtrackstatus",),
    )


def open_gaze_collect_player(
    executable: str | Path = r"D:\GazeCollect\VMMachine\HPFMediaPlayer.exe",
) -> subprocess.Popen[bytes]:
    """Open HPF manually; the gaze adapter never starts or stops it implicitly."""
    return _open_windows_executable(executable, label="HPFMediaPlayer")


def open_tobii_calibration() -> subprocess.Popen[bytes]:
    """Open the installed Tobii Eye Tracking Portal through its registered app id."""
    if os.name != "nt":
        raise RuntimeError("Tobii calibration requires Windows.")
    return subprocess.Popen(
        ["explorer.exe", "shell:AppsFolder\\TobiiAB.TobiiEyeTrackingPortal_j9ea20k37yd2w!App"]
    )


def open_just_need_to_see(
    executable: str | Path = r"D:\JustNeedToSee\JustNeedToSee.exe",
) -> subprocess.Popen[bytes]:
    """Launch JustNeedToSee as an external AAC application, not a gaze source."""
    return _open_windows_executable(executable, label="JustNeedToSee")
