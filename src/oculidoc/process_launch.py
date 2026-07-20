from __future__ import annotations

import sys
from pathlib import Path

_TASK_COMMANDS = frozenset({"tracking", "binary"})


def is_frozen_application() -> bool:
    """Return whether Python is running from a frozen executable."""
    return bool(getattr(sys, "frozen", False))


def gaze_task_process_command(
    command: str,
    *,
    executable: str | Path | None = None,
    frozen: bool | None = None,
) -> tuple[str, list[str]]:
    """Build the child process command for one gaze task."""
    normalized_command = command.strip()
    if normalized_command not in _TASK_COMMANDS:
        raise ValueError(f"Unsupported gaze task command: {command}")

    program = str(executable if executable is not None else sys.executable).strip()
    if not program:
        raise ValueError("Task process executable cannot be empty.")

    frozen_mode = is_frozen_application() if frozen is None else bool(frozen)
    arguments = (
        ["--task", normalized_command]
        if frozen_mode
        else ["-m", "oculidoc.tasks", normalized_command]
    )
    return program, arguments


def local_api_process_command(
    *,
    executable: str | Path | None = None,
    frozen: bool | None = None,
) -> tuple[str, list[str]]:
    """Build the child process command for the local FastAPI backend."""
    program = str(executable if executable is not None else sys.executable).strip()

    if not program:
        raise ValueError("API process executable cannot be empty.")

    frozen_mode = is_frozen_application() if frozen is None else bool(frozen)
    arguments = ["--api"] if frozen_mode else ["-m", "oculidoc.api"]
    return program, arguments
