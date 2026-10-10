"""Headless core shared by the signal-task UI and acceptance checks."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event
from time import monotonic_ns, sleep
from typing import Protocol
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray

from oculidoc.bci.ssvep.adaptation import guarded_trca_adaptation
from oculidoc.bci.ssvep.calibration import load_trca_model, save_trca_model
from oculidoc.bci.ssvep.config import SsvepStimulusConfig
from oculidoc.bci.ssvep.evaluation import (
    DecoderResult,
    classification_metrics,
    rejected_result,
)
from oculidoc.bci.ssvep.registry import DecoderRegistry
from oculidoc.devices.eeg.adapters.mylian import (
    MylianCsvEEGSource,
    MylianJsonLineSource,
    MylianWebSocketEEGSource,
)
from oculidoc.lan_control import utc_now_text
from oculidoc.signal_tasks.config import SignalTaskConfig, SignalTaskKind
from oculidoc.signals.coordinator import SignalCoordinator, SynchronizationMethod
from oculidoc.signals.models import EEGSampleBlock, SignalMarker, SignalSourceKind
from oculidoc.signals.quality import SignalQualityAssessment, assess_eeg_quality
from oculidoc.signals.sources import (
    EEGSource,
    LocalJsonLineEEGSource,
    ReplayEEGSource,
    SimulatedEEGSource,
    save_eeg_block,
    save_eeg_block_csv,
)

TrialStarted = Callable[[int, int, str, float | None], None]
TrialDecoded = Callable[[int, DecoderResult, str | None], None]
TrialQuality = Callable[[int, SignalQualityAssessment, dict[str, object]], None]


class SsvepDecoder(Protocol):
    algorithm_name: str
    algorithm_version: str

    def decode(self, values_uv: NDArray[np.float64]) -> DecoderResult: ...

    def parameters(self) -> dict[str, object]: ...


class SignalTaskCancelled(RuntimeError):
    """The operator closed or stopped a running signal task."""


@dataclass(frozen=True, slots=True)
class _Trial:
    index: int
    total: int
    cue: str
    frequency_hz: float | None = None
    simulation_frequency_hz: float | None = None


def _atomic_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=".tmp-",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary_path = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def _append_json_line(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")
        stream.flush()


def _trial_plan(config: SignalTaskConfig) -> tuple[_Trial, ...]:
    if config.task_kind is SignalTaskKind.MI_PROTOCOL:
        cues = tuple(cue for _round in range(config.trial_count) for cue in ("left", "right"))
        return tuple(
            _Trial(index=index, total=len(cues), cue=cue) for index, cue in enumerate(cues, start=1)
        )
    if config.task_kind is SignalTaskKind.SSVEP_BINARY_COMMUNICATION:
        return tuple(
            _Trial(
                index=index,
                total=config.trial_count,
                cue="自由选择",
                simulation_frequency_hz=config.frequencies_hz[(index - 1) % 2],
            )
            for index in range(1, config.trial_count + 1)
        )
    if config.capability.paradigm.value == "ssvep":
        frequencies = tuple(
            frequency for _round in range(config.trial_count) for frequency in config.frequencies_hz
        )
        return tuple(
            _Trial(
                index=index,
                total=len(frequencies),
                cue=f"{frequency:g} Hz",
                frequency_hz=frequency,
                simulation_frequency_hz=frequency,
            )
            for index, frequency in enumerate(frequencies, start=1)
        )
    return (_Trial(index=1, total=1, cue="rest"),)


def _wait(duration_seconds: float, cancel_event: Event | None) -> None:
    remaining = duration_seconds
    while remaining > 0:
        if cancel_event is not None and cancel_event.is_set():
            raise SignalTaskCancelled("Signal task cancelled by the operator.")
        interval = min(0.05, remaining)
        sleep(interval)
        remaining -= interval


def _source_for_trial(
    config: SignalTaskConfig,
    trial: _Trial,
    *,
    raw_capture_path: Path,
    cancel_event: Event | None,
    attempt: int,
) -> EEGSource:
    if config.source_kind is SignalSourceKind.SIMULATION:
        return SimulatedEEGSource(
            sample_rate_hz=config.sample_rate_hz,
            channel_names=config.channel_names,
            target_frequency_hz=trial.simulation_frequency_hz,
            mi_cue=(trial.cue if config.task_kind is SignalTaskKind.MI_PROTOCOL else None),
            seed=config.seed + trial.index + attempt * 1_000,
        )
    if config.source_kind is SignalSourceKind.REPLAY:
        assert config.source_path is not None
        return ReplayEEGSource(config.source_path)
    if config.source_kind is SignalSourceKind.LOCAL_BRIDGE:
        assert config.source_path is not None
        return LocalJsonLineEEGSource(config.source_path)
    if config.source_kind is SignalSourceKind.MYLIAN_BRIDGE:
        assert config.source_path is not None
        if Path(config.source_path).suffix.casefold() == ".csv":
            return MylianCsvEEGSource(
                config.source_path,
                sample_rate_hz=config.sample_rate_hz,
                channel_names=config.channel_names,
                value_scale_uv_per_count=config.value_scale_uv_per_count,
            )
        return MylianJsonLineSource(config.source_path)
    if config.source_kind is SignalSourceKind.MYLIAN_WEBSOCKET:
        assert config.source_path is not None
        return MylianWebSocketEEGSource(
            config.source_path,
            sample_rate_hz=config.sample_rate_hz,
            channel_names=config.channel_names,
            value_scale_uv_per_count=config.value_scale_uv_per_count,
            raw_capture_path=raw_capture_path,
            mark_ssvep=config.capability.paradigm.value == "ssvep",
            cancel_event=cancel_event,
        )
    raise ValueError(f"Unsupported signal source: {config.source_kind.value}")


def _bandpower(
    values_uv: np.ndarray,
    *,
    sample_rate_hz: float,
    low_hz: float,
    high_hz: float,
) -> float:
    centered = np.asarray(values_uv, dtype=np.float64) - np.nanmean(values_uv)
    centered = np.nan_to_num(centered, copy=False)
    spectrum = np.abs(np.fft.rfft(centered)) ** 2
    frequencies = np.fft.rfftfreq(len(centered), d=1.0 / sample_rate_hz)
    mask = (frequencies >= low_hz) & (frequencies <= high_hz)
    return float(spectrum[mask].mean()) if mask.any() else 0.0


def _mi_trial_result(block: EEGSampleBlock, cue: str) -> dict[str, object]:
    channels = {}
    for index, channel_name in enumerate(block.channel_names):
        values = block.values_uv[index]
        channels[channel_name] = {
            "mu_8_13_hz": _bandpower(
                values,
                sample_rate_hz=block.sample_rate_hz,
                low_hz=8.0,
                high_hz=13.0,
            ),
            "beta_13_30_hz": _bandpower(
                values,
                sample_rate_hz=block.sample_rate_hz,
                low_hz=13.0,
                high_hz=30.0,
            ),
        }
    return {
        "cue": cue,
        "channel_bandpower": channels,
        "classification": None,
        "boundary": "protocol_and_features_only",
    }


def _ssvep_decoder(
    config: SignalTaskConfig,
    block: EEGSampleBlock,
    *,
    patient_id: str | None,
) -> SsvepDecoder:
    stimulus = SsvepStimulusConfig.for_frequencies(
        config.frequencies_hz,
        refresh_rate_hz=config.refresh_rate_hz,
        screen_index=config.screen_index,
        window_seconds=config.duration_seconds,
    )
    model = None
    if config.decoder_name in {"trca", "etrca"}:
        assert config.model_path is not None
        model, _metadata = load_trca_model(
            config.model_path,
            expected_patient_id=patient_id,
            allow_simulated=config.simulated,
        )
        if model.channel_names != block.channel_names:
            raise ValueError("Calibration model channels do not match the acquired EEG block.")
    return DecoderRegistry.create(
        config.decoder_name,
        stimulus=stimulus,
        sample_rate_hz=block.sample_rate_hz,
        model=model,
    )


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _algorithm_payload(
    decoder: SsvepDecoder | None,
    *,
    model_path: str | None = None,
) -> dict[str, object]:
    if decoder is None:
        return {
            "name": "none",
            "version": "feature-extraction-1.0",
            "parameters": {},
        }
    payload: dict[str, object] = {
        "name": str(decoder.algorithm_name),
        "version": str(decoder.algorithm_version),
        "parameters": dict(decoder.parameters()),
    }
    if model_path is not None:
        payload["model"] = {
            "file_name": Path(model_path).name,
            "sha256": _file_sha256(model_path),
        }
    return payload


def _target_label(config: SignalTaskConfig, result: DecoderResult) -> str | None:
    if result.rejected or result.target_index is None:
        return None
    labels = config.target_labels or tuple(f"{value:g} Hz" for value in config.frequencies_hz)
    return labels[result.target_index]


def run_signal_task(
    config: SignalTaskConfig,
    output_directory: str | Path,
    *,
    patient_id: str | None = None,
    wait_for_trials: bool = False,
    trial_started: TrialStarted | None = None,
    trial_decoded: TrialDecoded | None = None,
    trial_quality: TrialQuality | None = None,
    cancel_event: Event | None = None,
) -> Path:
    """Run one independent task and return its structured result path.

    Simulation is deterministic and remains marked as engineering-only. Source
    errors are never replaced by simulation.
    """

    output_root = Path(output_directory).expanduser().resolve()
    # Keep this segment deliberately short: Windows sites may still enforce the
    # legacy MAX_PATH limit for temporary files created beneath patient/session
    # directories. The timestamp remains in task metadata, not in the path.
    run_id = f"s-{uuid4().hex[:10]}"
    run_directory = output_root / "tasks" / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    event_path = run_directory / "task_events.jsonl"
    marker_path = run_directory / "signal_markers.jsonl"
    marker_path.touch(exist_ok=False)
    trials = _trial_plan(config)
    blocks: list[EEGSampleBlock] = []
    block_paths: list[Path] = []
    csv_paths: list[Path] = []
    raw_capture_paths: list[Path] = []
    decoder_results: list[DecoderResult] = []
    expected_frequencies: list[float] = []
    labeled_trials: dict[float, list[EEGSampleBlock]] = {
        frequency: [] for frequency in config.frequencies_hz
    }
    mi_results: list[dict[str, object]] = []
    final_quality: list[SignalQualityAssessment] = []
    attempt_results: list[dict[str, object]] = []
    task_outcomes: list[dict[str, object]] = []
    source_telemetry: list[dict[str, object]] = []
    decoder: SsvepDecoder | None = None

    _append_json_line(
        event_path,
        {
            "event_type": "task_started",
            "timestamp_ns": monotonic_ns(),
            "task_kind": config.task_kind.value,
            "source_kind": config.source_kind.value,
        },
    )
    for trial in trials:
        maximum_attempts = (
            1 + config.max_retries
            if config.task_kind is SignalTaskKind.SSVEP_BINARY_COMMUNICATION
            else 1
        )
        for attempt in range(1, maximum_attempts + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise SignalTaskCancelled("Signal task cancelled by the operator.")
            if trial_started is not None:
                trial_started(trial.index, trial.total, trial.cue, trial.frequency_hz)
            trial_start_ns = monotonic_ns()
            _append_json_line(
                event_path,
                {
                    "event_type": "trial_started",
                    "timestamp_ns": trial_start_ns,
                    "trial_index": trial.index,
                    "attempt": attempt,
                    "cue": trial.cue,
                    "frequency_hz": trial.frequency_hz,
                },
            )
            suffix = (
                f"_{trial.index:03d}_attempt_{attempt:02d}"
                if maximum_attempts > 1
                else f"_{trial.index:03d}"
            )
            raw_capture_path = run_directory / f"mylian_raw{suffix}.csv"
            source = _source_for_trial(
                config,
                trial,
                raw_capture_path=raw_capture_path,
                cancel_event=cancel_event,
                attempt=attempt,
            )
            if wait_for_trials and config.source_kind is not SignalSourceKind.MYLIAN_WEBSOCKET:
                _wait(config.duration_seconds, cancel_event)
            try:
                block = source.acquire(config.duration_seconds)
            except InterruptedError as error:
                raise SignalTaskCancelled(str(error)) from error
            if block.channel_names != config.channel_names:
                raise ValueError(
                    "Acquired EEG channels do not match the frozen task configuration."
                )
            if not np.isclose(block.sample_rate_hz, config.sample_rate_hz, rtol=0.0, atol=1e-6):
                raise ValueError(
                    "Acquired EEG sample rate does not match the frozen task configuration."
                )
            blocks.append(block)
            if raw_capture_path.is_file():
                raw_capture_paths.append(raw_capture_path)
            block_path = run_directory / f"eeg_trial{suffix}.npz"
            csv_path = run_directory / f"eeg_trial{suffix}.csv"
            block_paths.append(save_eeg_block(block_path, block))
            csv_paths.append(
                save_eeg_block_csv(
                    csv_path,
                    block,
                    tag=(trial.frequency_hz if trial.frequency_hz is not None else trial.cue),
                )
            )
            for marker in block.markers:
                _append_json_line(
                    marker_path,
                    {
                        **marker.to_dict(),
                        "trial_index": trial.index,
                        "attempt": attempt,
                        "source": block.device_id,
                    },
                )
            quality = assess_eeg_quality(block)
            telemetry = (
                dict(source.last_telemetry) if isinstance(source, MylianWebSocketEEGSource) else {}
            )
            source_telemetry.append(
                {
                    "trial_index": trial.index,
                    "attempt": attempt,
                    **telemetry,
                }
            )
            if trial_quality is not None:
                trial_quality(trial.index, quality, telemetry)
            _append_json_line(
                event_path,
                {
                    "event_type": "block_acquired",
                    "timestamp_ns": monotonic_ns(),
                    "trial_index": trial.index,
                    "attempt": attempt,
                    "sample_count": block.sample_count,
                    "device_id": block.device_id,
                    "simulated": block.simulated,
                    "quality_usable": quality.usable,
                    "quality_reasons": list(quality.reasons),
                },
            )

            if config.capability.paradigm.value == "ssvep":
                decoder = _ssvep_decoder(config, block, patient_id=patient_id)
                result = (
                    decoder.decode(block.values_uv)
                    if quality.usable
                    else rejected_result(config.frequencies_hz, "signal_quality_failed")
                )
                selected_label = _target_label(config, result)
                attempt_results.append(
                    {
                        "trial_index": trial.index,
                        "attempt": attempt,
                        "expected_frequency_hz": trial.frequency_hz,
                        "quality_gate": quality.to_dict(),
                        "decoder": result.to_dict(),
                        "selected_label": selected_label,
                    }
                )
                _append_json_line(
                    event_path,
                    {
                        "event_type": "decoded",
                        "timestamp_ns": monotonic_ns(),
                        "trial_index": trial.index,
                        "attempt": attempt,
                        "selected_label": selected_label,
                        **result.to_dict(),
                    },
                )
                if trial_decoded is not None:
                    trial_decoded(trial.index, result, selected_label)
                if wait_for_trials and config.feedback_seconds:
                    _wait(config.feedback_seconds, cancel_event)
                should_retry = (
                    config.task_kind is SignalTaskKind.SSVEP_BINARY_COMMUNICATION
                    and result.rejected
                    and attempt < maximum_attempts
                )
                if should_retry:
                    _append_json_line(
                        event_path,
                        {
                            "event_type": "trial_retry_scheduled",
                            "timestamp_ns": monotonic_ns(),
                            "trial_index": trial.index,
                            "next_attempt": attempt + 1,
                            "reason": result.reject_reason,
                        },
                    )
                    continue
                decoder_results.append(result)
                final_quality.append(quality)
                if trial.frequency_hz is not None:
                    expected_frequencies.append(trial.frequency_hz)
                    if quality.usable:
                        labeled_trials[trial.frequency_hz].append(block)
                if config.task_kind is SignalTaskKind.SSVEP_BINARY_COMMUNICATION:
                    outcome: dict[str, object] = {
                        "trial_index": trial.index,
                        "status": "selected" if selected_label is not None else "rejected",
                        "selected_label": selected_label,
                        "target_index": result.target_index,
                        "target_frequency_hz": result.target_frequency_hz,
                        "confidence": result.confidence,
                        "margin": result.margin,
                        "attempts": attempt,
                        "retry_exhausted": result.rejected,
                    }
                    task_outcomes.append(outcome)
                    _append_json_line(
                        event_path,
                        {
                            "event_type": "task_feedback",
                            "timestamp_ns": monotonic_ns(),
                            **outcome,
                        },
                    )
                break
            final_quality.append(quality)
            if config.task_kind is SignalTaskKind.MI_PROTOCOL:
                mi_result = _mi_trial_result(block, trial.cue)
                mi_result["quality_gate"] = quality.to_dict()
                mi_results.append(mi_result)
            break

    simulated = any(block.simulated for block in blocks)
    sample_count = sum(block.sample_count for block in blocks)
    valid_count = sum(np.isfinite(block.values_uv).sum() for block in blocks)
    value_count = sum(block.values_uv.size for block in blocks)
    device_ids = tuple(dict.fromkeys(block.device_id for block in blocks))
    channel_sets = tuple(dict.fromkeys(block.channel_names for block in blocks))
    sample_rates = tuple(dict.fromkeys(block.sample_rate_hz for block in blocks))
    available_sync_methods: list[SynchronizationMethod] = [SynchronizationMethod.TIMESTAMP]
    marker_names = {marker.name.casefold() for block in blocks for marker in block.markers}
    if "hardware_trigger" in marker_names:
        available_sync_methods.append(SynchronizationMethod.HARDWARE_TRIGGER)
    if "lsl" in marker_names:
        available_sync_methods.append(SynchronizationMethod.LSL)
    synchronization = SignalCoordinator.for_available_methods(tuple(available_sync_methods)).method
    historical_justssvep_csv = (
        config.source_kind is SignalSourceKind.MYLIAN_BRIDGE
        and config.source_path is not None
        and Path(config.source_path).suffix.casefold() == ".csv"
    )
    count_scale_source = (
        config.source_kind is SignalSourceKind.MYLIAN_WEBSOCKET or historical_justssvep_csv
    )
    calibration_model: dict[str, object] | None = None
    if config.task_kind is SignalTaskKind.SSVEP_FREQUENCY_SCAN:
        counts = tuple(len(labeled_trials[frequency]) for frequency in config.frequencies_hz)
        prior_model = None
        prior_model_sha256 = None
        if config.model_path is not None:
            prior_model, _prior_metadata = load_trca_model(
                config.model_path,
                expected_patient_id=patient_id,
                allow_simulated=config.simulated,
            )
            prior_model_sha256 = _file_sha256(config.model_path)
        model = None
        if len(set(counts)) == 1 and counts[0] > 0:
            trials_by_frequency = {
                frequency: np.stack(
                    [block.values_uv for block in labeled_trials[frequency]],
                    axis=0,
                )
                for frequency in config.frequencies_hz
            }
            model, adaptation_decision = guarded_trca_adaptation(
                trials_by_frequency,
                sample_rate_hz=config.sample_rate_hz,
                channel_names=config.channel_names,
                prior_model=prior_model,
            )
            adaptation = adaptation_decision.to_dict()
        else:
            adaptation = {
                "policy_version": "guarded-trca-1.0",
                "accepted": False,
                "reason": "unbalanced_quality_passed_trials",
                "quality_passed_trials_per_frequency": list(counts),
                "label_policy": "operator_cued_calibration_only",
                "unlabeled_self_training": False,
            }
        model_path: Path | None = None
        if model is not None:
            model_path = save_trca_model(
                run_directory / "ssvep_trca_model.npz",
                model,
                patient_id=patient_id or "unassigned",
                simulated=simulated,
                adaptation=adaptation,
            )
        recommended = (
            model_path is not None
            and not simulated
            and not historical_justssvep_csv
            and (not count_scale_source or config.scale_verified)
        )
        calibration_model = {
            "algorithm": "trca",
            "algorithm_version": "trca-1.0",
            "adaptation": adaptation,
            "status": "accepted" if model_path is not None else "rejected",
            "file_name": model_path.name if model_path is not None else None,
            "sha256": _file_sha256(model_path) if model_path is not None else None,
            "parent_model_sha256": prior_model_sha256,
            "recommended_for_use": recommended,
            "simulated": simulated,
        }
    quality_all_usable = bool(final_quality) and all(item.usable for item in final_quality)
    ineligibility_reasons = []
    if simulated:
        ineligibility_reasons.append("simulated_or_simulated_replay")
    if not quality_all_usable:
        ineligibility_reasons.append("signal_quality_gate_failed")
    if historical_justssvep_csv:
        ineligibility_reasons.append("historical_csv_labels_not_verified")
    if count_scale_source and not config.scale_verified:
        ineligibility_reasons.append("microvolt_scale_unverified")
    result_payload: dict[str, object] = {
        "paradigm": config.paradigm.value,
        "source_kind": config.source_kind.value,
        "device_ids": list(device_ids),
        "channel_names": [list(channels) for channels in channel_sets],
        "sample_rates_hz": list(sample_rates),
        "configured_frequencies_hz": list(config.frequencies_hz),
        "algorithm": _algorithm_payload(decoder, model_path=config.model_path),
        "synchronization": {
            "method": synchronization.value,
            "priority_policy": [
                "hardware_trigger",
                "lsl",
                "timestamp",
                "software_estimate",
            ],
        },
        "simulated": simulated,
        "report_eligible": not ineligibility_reasons,
        "report_ineligibility_reasons": ineligibility_reasons,
        "simulation_notice": (
            "Engineering simulation/replay; excluded from patient clinical reports."
            if simulated
            else None
        ),
        "calibration_model": calibration_model,
        "input_provenance": {
            "raw_count_source": count_scale_source,
            "value_scale_uv_per_count": (
                config.value_scale_uv_per_count if count_scale_source else None
            ),
            "scale_verified": config.scale_verified if count_scale_source else None,
            "historical_csv_replay": historical_justssvep_csv,
            "direct_serial_claimed": False,
        },
        "quality_gate": {
            "all_usable": quality_all_usable,
            "usable_trial_count": sum(item.usable for item in final_quality),
            "trial_count": len(final_quality),
            "trials": [item.to_dict() for item in final_quality],
        },
        "source_telemetry": source_telemetry,
    }
    if decoder_results:
        result_payload["trial_results"] = [item.to_dict() for item in decoder_results]
        result_payload["attempt_results"] = attempt_results
        result_payload["invalid_or_rejected_count"] = sum(item.rejected for item in decoder_results)
        if config.task_kind is SignalTaskKind.SSVEP_BINARY_COMMUNICATION:
            result_payload["task_outcomes"] = task_outcomes
            result_payload["task_closed_loop"] = {
                "stimulus": True,
                "raw_data_recorded": True,
                "quality_gated": True,
                "decoded": True,
                "visible_feedback": True,
                "rejection_retry": True,
                "external_robotic_actuation": False,
            }
            result_payload["evaluation"] = None
        else:
            result_payload["evaluation"] = classification_metrics(
                tuple(expected_frequencies), tuple(decoder_results)
            )
    elif mi_results:
        result_payload.update(
            {
                "trials": mi_results,
                "classification": None,
                "boundary": "MI remains independent; no gaze/SSVEP control fusion in v0.1.3.",
            }
        )
    else:
        result_payload["channel_quality"] = final_quality[0].channel_metrics

    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "task_kind": config.task_kind.value,
        "end_reason": "completed",
        "created_at_utc": utc_now_text(),
        "summary": {
            "sample_count": sample_count,
            "valid_sample_ratio": float(valid_count / value_count) if value_count else 0.0,
        },
        "result": result_payload,
    }
    _append_json_line(
        event_path,
        {
            "event_type": "task_completed",
            "timestamp_ns": monotonic_ns(),
            "trial_count": len(trials),
            "simulated": simulated,
        },
    )
    result_path = _atomic_json(run_directory / "task_result.json", payload)
    artifact_paths = tuple(
        path
        for path in sorted(run_directory.iterdir())
        if path.is_file() and path.name != "run_manifest.json" and not path.name.startswith(".tmp-")
    )
    _atomic_json(
        run_directory / "run_manifest.json",
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "finished",
            "task_result": result_path.name,
            "eeg_blocks": [path.name for path in block_paths],
            "eeg_csv": [path.name for path in csv_paths],
            "raw_source_captures": [path.name for path in raw_capture_paths],
            "markers": marker_path.name,
            "events": event_path.name,
            "artifacts": [
                {"path": path.name, "sha256": _file_sha256(path)} for path in artifact_paths
            ],
        },
    )
    return result_path


def marker_for_trial(name: str, timestamp_ns: int, value: str) -> SignalMarker:
    """Small public helper used by hardware bridges that emit task markers."""
    return SignalMarker(name=name, timestamp_ns=timestamp_ns, value=value)
