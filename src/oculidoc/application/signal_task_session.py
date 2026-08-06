"""Bind independent neural-signal processes to patient sessions."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from oculidoc.application.builtin_test_patient import BUILTIN_TEST_PATIENT_CODE
from oculidoc.application.experiment_session_service import (
    CreateExperimentSessionRequest,
    DuplicateSessionArtifactError,
    ExperimentSessionService,
    RegisterSessionArtifactRequest,
)
from oculidoc.bci.ssvep.registry import DecoderRegistry
from oculidoc.domain.experiment_session import ExperimentSessionStatus, SessionArtifactKind
from oculidoc.signal_tasks.config import SignalTaskConfig
from oculidoc.signals.models import SignalSourceKind
from oculidoc.signals.profile import PatientSignalProfileStore
from oculidoc.signals.snapshot import SessionSignalSnapshot
from oculidoc.signals.sources import LocalJsonLineEEGSource, load_eeg_block


@dataclass(frozen=True, slots=True)
class SignalTaskLaunch:
    """One patient-scoped neural-signal child process launch."""

    session_id: UUID
    patient_id: UUID
    patient_code: str
    module_id: str
    config: SignalTaskConfig
    session_directory: Path
    config_path: Path
    snapshot_path: Path

    @property
    def process_environment(self) -> dict[str, str]:
        return {
            "OCULIDOC_PATIENT_ID": str(self.patient_id),
            "OCULIDOC_SESSION_ID": str(self.session_id),
            "OCULIDOC_SESSION_DIRECTORY": str(self.session_directory),
        }


def _is_simulated_input(config: SignalTaskConfig) -> bool:
    if config.source_kind is SignalSourceKind.SIMULATION:
        return True
    if config.source_kind is SignalSourceKind.REPLAY:
        assert config.source_path is not None
        return load_eeg_block(config.source_path).simulated
    if config.source_kind is SignalSourceKind.LOCAL_BRIDGE:
        assert config.source_path is not None
        return LocalJsonLineEEGSource(config.source_path).acquire(config.duration_seconds).simulated
    return False


def create_signal_task_launch(
    service: ExperimentSessionService,
    profile_store: PatientSignalProfileStore,
    *,
    patient_id: UUID,
    config: SignalTaskConfig,
) -> SignalTaskLaunch:
    """Create a session and freeze its profile/configuration before launch."""

    patient = service.get_patient(patient_id)
    if (
        _is_simulated_input(config)
        and patient.patient_code.casefold() != BUILTIN_TEST_PATIENT_CODE.casefold()
    ):
        raise ValueError("工程模拟及模拟来源回放仅允许用于内置 Beta00，不能写入真实患者会话。")
    profile = profile_store.load(str(patient_id))
    updated_profile = profile.with_session_defaults(
        paradigms=profile.default_paradigms + (config.paradigm,),
        frequencies_hz=config.frequencies_hz,
        algorithm=(config.decoder_name if config.capability.decoder_required else None),
        device_id=config.device_id,
        calibration_model=config.model_path,
    )
    saved_profile = profile_store.save(updated_profile, expected_revision=profile.revision)
    session = service.create_session(
        CreateExperimentSessionRequest(
            patient_id=patient_id,
            module_id="neural_interaction",
            schema_version="1.1",
        )
    )
    session_directory = service.resolve_session_directory(session.session_id)
    config_path = config.write(session_directory / "signal_task_config.json")
    snapshot = SessionSignalSnapshot.create(
        patient_id=str(patient_id),
        profile_revision=saved_profile.revision,
        paradigms=(config.paradigm,),
        task_kind=config.task_kind.value,
        source_kind=config.source_kind,
        device_id=config.device_id,
        sample_rate_hz=config.sample_rate_hz,
        channel_names=config.channel_names,
        task_configuration=config.to_dict(),
        algorithm_versions=(
            {config.decoder_name: DecoderRegistry.versions()[config.decoder_name]}
            if config.capability.decoder_required
            else {"feature-extraction": "1.0"}
        ),
        simulated=_is_simulated_input(config),
    )
    snapshot_path = snapshot.write(session_directory / "signal_snapshot.json")
    try:
        service.start_session(session.session_id)
    except Exception:
        try:
            service.abort_session(
                session.session_id,
                "Signal task launch failed before the child process started.",
            )
        except Exception:
            pass
        raise
    return SignalTaskLaunch(
        session_id=session.session_id,
        patient_id=patient_id,
        patient_code=patient.patient_code,
        module_id="neural_interaction",
        config=config,
        session_directory=session_directory,
        config_path=config_path,
        snapshot_path=snapshot_path,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_kind(path: Path) -> SessionArtifactKind:
    if path.name.startswith("eeg_trial_") and path.suffix.lower() == ".npz":
        return SessionArtifactKind.EEG
    if path.name == "ssvep_trca_model.npz":
        return SessionArtifactKind.DECODER_MODEL
    if path.name == "signal_markers.jsonl":
        return SessionArtifactKind.SIGNAL_MARKERS
    if path.name == "signal_task_config.json":
        return SessionArtifactKind.SIGNAL_CONFIGURATION
    if path.name == "signal_snapshot.json":
        return SessionArtifactKind.SIGNAL_SNAPSHOT
    if path.name.startswith("signal_report."):
        return SessionArtifactKind.SIGNAL_REPORT
    if path.name == "task_events.jsonl":
        return SessionArtifactKind.EVENTS
    return SessionArtifactKind.OTHER


def _artifact_source(path: Path) -> str:
    kind = _artifact_kind(path)
    if kind is SessionArtifactKind.EEG:
        return "eeg_source"
    if kind in {SessionArtifactKind.SIGNAL_CONFIGURATION, SessionArtifactKind.SIGNAL_SNAPSHOT}:
        return "signal_session"
    if kind is SessionArtifactKind.SIGNAL_REPORT:
        return "signal_report"
    return "signal_task"


def _mime_type(path: Path) -> str | None:
    if path.suffix.lower() == ".jsonl":
        return "application/x-ndjson"
    if path.suffix.lower() == ".npz":
        return "application/x-numpy-archive"
    return mimetypes.guess_type(path.name)[0]


def discover_signal_task_artifacts(launch: SignalTaskLaunch) -> tuple[Path, ...]:
    """Return all stable signal products except the service-owned session.json."""

    return tuple(
        path
        for path in sorted(launch.session_directory.rglob("*"))
        if path.is_file()
        and path.name != "session.json"
        and not path.name.startswith(".")
        and not path.name.endswith(".tmp")
    )


def register_signal_task_artifacts(
    service: ExperimentSessionService,
    launch: SignalTaskLaunch,
) -> tuple[Path, ...]:
    paths = discover_signal_task_artifacts(launch)
    for path in paths:
        relative_path = path.relative_to(launch.session_directory).as_posix()
        try:
            service.register_artifact(
                RegisterSessionArtifactRequest(
                    session_id=launch.session_id,
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


def finalize_signal_task_launch(
    service: ExperimentSessionService,
    launch: SignalTaskLaunch,
    *,
    exit_code: int,
    process_output: str = "",
) -> ExperimentSessionStatus:
    """Register products and finish a signal session without gaze coupling."""

    paths = register_signal_task_artifacts(service, launch)
    session = service.get_session(launch.session_id)
    if session.is_terminal:
        return session.status
    if exit_code == 2:
        return service.abort_session(launch.session_id, "Signal task cancelled by operator.").status
    if exit_code != 0:
        output_tail = process_output.strip()[-1_500:]
        reason = f"Signal task process exited with code {exit_code}."
        if output_tail:
            reason += f" Output: {output_tail}"
        return service.fail_session(launch.session_id, reason).status
    result_paths = tuple(path for path in paths if path.name == "task_result.json")
    if not result_paths:
        return service.fail_session(
            launch.session_id,
            "The signal task ended without task_result.json.",
        ).status
    try:
        results = [json.loads(path.read_text(encoding="utf-8")) for path in result_paths]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return service.fail_session(
            launch.session_id, f"Invalid signal task result: {error}"
        ).status
    if any(not isinstance(item, dict) or item.get("end_reason") != "completed" for item in results):
        return service.fail_session(
            launch.session_id,
            "Signal task result does not record a completed end state.",
        ).status
    return service.complete_session(launch.session_id).status
