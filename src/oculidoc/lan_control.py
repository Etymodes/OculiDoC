"""Local-network pairing and patient-display control state."""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode

DEFAULT_IDLE_TEXT = "患者显示端已就绪\n等待管理员启动实验"


def utc_now_text() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class LanControlState:
    """Small file-backed state shared by the API and desktop process."""

    revision: int
    mode: str
    text: str
    task_id: str | None
    updated_at_utc: str

    @classmethod
    def idle(cls) -> LanControlState:
        return cls(
            revision=0,
            mode="idle",
            text=DEFAULT_IDLE_TEXT,
            task_id=None,
            updated_at_utc=utc_now_text(),
        )

    @classmethod
    def from_dict(cls, value: object) -> LanControlState:
        if not isinstance(value, dict):
            raise TypeError("LAN control state must be an object.")

        return cls(
            revision=max(0, int(value.get("revision", 0))),
            mode=str(value.get("mode", "idle")),
            text=str(value.get("text", DEFAULT_IDLE_TEXT)),
            task_id=(str(value["task_id"]) if value.get("task_id") is not None else None),
            updated_at_utc=str(value.get("updated_at_utc", utc_now_text())),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "mode": self.mode,
            "text": self.text,
            "task_id": self.task_id,
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
        mode: str = "message",
        task_id: str | None = None,
    ) -> LanControlState:
        normalized_text = text.strip()

        if not normalized_text:
            raise ValueError("Patient-display text cannot be empty.")

        previous = self.load()
        state = LanControlState(
            revision=previous.revision + 1,
            mode=mode.strip() or "message",
            text=normalized_text,
            task_id=task_id,
            updated_at_utc=utc_now_text(),
        )
        self._write(state)
        return state

    def reset_idle(self) -> LanControlState:
        return self.set_display(
            DEFAULT_IDLE_TEXT,
            mode="idle",
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
