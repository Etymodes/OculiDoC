"""Bind gaze task processes to patient experiment sessions."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from oculidoc.application.experiment_session_service import (
    CreateExperimentSessionRequest,
    DuplicateSessionArtifactError,
    ExperimentSessionService,
    RegisterSessionArtifactRequest,
)
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
    SessionArtifactKind,
)

_GAZE_TASK_COMMANDS = {
    "tracking_ball": "tracking",
    "binary_horizontal": "binary",
    "binary_vertical": "binary-vertical",
    "screen_keyboard": "typing",
    "multiple_choice": "multiple-choice",
    "image_choice": "image-choice",
    "instruction_fixation": "instruction-fixation",
}


@dataclass(frozen=True, slots=True)
class GazeTaskLaunch:
    """One patient-scoped gaze task process launch."""

    session_id: UUID
    patient_id: UUID
    module_id: str
    command: str
    session_directory: Path

    def __post_init__(self) -> None:
        normalized_module = self.module_id.strip()
        normalized_command = self.command.strip()
        resolved_directory = Path(self.session_directory).expanduser().resolve()

        if not normalized_module:
            raise ValueError("module_id cannot be empty.")

        if not normalized_command:
            raise ValueError("command cannot be empty.")

        object.__setattr__(
            self,
            "module_id",
            normalized_module,
        )
        object.__setattr__(
            self,
            "command",
            normalized_command,
        )
        object.__setattr__(
            self,
            "session_directory",
            resolved_directory,
        )

    @property
    def process_environment(
        self,
    ) -> dict[str, str]:
        """Return identity and workspace variables for the child."""

        return {
            "OCULIDOC_PATIENT_ID": str(self.patient_id),
            "OCULIDOC_SESSION_ID": str(self.session_id),
            "OCULIDOC_SESSION_DIRECTORY": str(self.session_directory),
        }


def create_gaze_task_launch(
    service: ExperimentSessionService,
    *,
    patient_id: UUID,
    module_id: str,
) -> GazeTaskLaunch:
    """Create and start a patient-scoped gaze session."""

    normalized_module = module_id.strip()

    try:
        command = _GAZE_TASK_COMMANDS[normalized_module]
    except KeyError as error:
        raise ValueError(f"Unsupported gaze task module: {normalized_module}") from error

    session = service.create_session(
        CreateExperimentSessionRequest(
            patient_id=patient_id,
            module_id=normalized_module,
        )
    )

    try:
        service.start_session(session.session_id)
        session_directory = service.resolve_session_directory(session.session_id)
    except Exception:
        try:
            service.abort_session(
                session.session_id,
                "Gaze task launch failed before the child process started.",
            )
        except Exception:
            pass

        raise

    return GazeTaskLaunch(
        session_id=session.session_id,
        patient_id=patient_id,
        module_id=normalized_module,
        command=command,
        session_directory=session_directory,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def _artifact_kind(
    path: Path,
) -> SessionArtifactKind:
    if path.name == "gaze_events.parquet":
        return SessionArtifactKind.GAZE

    if path.name == "task_events.jsonl":
        return SessionArtifactKind.EVENTS

    return SessionArtifactKind.OTHER


def _artifact_source(
    path: Path,
) -> str:
    if path.name == "gaze_events.parquet":
        return "gaze_stream"

    if path.name == "task_events.jsonl":
        return "task"

    return "task_runtime"


def _mime_type(path: Path) -> str | None:
    if path.suffix.lower() == ".parquet":
        return "application/vnd.apache.parquet"

    if path.suffix.lower() == ".jsonl":
        return "application/x-ndjson"

    return mimetypes.guess_type(path.name)[0]


def discover_gaze_task_artifacts(
    launch: GazeTaskLaunch,
) -> tuple[Path, ...]:
    """Return stable files produced below the tasks directory."""

    task_root = launch.session_directory / "tasks"

    if not task_root.is_dir():
        return ()

    return tuple(
        path
        for path in sorted(task_root.rglob("*"))
        if (path.is_file() and not path.name.startswith(".") and not path.name.endswith(".tmp"))
    )


def register_gaze_task_artifacts(
    service: ExperimentSessionService,
    launch: GazeTaskLaunch,
) -> tuple[Path, ...]:
    """Register every completed task file in the session manifest."""

    paths = discover_gaze_task_artifacts(launch)

    for path in paths:
        relative_path = path.relative_to(launch.session_directory).as_posix()

        try:
            service.register_artifact(
                RegisterSessionArtifactRequest(
                    session_id=(launch.session_id),
                    kind=_artifact_kind(path),
                    relative_path=relative_path,
                    source=_artifact_source(path),
                    mime_type=_mime_type(path),
                    size_bytes=path.stat().st_size,
                    sha256=_sha256(path),
                )
            )
        except DuplicateSessionArtifactError:
            continue

    return paths


def _recording_failure_reason(
    result_paths: tuple[Path, ...],
) -> str | None:
    for path in result_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            return f"Invalid task result file {path.name}: {error}"

        result = payload.get("result")

        if isinstance(result, dict) and result.get("recording_failed") is True:
            return "The task UI completed, but experiment recording failed."

    return None


def finalize_gaze_task_launch(
    service: ExperimentSessionService,
    launch: GazeTaskLaunch,
    *,
    exit_code: int,
    process_output: str = "",
) -> ExperimentSessionStatus:
    """Register outputs and finalize the database session."""

    paths = register_gaze_task_artifacts(
        service,
        launch,
    )
    session = service.get_session(launch.session_id)

    if session.is_terminal:
        return session.status

    if exit_code != 0:
        output_tail = process_output.strip()[-1_500:]
        reason = f"Gaze task process exited with code {exit_code}."

        if output_tail:
            reason += f" Output: {output_tail}"

        return service.fail_session(
            launch.session_id,
            reason,
        ).status

    result_paths = tuple(path for path in paths if path.name == "task_result.json")

    if not paths:
        return service.abort_session(
            launch.session_id,
            "Task setup was cancelled before recording started.",
        ).status

    if not result_paths:
        return service.fail_session(
            launch.session_id,
            "The task process ended without task_result.json.",
        ).status

    failure_reason = _recording_failure_reason(result_paths)

    if failure_reason is not None:
        return service.fail_session(
            launch.session_id,
            failure_reason,
        ).status

    return service.complete_session(launch.session_id).status
