"""Small file-backed signal for replaying the current task instruction."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from oculidoc.lan_control import utc_now_text


@dataclass(frozen=True, slots=True)
class SpeechReplayRequest:
    revision: int
    task_id: str | None
    requested_at_utc: str

    @classmethod
    def from_dict(cls, value: object) -> SpeechReplayRequest:
        if not isinstance(value, dict):
            raise TypeError("Speech replay request must be an object.")

        revision = int(value["revision"])

        if revision < 0:
            raise ValueError("Speech replay revision cannot be negative.")

        task_value = value.get("task_id")
        task_id = str(task_value).strip() if task_value is not None else None
        return cls(
            revision=revision,
            task_id=task_id or None,
            requested_at_utc=str(value["requested_at_utc"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "task_id": self.task_id,
            "requested_at_utc": self.requested_at_utc,
        }


class SpeechReplayStore:
    """Atomically publish monotonically versioned replay requests."""

    schema_version = "1.0"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self) -> SpeechReplayRequest:
        if not self.path.is_file():
            return SpeechReplayRequest(
                revision=0,
                task_id=None,
                requested_at_utc=utc_now_text(),
            )

        payload = json.loads(self.path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict) or payload.get("schema_version") != self.schema_version:
            raise ValueError("Unsupported speech replay schema.")

        return SpeechReplayRequest.from_dict(payload.get("request"))

    def request(self, task_id: str) -> SpeechReplayRequest:
        normalized_task = task_id.strip()

        if not normalized_task:
            raise ValueError("task_id cannot be empty.")

        current = self.load()
        updated = SpeechReplayRequest(
            revision=current.revision + 1,
            task_id=normalized_task,
            requested_at_utc=utc_now_text(),
        )
        self._write(updated)
        return updated

    def _write(self, request: SpeechReplayRequest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

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
                {
                    "schema_version": self.schema_version,
                    "request": request.to_dict(),
                },
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
