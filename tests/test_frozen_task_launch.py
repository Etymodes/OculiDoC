from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

import oculidoc.__main__ as main_module
import oculidoc.api.__main__ as api_main_module
import oculidoc.tasks.__main__ as task_main_module
from oculidoc.process_launch import (
    gaze_task_process_command,
    local_api_process_command,
)


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
        main_module,
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


def test_dispatch_forwards_frozen_task_arguments(
    monkeypatch: MonkeyPatch,
) -> None:
    received: list[list[str] | None] = []
    monkeypatch.setattr(
        task_main_module,
        "main",
        lambda argv=None: received.append(list(argv) if argv is not None else None) or 23,
    )
    assert main_module.dispatch(["--task", "tracking"]) == 23
    assert received == [["tracking"]]


def test_dispatch_rejects_invalid_arguments() -> None:
    with pytest.raises(SystemExit, match="Unsupported OculiDoC arguments"):
        main_module.dispatch(["--unknown"])
    with pytest.raises(SystemExit, match="requires exactly one"):
        main_module.dispatch(["--task"])
