"""Validate live patient-scoped gaze-task sessions."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from oculidoc.config import Settings
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
)
from oculidoc.infrastructure.database import (
    initialize_database,
)

REQUIRED_MODULES = (
    "tracking_ball",
    "binary_horizontal",
)


def validate_completed_session(
    service: object,
    session: object,
) -> tuple[bool, list[str]]:
    notes: list[str] = []
    session_directory = service.resolve_session_directory(session.session_id)
    result_paths = sorted(session_directory.glob("tasks/*/task_result.json"))
    gaze_paths = sorted(session_directory.glob("tasks/*/gaze_events.parquet"))
    artifact_paths = {
        artifact.relative_path for artifact in service.list_artifacts(session.session_id)
    }

    if not session_directory.is_dir():
        notes.append("session directory missing")

    if len(result_paths) != 1:
        notes.append("expected one task_result.json")

    if len(gaze_paths) != 1:
        notes.append("expected one gaze_events.parquet")

    for path in (*result_paths, *gaze_paths):
        relative_path = path.relative_to(session_directory).as_posix()

        if relative_path not in artifact_paths:
            notes.append(f"artifact not registered: {relative_path}")

    if result_paths:
        try:
            payload = json.loads(result_paths[0].read_text(encoding="utf-8"))
            sample_count = int(
                payload.get("summary", {}).get(
                    "sample_count",
                    0,
                )
            )

            if sample_count <= 0:
                notes.append("sample_count is zero")
        except (
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as error:
            notes.append(f"invalid task result: {error}")

    return not notes, notes


def build_report() -> tuple[list[str], bool]:
    settings = Settings()
    runtime = initialize_database(
        settings.database_path,
        data_root=settings.data_dir,
    )
    lines: list[str] = []
    passed = True

    try:
        sessions_by_module: dict[str, list[object]] = {
            module_id: [] for module_id in REQUIRED_MODULES
        }
        running_sessions: list[object] = []
        aborted_count = 0
        failed_count = 0

        for patient in runtime.patient_service.list_patients():
            sessions = runtime.experiment_session_service.list_sessions_for_patient(
                patient.patient_id
            )

            for session in sessions:
                if session.module_id not in sessions_by_module:
                    continue

                sessions_by_module[session.module_id].append(session)

                if session.status is ExperimentSessionStatus.RUNNING:
                    running_sessions.append(session)
                elif session.status is ExperimentSessionStatus.ABORTED:
                    aborted_count += 1
                elif session.status is ExperimentSessionStatus.FAILED:
                    failed_count += 1

        lines.append("OculiDoC GAZE TASK LIVE ACCEPTANCE")
        lines.append("=" * 88)
        lines.append(f"DATABASE={settings.database_path}")
        lines.append(f"DATA_ROOT={settings.data_dir}")
        lines.append(f"RUNNING_COUNT={len(running_sessions)}")
        lines.append(f"ABORTED_COUNT={aborted_count}")
        lines.append(f"FAILED_COUNT={failed_count}")
        lines.append("")

        if running_sessions:
            passed = False
            lines.append("ERROR=running gaze sessions remain in the database")
            lines.append("")

        for module_id in REQUIRED_MODULES:
            completed = [
                session
                for session in sessions_by_module[module_id]
                if session.status is ExperimentSessionStatus.COMPLETED
            ]
            completed.sort(
                key=lambda session: session.created_at,
                reverse=True,
            )

            lines.append("-" * 88)
            lines.append(f"MODULE={module_id}")
            lines.append(f"TOTAL_SESSIONS={len(sessions_by_module[module_id])}")
            lines.append(f"COMPLETED_SESSIONS={len(completed)}")

            if not completed:
                passed = False
                lines.append("RESULT=MISSING_COMPLETED_SESSION")
                continue

            selected = completed[0]
            valid, notes = validate_completed_session(
                runtime.experiment_session_service,
                selected,
            )
            lines.append(f"SESSION_ID={selected.session_id}")
            lines.append(f"CREATED_AT={selected.created_at}")
            lines.append("RESULT=" + ("PASS" if valid else "INVALID"))

            for note in notes:
                lines.append(f"NOTE={note}")

            if not valid:
                passed = False

        lines.append("")
        lines.append("=" * 88)
        lines.append("M3D4_LIVE_ACCEPTANCE=" + ("PASS" if passed else "REVIEW_REQUIRED"))
    finally:
        runtime.dispose()

    return lines, passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=(Path(tempfile.gettempdir()) / "OculiDoC_gaze_live_acceptance.txt"),
    )
    arguments = parser.parse_args()

    lines, passed = build_report()
    arguments.output.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print(arguments.output)
    print(lines[-1])

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
