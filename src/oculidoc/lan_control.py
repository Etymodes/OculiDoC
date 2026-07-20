"""Local-network pairing and patient-display control state."""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode

DEFAULT_IDLE_TEXT = "患者显示端已就绪\n等待管理员启动实验"
DEFAULT_CLOSED_TEXT = "患者显示端已关闭"


class PatientDisplayMode(StrEnum):
    """Shared patient-display states used by desktop, mobile, and display UI."""

    CLOSED = "closed"
    IDLE = "idle"
    READY = "ready"
    PREVIEW = "preview"
    RUNNING = "running"
    PAUSED = "paused"
    RESULT = "result"
    ERROR = "error"


class LanControlTransitionError(ValueError):
    """Raised when a patient-display state transition is not allowed."""


_ALLOWED_TRANSITIONS = {
    PatientDisplayMode.CLOSED: {
        PatientDisplayMode.CLOSED,
        PatientDisplayMode.IDLE,
    },
    PatientDisplayMode.IDLE: {
        PatientDisplayMode.CLOSED,
        PatientDisplayMode.IDLE,
        PatientDisplayMode.READY,
        PatientDisplayMode.PREVIEW,
        PatientDisplayMode.ERROR,
    },
    PatientDisplayMode.READY: {
        PatientDisplayMode.CLOSED,
        PatientDisplayMode.IDLE,
        PatientDisplayMode.READY,
        PatientDisplayMode.RUNNING,
        PatientDisplayMode.ERROR,
    },
    PatientDisplayMode.PREVIEW: {
        PatientDisplayMode.CLOSED,
        PatientDisplayMode.IDLE,
        PatientDisplayMode.READY,
        PatientDisplayMode.PREVIEW,
        PatientDisplayMode.ERROR,
    },
    PatientDisplayMode.RUNNING: {
        PatientDisplayMode.CLOSED,
        PatientDisplayMode.IDLE,
        PatientDisplayMode.RUNNING,
        PatientDisplayMode.PAUSED,
        PatientDisplayMode.RESULT,
        PatientDisplayMode.ERROR,
    },
    PatientDisplayMode.PAUSED: {
        PatientDisplayMode.CLOSED,
        PatientDisplayMode.IDLE,
        PatientDisplayMode.RUNNING,
        PatientDisplayMode.PAUSED,
        PatientDisplayMode.RESULT,
        PatientDisplayMode.ERROR,
    },
    PatientDisplayMode.RESULT: {
        PatientDisplayMode.CLOSED,
        PatientDisplayMode.IDLE,
        PatientDisplayMode.READY,
        PatientDisplayMode.PREVIEW,
        PatientDisplayMode.RESULT,
        PatientDisplayMode.ERROR,
    },
    PatientDisplayMode.ERROR: {
        PatientDisplayMode.CLOSED,
        PatientDisplayMode.IDLE,
        PatientDisplayMode.READY,
        PatientDisplayMode.PREVIEW,
        PatientDisplayMode.ERROR,
    },
}


def _patient_display_mode(value: object) -> PatientDisplayMode:
    normalized = str(value or PatientDisplayMode.IDLE.value).strip().lower()

    if normalized == "message":
        normalized = PatientDisplayMode.PREVIEW.value

    return PatientDisplayMode(normalized)


def utc_now_text() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class LanControlState:
    """Small file-backed state shared by the API and desktop process."""

    revision: int
    mode: PatientDisplayMode
    text: str
    task_id: str | None
    countdown_seconds: int | None
    updated_at_utc: str

    @classmethod
    def idle(cls) -> LanControlState:
        return cls(
            revision=0,
            mode=PatientDisplayMode.IDLE,
            text=DEFAULT_IDLE_TEXT,
            task_id=None,
            countdown_seconds=None,
            updated_at_utc=utc_now_text(),
        )

    @classmethod
    def from_dict(cls, value: object) -> LanControlState:
        if not isinstance(value, dict):
            raise TypeError("LAN control state must be an object.")

        countdown_value = value.get("countdown_seconds")
        countdown_seconds = int(countdown_value) if countdown_value is not None else None

        if countdown_seconds is not None and countdown_seconds < 0:
            raise ValueError("Patient-display countdown cannot be negative.")

        return cls(
            revision=max(0, int(value.get("revision", 0))),
            mode=_patient_display_mode(value.get("mode")),
            text=str(value.get("text", DEFAULT_IDLE_TEXT)),
            task_id=(str(value["task_id"]) if value.get("task_id") is not None else None),
            countdown_seconds=countdown_seconds,
            updated_at_utc=str(value.get("updated_at_utc", utc_now_text())),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "mode": self.mode.value,
            "text": self.text,
            "task_id": self.task_id,
            "countdown_seconds": self.countdown_seconds,
            "updated_at_utc": self.updated_at_utc,
        }


class LanControlStateStore:
    """Atomically persist the latest mobile-control display state."""

    schema_version = "1.0"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self) -> LanControlState:
        if not self.path.is_file():
            return LanControlState.idle()

        payload = json.loads(self.path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict):
            raise ValueError("LAN control state root must be an object.")

        if payload.get("schema_version") != self.schema_version:
            raise ValueError("Unsupported LAN control state schema.")

        return LanControlState.from_dict(payload.get("state"))

    def ensure(self) -> LanControlState:
        state = self.load()

        if not self.path.is_file():
            self._write(state)

        return state

    def set_display(
        self,
        text: str,
        *,
        mode: PatientDisplayMode | str = PatientDisplayMode.PREVIEW,
        task_id: str | None = None,
        countdown_seconds: int | None = None,
    ) -> LanControlState:
        normalized_text = text.strip()

        if not normalized_text:
            raise ValueError("Patient-display text cannot be empty.")

        normalized_mode = _patient_display_mode(mode)
        previous = self.load()

        if normalized_mode not in _ALLOWED_TRANSITIONS[previous.mode]:
            raise LanControlTransitionError(
                f"Invalid patient-display transition: {previous.mode.value} -> "
                f"{normalized_mode.value}"
            )

        if countdown_seconds is not None and countdown_seconds < 0:
            raise ValueError("Patient-display countdown cannot be negative.")

        state = LanControlState(
            revision=previous.revision + 1,
            mode=normalized_mode,
            text=normalized_text,
            task_id=task_id,
            countdown_seconds=countdown_seconds,
            updated_at_utc=utc_now_text(),
        )
        self._write(state)
        return state

    def reset_idle(self) -> LanControlState:
        return self.set_display(
            DEFAULT_IDLE_TEXT,
            mode=PatientDisplayMode.IDLE,
            task_id=None,
        )

    def set_closed(self) -> LanControlState:
        return self.set_display(
            DEFAULT_CLOSED_TEXT,
            mode=PatientDisplayMode.CLOSED,
            task_id=None,
        )

    def _write(self, state: LanControlState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "state": state.to_dict(),
        }

        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        try:
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)


def generate_pairing_token() -> str:
    """Return a URL-safe short-lived token for one desktop run."""
    return secrets.token_urlsafe(24)


def preferred_private_ipv4() -> str:
    """Return the preferred private LAN address, falling back to loopback."""
    candidates: list[str] = []

    try:
        with socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
        ) as connection:
            connection.connect(("192.0.2.1", 9))
            candidates.append(str(connection.getsockname()[0]))
    except OSError:
        pass

    try:
        for result in socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        ):
            candidates.append(str(result[4][0]))
    except OSError:
        pass

    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue

        if address.version == 4 and address.is_private and not address.is_loopback:
            return candidate

    return "127.0.0.1"


def build_control_url(
    host: str,
    port: int,
    token: str,
) -> str:
    """Build the authenticated mobile-control URL."""
    query = urlencode({"token": token})
    return f"http://{host}:{port}/control?{query}"
