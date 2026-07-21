"""File-backed commands exchanged between the mobile API and desktop."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from oculidoc.lan_control import utc_now_text

REMOTE_GAZE_MODULE_IDS = frozenset(
    {
        "tracking_ball",
        "binary_horizontal",
        "binary_vertical",
        "screen_keyboard",
        "multiple_choice",
    }
)


class LanCommandType(StrEnum):
    OPEN_PATIENT_DISPLAY = "open_patient_display"
    START_TASK = "start_task"
    STOP_TASK = "stop_task"
    REPLAY_SPEECH = "replay_speech"


class LanCommandStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        return self in {
            LanCommandStatus.COMPLETED,
            LanCommandStatus.REJECTED,
        }


class LanCommandRejected(RuntimeError):
    """Expected rejection caused by desktop state or safety validation."""


@dataclass(frozen=True, slots=True)
class LanCommand:
    command_id: str
    command_type: LanCommandType
    payload: dict[str, object]
    status: LanCommandStatus
    message: str
    created_at_utc: str
    updated_at_utc: str

    @classmethod
    def from_dict(cls, value: object) -> LanCommand:
        if not isinstance(value, dict):
            raise TypeError("LAN command must be an object.")

        payload = value.get("payload", {})

        if not isinstance(payload, dict):
            raise TypeError("LAN command payload must be an object.")

        return cls(
            command_id=str(value["command_id"]),
            command_type=LanCommandType(str(value["command_type"])),
            payload={str(key): item for key, item in payload.items()},
            status=LanCommandStatus(str(value["status"])),
            message=str(value.get("message", "")),
            created_at_utc=str(value["created_at_utc"]),
            updated_at_utc=str(value["updated_at_utc"]),
        )

    @property
    def module_id(self) -> str | None:
        value = self.payload.get("module_id")

        if value is None:
            return None

        normalized = str(value).strip()
        return normalized or None

    @property
    def config_revision(self) -> int | None:
        value = self.payload.get("config_revision")

        if value is None:
            return None

        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("config_revision must be an integer.")

        revision = value

        if revision < 0:
            raise ValueError("config_revision cannot be negative.")

        return revision

    def to_dict(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "command_type": self.command_type.value,
            "payload": dict(self.payload),
            "status": self.status.value,
            "message": self.message,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
        }


class LanCommandStore:
    """Persist each command in its own atomically replaced JSON file."""

    schema_version = "1.0"

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()

    def submit(
        self,
        command_type: LanCommandType | str,
        *,
        payload: dict[str, object] | None = None,
    ) -> LanCommand:
        now = utc_now_text()
        command = LanCommand(
            command_id=uuid4().hex,
            command_type=LanCommandType(command_type),
            payload=dict(payload or {}),
            status=LanCommandStatus.PENDING,
            message="等待桌面管理员端接收。",
            created_at_utc=now,
            updated_at_utc=now,
        )
        self._write(command)
        return command

    def load(self, command_id: str) -> LanCommand:
        path = self._command_path(command_id)

        if not path.is_file():
            raise FileNotFoundError(f"Unknown LAN command: {command_id}")

        payload = json.loads(path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict):
            raise ValueError("LAN command root must be an object.")

        if payload.get("schema_version") != self.schema_version:
            raise ValueError("Unsupported LAN command schema.")

        return LanCommand.from_dict(payload.get("command"))

    def list_commands(self, *, limit: int = 20) -> tuple[LanCommand, ...]:
        normalized_limit = max(1, min(100, int(limit)))

        if not self.directory.is_dir():
            return ()

        commands: list[LanCommand] = []

        for path in self.directory.glob("*.json"):
            try:
                command = self.load(path.stem)
            except (
                OSError,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                continue

            commands.append(command)

        commands.sort(
            key=lambda item: (item.created_at_utc, item.command_id),
            reverse=True,
        )
        return tuple(commands[:normalized_limit])

    def pending(self) -> tuple[LanCommand, ...]:
        commands = [
            command
            for command in self.list_commands(limit=100)
            if command.status is LanCommandStatus.PENDING
        ]
        commands.sort(key=lambda item: (item.created_at_utc, item.command_id))
        return tuple(commands)

    def transition(
        self,
        command_id: str,
        status: LanCommandStatus | str,
        message: str,
    ) -> LanCommand:
        previous = self.load(command_id)
        next_status = LanCommandStatus(status)
        allowed = {
            LanCommandStatus.PENDING: {
                LanCommandStatus.ACCEPTED,
                LanCommandStatus.REJECTED,
            },
            LanCommandStatus.ACCEPTED: {
                LanCommandStatus.COMPLETED,
                LanCommandStatus.REJECTED,
            },
            LanCommandStatus.COMPLETED: set(),
            LanCommandStatus.REJECTED: set(),
        }

        if next_status not in allowed[previous.status]:
            raise ValueError(
                f"Invalid LAN command transition: {previous.status.value} -> {next_status.value}"
            )

        command = LanCommand(
            command_id=previous.command_id,
            command_type=previous.command_type,
            payload=dict(previous.payload),
            status=next_status,
            message=message.strip(),
            created_at_utc=previous.created_at_utc,
            updated_at_utc=utc_now_text(),
        )
        self._write(command)
        return command

    def _command_path(self, command_id: str) -> Path:
        normalized = command_id.strip()

        if not normalized or len(normalized) > 64 or not normalized.isalnum():
            raise ValueError("Invalid LAN command identifier.")

        return self.directory / (normalized + ".json")

    def _write(self, command: LanCommand) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._command_path(command.command_id)
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "command": command.to_dict(),
        }

        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=self.directory,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        try:
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
