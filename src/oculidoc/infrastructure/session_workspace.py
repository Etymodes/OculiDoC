"""Filesystem implementation for experiment workspaces."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from oculidoc.domain.experiment_session import (
    STANDARD_SESSION_ARTIFACTS,
    ExperimentSession,
)


def _datetime_text(
    value: datetime | None,
) -> str | None:
    """Serialize one timestamp as ISO-8601 UTC text."""
    if value is None:
        return None

    if value.tzinfo is None:
        raise ValueError("Session metadata timestamps must be aware.")

    return value.astimezone(UTC).isoformat()


class FileSystemSessionWorkspace:
    """Create session directories and atomically write metadata."""

    def __init__(
        self,
        root_directory: str | Path,
    ) -> None:
        self.root_directory = Path(root_directory).expanduser().resolve()

    def resolve_session_directory(
        self,
        session: ExperimentSession,
    ) -> Path:
        """Resolve the session path below the configured root."""
        relative_path = PurePosixPath(session.data_directory)
        candidate = self.root_directory.joinpath(*relative_path.parts).resolve()

        try:
            candidate.relative_to(self.root_directory)
        except ValueError as error:
            raise ValueError("Session directory escapes the data root.") from error

        return candidate

    def initialize(
        self,
        session: ExperimentSession,
    ) -> Path:
        """Create and return the session directory."""
        session_directory = self.resolve_session_directory(session)
        session_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        return session_directory

    def write_metadata(
        self,
        session: ExperimentSession,
    ) -> Path:
        """Atomically write session.json."""
        session_directory = self.initialize(session)
        metadata_path = session_directory / "session.json"

        payload = {
            "schema_version": session.schema_version,
            "session_id": str(session.session_id),
            "patient_id": str(session.patient_id),
            "module_id": session.module_id,
            "status": session.status.value,
            "data_directory": session.data_directory,
            "clock": {
                "origin_monotonic_ns": (session.clock_origin_monotonic_ns),
                "origin_utc": _datetime_text(session.clock_origin_utc),
            },
            "started_at": _datetime_text(session.started_at),
            "ended_at": _datetime_text(session.ended_at),
            "failure_reason": session.failure_reason,
            "created_at": _datetime_text(session.created_at),
            "updated_at": _datetime_text(session.updated_at),
            "expected_artifacts": [
                {
                    "kind": kind.value,
                    "relative_path": relative_path,
                    "source": source,
                }
                for (
                    kind,
                    relative_path,
                    source,
                ) in STANDARD_SESSION_ARTIFACTS
            ],
        }

        temporary_path = metadata_path.with_name(f".{metadata_path.name}.{uuid4().hex}.tmp")

        try:
            with temporary_path.open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            temporary_path.replace(metadata_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        return metadata_path
