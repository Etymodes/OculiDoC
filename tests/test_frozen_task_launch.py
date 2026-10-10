from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from pytest import MonkeyPatch

import oculidoc
import oculidoc.__main__ as main_module
import oculidoc.api.__main__ as api_main_module
import oculidoc.app as app_module
import oculidoc.tasks.__main__ as task_main_module
from oculidoc.process_launch import (
    gaze_task_process_command,
    local_api_process_command,
)
from oculidoc.ui.main_window import _updater_command


def test_source_task_process_uses_python_module() -> None:
    program, arguments = gaze_task_process_command(
        "tracking",
        executable=Path("python.exe"),
        frozen=False,
    )
    assert program == "python.exe"
    assert arguments == ["-m", "oculidoc.tasks", "tracking"]


def test_frozen_task_process_routes_through_executable() -> None:
    program, arguments = gaze_task_process_command(
        "binary",
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )
    assert program == "OculiDoC.exe"
    assert arguments == ["--task", "binary"]


def test_typing_task_process_is_supported() -> None:
    program, arguments = gaze_task_process_command(
        "typing",
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )

    assert program == "OculiDoC.exe"
    assert arguments == ["--task", "typing"]


def test_vertical_binary_process_is_supported() -> None:
    program, arguments = gaze_task_process_command(
        "binary-vertical",
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )

    assert program == "OculiDoC.exe"
    assert arguments == ["--task", "binary-vertical"]


def test_multiple_choice_process_is_supported() -> None:
    program, arguments = gaze_task_process_command(
        "multiple-choice",
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )

    assert program == "OculiDoC.exe"
    assert arguments == ["--task", "multiple-choice"]


def test_image_choice_process_is_supported() -> None:
    program, arguments = gaze_task_process_command(
        "image-choice",
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )

    assert program == "OculiDoC.exe"
    assert arguments == ["--task", "image-choice"]


def test_instruction_fixation_process_is_supported() -> None:
    program, arguments = gaze_task_process_command(
        "instruction-fixation",
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )

    assert program == "OculiDoC.exe"
    assert arguments == ["--task", "instruction-fixation"]


def test_direct_task_process_includes_config_revision() -> None:
    program, arguments = gaze_task_process_command(
        "tracking",
        config_revision=7,
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )

    assert program == "OculiDoC.exe"
    assert arguments == ["--task", "tracking", "--direct", "--config-revision", "7"]


def test_source_api_process_uses_python_module() -> None:
    program, arguments = local_api_process_command(
        executable=Path("python.exe"),
        frozen=False,
    )

    assert program == "python.exe"
    assert arguments == ["-m", "oculidoc.api"]


def test_frozen_api_process_routes_through_executable() -> None:
    program, arguments = local_api_process_command(
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )

    assert program == "OculiDoC.exe"
    assert arguments == ["--api"]


def test_frozen_updater_uses_frozen_application(tmp_path: Path) -> None:
    assert _updater_command(tmp_path / "OculiDoC.exe", frozen=True, repository_root=None) == (
        str(tmp_path / "OculiDoC.exe"),
        ["--install-latest-release"],
    )


def test_source_updater_keeps_python_entry_point(tmp_path: Path) -> None:
    assert _updater_command(tmp_path / "python.exe", frozen=False, repository_root=tmp_path) == (
        str(tmp_path / "python.exe"),
        ["-m", "oculidoc.updater", "--repo", str(tmp_path)],
    )


def test_task_process_rejects_unknown_command() -> None:
    with pytest.raises(ValueError, match="Unsupported gaze task command"):
        gaze_task_process_command(
            "unknown",
            executable="OculiDoC.exe",
            frozen=True,
        )


def test_dispatch_runs_desktop_without_arguments(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        app_module,
        "run",
        lambda: calls.append("desktop") or 17,
    )
    assert main_module.dispatch([]) == 17
    assert calls == ["desktop"]


def test_dispatch_runs_local_api(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        api_main_module,
        "main",
        lambda: calls.append("api"),
    )

    assert main_module.dispatch(["--api"]) == 0
    assert calls == ["api"]


def test_dispatch_runs_thinks_worker(monkeypatch: MonkeyPatch) -> None:
    from oculidoc.vendor_helpers import thinks_tracker_worker

    monkeypatch.setattr(thinks_tracker_worker, "main", lambda: 29)
    assert main_module.dispatch(["--thinks-worker"]) == 29


def test_dispatch_stdio_smoke_reports_version(monkeypatch: MonkeyPatch) -> None:
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("OCULIDOC_STDIO_PROBE\n"))
    monkeypatch.setattr(sys, "stdout", output)

    assert main_module.dispatch(["--stdio-smoke"]) == 0
    assert json.loads(output.getvalue()) == {"status": "ok", "version": oculidoc.__version__}


def test_dispatch_forwards_frozen_task_arguments(
    monkeypatch: MonkeyPatch,
) -> None:
    received: list[list[str] | None] = []
    monkeypatch.setattr(
        task_main_module,
        "main",
        lambda argv=None: received.append(list(argv) if argv is not None else None) or 23,
    )
    assert main_module.dispatch(["--task", "tracking", "--direct", "--config-revision", "4"]) == 23
    assert received == [["tracking", "--direct", "--config-revision", "4"]]


def test_dispatch_rejects_invalid_arguments() -> None:
    with pytest.raises(SystemExit, match="Unsupported OculiDoC arguments"):
        main_module.dispatch(["--unknown"])
    with pytest.raises(SystemExit, match="requires a task command"):
        main_module.dispatch(["--task"])
