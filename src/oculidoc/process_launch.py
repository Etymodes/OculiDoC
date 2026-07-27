from __future__ import annotations

import sys
from pathlib import Path

_TASK_COMMANDS = frozenset(
    {
        "tracking",
        "binary",
        "binary-vertical",
        "typing",
        "multiple-choice",
        "image-choice",
        "instruction-fixation",
        "gaze-games",
        "visual-preference",
    }
)
_GAZE_GAME_MODES = frozenset({"garden", "treasure_hunt"})


def is_frozen_application() -> bool:
    """Return whether Python is running from a frozen executable."""
    return bool(getattr(sys, "frozen", False))


def gaze_task_process_command(
    command: str,
    *,
    config_revision: int | None = None,
    game_mode: str | None = None,
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

    if config_revision is not None:
        if config_revision < 0:
            raise ValueError("config_revision cannot be negative.")

        arguments.extend(["--direct", "--config-revision", str(config_revision)])

    normalized_game_mode = game_mode.strip() if game_mode is not None else None

    if normalized_game_mode is not None:
        if normalized_command != "gaze-games":
            raise ValueError("game_mode is only valid for the gaze-games command.")

        if normalized_game_mode not in _GAZE_GAME_MODES:
            raise ValueError(f"Unsupported gaze game mode: {game_mode}")

        arguments.extend(["--game-mode", normalized_game_mode])
    elif normalized_command == "gaze-games" and config_revision is not None:
        raise ValueError("Direct gaze-games launch requires game_mode.")

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
