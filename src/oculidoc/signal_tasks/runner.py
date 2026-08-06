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

from oculidoc.bci.ssvep.calibration import load_trca_model, save_trca_model
from oculidoc.bci.ssvep.config import SsvepStimulusConfig
from oculidoc.bci.ssvep.evaluation import DecoderResult, classification_metrics
from oculidoc.bci.ssvep.registry import DecoderRegistry
from oculidoc.bci.ssvep.trca import TrcaModel
from oculidoc.devices.eeg.adapters.mylian import MylianJsonLineSource
from oculidoc.lan_control import utc_now_text
from oculidoc.signal_tasks.config import SignalTaskConfig, SignalTaskKind
from oculidoc.signals.coordinator import SignalCoordinator, SynchronizationMethod
from oculidoc.signals.models import EEGSampleBlock, SignalMarker, SignalSourceKind
from oculidoc.signals.sources import (
    EEGSource,
    LocalJsonLineEEGSource,
    ReplayEEGSource,
    SimulatedEEGSource,
    save_eeg_block,
)

TrialStarted = Callable[[int, int, str, float | None], None]


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


def _atomic_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
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


def _source_for_trial(config: SignalTaskConfig, trial: _Trial) -> EEGSource:
    if config.source_kind is SignalSourceKind.SIMULATION:
        return SimulatedEEGSource(
            sample_rate_hz=config.sample_rate_hz,
            channel_names=config.channel_names,
            target_frequency_hz=trial.frequency_hz,
            mi_cue=(trial.cue if config.task_kind is SignalTaskKind.MI_PROTOCOL else None),
            seed=config.seed + trial.index,
        )
    if config.source_kind is SignalSourceKind.REPLAY:
        assert config.source_path is not None
        return ReplayEEGSource(config.source_path)
    if config.source_kind is SignalSourceKind.LOCAL_BRIDGE:
        assert config.source_path is not None
        return LocalJsonLineEEGSource(config.source_path)
    if config.source_kind is SignalSourceKind.MYLIAN_BRIDGE:
        assert config.source_path is not None
        return MylianJsonLineSource(config.source_path)
    raise ValueError(f"Unsupported signal source: {config.source_kind.value}")


def _channel_quality(block: EEGSampleBlock) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    for index, channel_name in enumerate(block.channel_names):
        values = block.values_uv[index]
        finite = values[np.isfinite(values)]
        metrics[channel_name] = {
            "valid_sample_ratio": float(len(finite) / len(values)),
            "standard_deviation_uv": float(np.std(finite)) if len(finite) else 0.0,
            "peak_to_peak_uv": float(np.ptp(finite)) if len(finite) else 0.0,
            "device_quality": float(block.quality.get(channel_name, 0.0)),
        }
    return metrics


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


def run_signal_task(
    config: SignalTaskConfig,
    output_directory: str | Path,
    *,
    patient_id: str | None = None,
    wait_for_trials: bool = False,
    trial_started: TrialStarted | None = None,
    cancel_event: Event | None = None,
) -> Path:
    """Run one independent task and return its structured result path.

    Simulation is deterministic and remains marked as engineering-only. Source
    errors are never replaced by simulation.
    """

    output_root = Path(output_directory).expanduser().resolve()
    run_id = f"signal-{utc_now_text().replace(':', '').replace('-', '')}-{uuid4().hex[:8]}"
    run_directory = output_root / "tasks" / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    event_path = run_directory / "task_events.jsonl"
    marker_path = run_directory / "signal_markers.jsonl"
    trials = _trial_plan(config)
    blocks: list[EEGSampleBlock] = []
    decoder_results: list[DecoderResult] = []
    expected_frequencies: list[float] = []
    mi_results: list[dict[str, object]] = []
    quality_results: list[dict[str, dict[str, float]]] = []
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
                "cue": trial.cue,
                "frequency_hz": trial.frequency_hz,
            },
        )
        if wait_for_trials:
            _wait(config.duration_seconds, cancel_event)
        block = _source_for_trial(config, trial).acquire(config.duration_seconds)
        if block.channel_names != config.channel_names:
            raise ValueError("Acquired EEG channels do not match the frozen task configuration.")
        if not np.isclose(block.sample_rate_hz, config.sample_rate_hz, rtol=0.0, atol=1e-6):
            raise ValueError(
                "Acquired EEG sample rate does not match the frozen task configuration."
            )
        blocks.append(block)
        block_path = run_directory / f"eeg_trial_{trial.index:03d}.npz"
        save_eeg_block(block_path, block)
        for marker in block.markers:
            _append_json_line(
                marker_path,
                {
                    **marker.to_dict(),
                    "trial_index": trial.index,
                    "source": block.device_id,
                },
            )
        _append_json_line(
            event_path,
            {
                "event_type": "block_acquired",
                "timestamp_ns": monotonic_ns(),
                "trial_index": trial.index,
                "sample_count": block.sample_count,
                "device_id": block.device_id,
                "simulated": block.simulated,
            },
        )

        if config.capability.paradigm.value == "ssvep":
            decoder = _ssvep_decoder(config, block, patient_id=patient_id)
            result = decoder.decode(block.values_uv)
            decoder_results.append(result)
            assert trial.frequency_hz is not None
            expected_frequencies.append(trial.frequency_hz)
            _append_json_line(
                event_path,
                {
                    "event_type": "decoded",
                    "timestamp_ns": monotonic_ns(),
                    "trial_index": trial.index,
                    **result.to_dict(),
                },
            )
        elif config.task_kind is SignalTaskKind.MI_PROTOCOL:
            mi_results.append(_mi_trial_result(block, trial.cue))
        else:
            quality_results.append(_channel_quality(block))

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
    calibration_model: dict[str, object] | None = None
    if config.task_kind is SignalTaskKind.SSVEP_FREQUENCY_SCAN:
        trials_by_frequency = {
            frequency: np.stack(
                [
                    block.values_uv
                    for block, expected in zip(
                        blocks,
                        expected_frequencies,
                        strict=True,
                    )
                    if expected == frequency
                ],
                axis=0,
            )
            for frequency in config.frequencies_hz
        }
        model = TrcaModel.fit(
            trials_by_frequency,
            sample_rate_hz=config.sample_rate_hz,
            channel_names=config.channel_names,
        )
        model_path = save_trca_model(
            run_directory / "ssvep_trca_model.npz",
            model,
            patient_id=patient_id or "unassigned",
            simulated=simulated,
        )
        calibration_model = {
            "algorithm": "trca",
            "algorithm_version": "trca-1.0",
            "file_name": model_path.name,
            "sha256": _file_sha256(model_path),
            "simulated": simulated,
        }
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
        "report_eligible": not simulated,
        "simulation_notice": (
            "Engineering simulation/replay; excluded from patient clinical reports."
            if simulated
            else None
        ),
        "calibration_model": calibration_model,
    }
    if decoder_results:
        result_payload.update(
            {
                "trial_results": [item.to_dict() for item in decoder_results],
                "evaluation": classification_metrics(
                    tuple(expected_frequencies), tuple(decoder_results)
                ),
                "invalid_or_rejected_count": sum(item.rejected for item in decoder_results),
            }
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
        result_payload["channel_quality"] = quality_results[0]

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
    result_path = _atomic_json(run_directory / "task_result.json", payload)
    _atomic_json(
        run_directory / "run_manifest.json",
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "finished",
            "task_result": result_path.name,
            "eeg_blocks": [f"eeg_trial_{index:03d}.npz" for index in range(1, len(blocks) + 1)],
            "markers": marker_path.name,
            "events": event_path.name,
        },
    )
    _append_json_line(
        event_path,
        {
            "event_type": "task_completed",
            "timestamp_ns": monotonic_ns(),
            "trial_count": len(trials),
            "simulated": simulated,
        },
    )
    return result_path


def marker_for_trial(name: str, timestamp_ns: int, value: str) -> SignalMarker:
    """Small public helper used by hardware bridges that emit task markers."""
    return SignalMarker(name=name, timestamp_ns=timestamp_ns, value=value)
