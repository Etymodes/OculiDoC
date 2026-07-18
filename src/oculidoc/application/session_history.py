"""Patient-scoped experiment-session history and export."""

from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from oculidoc.application.experiment_session_service import (
    ExperimentSessionService,
)
from oculidoc.domain.experiment_session import (
    ExperimentSession,
    ExperimentSessionStatus,
)


@dataclass(frozen=True, slots=True)
class SessionHistoryEntry:
    """Display-ready summary for one experiment session."""

    session_id: UUID
    patient_id: UUID
    module_id: str
    status: ExperimentSessionStatus
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    artifact_count: int
    sample_count: int | None
    valid_sample_ratio: float | None
    dwell_by_role_ms: Mapping[str, float]
    failure_reason: str | None
    session_directory: Path

    @property
    def has_task_result(self) -> bool:
        return any(self.session_directory.glob("tasks/*/task_result.json"))


def _safe_number(
    value: object,
) -> float | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    return None


def _task_summary(
    session_directory: Path,
) -> tuple[
    int | None,
    float | None,
    dict[str, float],
]:
    sample_count_total = 0
    valid_sample_total = 0.0
    result_count = 0
    dwell_by_role_ms: dict[str, float] = {}

    for result_path in sorted(session_directory.glob("tasks/*/task_result.json")):
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            continue

        summary = payload.get("summary")

        if not isinstance(summary, dict):
            continue

        raw_sample_count = _safe_number(summary.get("sample_count"))
        sample_count = max(0, int(raw_sample_count)) if raw_sample_count is not None else 0
        raw_valid_ratio = _safe_number(summary.get("valid_sample_ratio"))

        sample_count_total += sample_count

        if raw_valid_ratio is not None:
            valid_sample_total += sample_count * max(
                0.0,
                min(1.0, raw_valid_ratio),
            )

        raw_dwell = summary.get("dwell_by_role_ms")

        if isinstance(raw_dwell, dict):
            for role, duration in raw_dwell.items():
                normalized_duration = _safe_number(duration)

                if normalized_duration is None:
                    continue

                role_name = str(role)
                dwell_by_role_ms[role_name] = dwell_by_role_ms.get(
                    role_name,
                    0.0,
                ) + max(
                    0.0,
                    normalized_duration,
                )

        result_count += 1

    if result_count == 0:
        return None, None, {}

    valid_sample_ratio = valid_sample_total / sample_count_total if sample_count_total > 0 else None

    return (
        sample_count_total,
        valid_sample_ratio,
        dwell_by_role_ms,
    )


def _duration_seconds(
    session: ExperimentSession,
) -> float | None:
    start = session.started_at or session.created_at
    end = session.ended_at

    if end is None:
        return None

    return max(
        0.0,
        (end - start).total_seconds(),
    )


def build_patient_session_history(
    service: ExperimentSessionService,
    patient_id: UUID,
) -> tuple[SessionHistoryEntry, ...]:
    """Return newest-first session summaries."""

    entries: list[SessionHistoryEntry] = []

    for session in service.list_sessions_for_patient(patient_id):
        session_directory = service.resolve_session_directory(session.session_id)
        artifacts = service.list_artifacts(session.session_id)
        (
            sample_count,
            valid_sample_ratio,
            dwell_by_role_ms,
        ) = _task_summary(session_directory)

        entries.append(
            SessionHistoryEntry(
                session_id=session.session_id,
                patient_id=session.patient_id,
                module_id=session.module_id,
                status=session.status,
                created_at=session.created_at,
                started_at=session.started_at,
                ended_at=session.ended_at,
                duration_seconds=(_duration_seconds(session)),
                artifact_count=len(artifacts),
                sample_count=sample_count,
                valid_sample_ratio=(valid_sample_ratio),
                dwell_by_role_ms=(dwell_by_role_ms),
                failure_reason=(session.failure_reason),
                session_directory=(session_directory),
            )
        )

    entries.sort(
        key=lambda entry: entry.created_at,
        reverse=True,
    )
    return tuple(entries)


def export_session_zip(
    service: ExperimentSessionService,
    session_id: UUID,
    destination: str | Path,
) -> Path:
    """Create an atomic ZIP archive for one session."""

    session_directory = service.resolve_session_directory(session_id)
    destination_path = Path(destination).expanduser().resolve()

    if destination_path.suffix.lower() != ".zip":
        destination_path = destination_path.with_suffix(".zip")

    destination_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with NamedTemporaryFile(
        prefix=f".{destination_path.stem}.",
        suffix=".tmp",
        dir=destination_path.parent,
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)

    try:
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in sorted(session_directory.rglob("*")):
                if not path.is_file():
                    continue

                resolved_path = path.resolve()

                if resolved_path in {
                    destination_path,
                    temporary_path,
                }:
                    continue

                archive.write(
                    resolved_path,
                    arcname=(resolved_path.relative_to(session_directory).as_posix()),
                )

        os.replace(
            temporary_path,
            destination_path,
        )
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return destination_path
