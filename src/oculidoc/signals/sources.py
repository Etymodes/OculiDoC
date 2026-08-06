"""Replay and explicitly isolated engineering EEG sources."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from time import monotonic_ns
from typing import Protocol

import numpy as np

from oculidoc.signals.models import EEGSampleBlock, SignalMarker


class EEGSource(Protocol):
    """Minimal acquisition interface used by signal tasks."""

    def acquire(self, duration_seconds: float) -> EEGSampleBlock: ...


class SimulatedEEGSource:
    """Deterministic engineering source; never a clinical fallback."""

    def __init__(
        self,
        *,
        sample_rate_hz: float,
        channel_names: tuple[str, ...],
        target_frequency_hz: float | None = None,
        mi_cue: str | None = None,
        seed: int = 0,
    ) -> None:
        self.sample_rate_hz = float(sample_rate_hz)
        self.channel_names = tuple(channel_names)
        self.target_frequency_hz = target_frequency_hz
        self.mi_cue = mi_cue
        self.seed = int(seed)

    def acquire(self, duration_seconds: float) -> EEGSampleBlock:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive.")
        start_timestamp_ns = monotonic_ns()
        sample_count = max(2, round(duration_seconds * self.sample_rate_hz))
        time_axis = np.arange(sample_count, dtype=np.float64) / self.sample_rate_hz
        generator = np.random.default_rng(self.seed)
        values = generator.normal(0.0, 2.0, (len(self.channel_names), sample_count))

        if self.target_frequency_hz is not None:
            for channel_index in range(len(self.channel_names)):
                amplitude = 5.0 + channel_index * 1.5
                phase = channel_index * 0.18
                values[channel_index] += amplitude * np.sin(
                    2.0 * np.pi * self.target_frequency_hz * time_axis + phase
                )
                values[channel_index] += (
                    0.45
                    * amplitude
                    * np.sin(4.0 * np.pi * self.target_frequency_hz * time_axis + phase / 2.0)
                )

        if self.mi_cue is not None:
            cue = self.mi_cue.strip().casefold()
            for channel_index, name in enumerate(self.channel_names):
                mu_amplitude = 5.0
                if cue == "left" and name.casefold() == "c4":
                    mu_amplitude = 2.0
                if cue == "right" and name.casefold() == "c3":
                    mu_amplitude = 2.0
                values[channel_index] += mu_amplitude * np.sin(20.0 * np.pi * time_axis)

        marker_value: str | float | None = self.target_frequency_hz or self.mi_cue
        markers = (
            SignalMarker("simulation_start", start_timestamp_ns, marker_value),
            SignalMarker(
                "simulation_end",
                start_timestamp_ns + round(duration_seconds * 1_000_000_000),
                marker_value,
            ),
        )
        return EEGSampleBlock(
            device_id="engineering-simulator",
            start_timestamp_ns=start_timestamp_ns,
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self.channel_names,
            values_uv=values,
            quality={name: 1.0 for name in self.channel_names},
            markers=markers,
            simulated=True,
        )


class ReplayEEGSource:
    """Replay one OculiDoC NPZ block without changing its provenance flag."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def acquire(self, duration_seconds: float) -> EEGSampleBlock:
        block = load_eeg_block(self.path)
        if duration_seconds <= 0 or duration_seconds >= block.duration_seconds:
            return block
        sample_count = max(1, round(duration_seconds * block.sample_rate_hz))
        end_ns = block.start_timestamp_ns + round(duration_seconds * 1_000_000_000)
        return EEGSampleBlock(
            device_id=block.device_id,
            start_timestamp_ns=block.start_timestamp_ns,
            sample_rate_hz=block.sample_rate_hz,
            channel_names=block.channel_names,
            values_uv=block.values_uv[:, :sample_count],
            quality=block.quality,
            markers=tuple(marker for marker in block.markers if marker.timestamp_ns <= end_ns),
            simulated=block.simulated,
        )


class LocalJsonLineEEGSource:
    """Read a device-neutral EEG block from a local newline-JSON bridge."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def acquire(self, duration_seconds: float) -> EEGSampleBlock:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive.")
        try:
            lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line]
        except (OSError, UnicodeDecodeError) as error:
            raise RuntimeError(f"Cannot read local EEG bridge output: {self.path}") from error
        if not lines:
            raise RuntimeError("Local EEG bridge output contains no complete JSON block.")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as error:
            raise ValueError("Local EEG bridge output is not valid JSON.") from error
        if not isinstance(payload, dict):
            raise ValueError("Local EEG bridge line must contain an object.")
        required = {
            "device_id",
            "start_timestamp_ns",
            "sample_rate_hz",
            "channel_names",
            "values_uv",
        }
        if not required <= payload.keys():
            raise ValueError("Local EEG bridge block is missing standard fields.")
        block = EEGSampleBlock(
            device_id=str(payload["device_id"]),
            start_timestamp_ns=int(payload["start_timestamp_ns"]),
            sample_rate_hz=float(payload["sample_rate_hz"]),
            channel_names=tuple(str(item) for item in payload["channel_names"]),
            values_uv=np.asarray(payload["values_uv"], dtype=np.float64),
            quality={
                str(name): float(value) for name, value in dict(payload.get("quality", {})).items()
            },
            markers=tuple(SignalMarker.from_dict(item) for item in payload.get("markers", [])),
            simulated=bool(payload.get("simulated", False)),
        )
        if block.duration_seconds <= duration_seconds:
            return block
        sample_count = max(1, round(duration_seconds * block.sample_rate_hz))
        return EEGSampleBlock(
            device_id=block.device_id,
            start_timestamp_ns=block.start_timestamp_ns,
            sample_rate_hz=block.sample_rate_hz,
            channel_names=block.channel_names,
            values_uv=block.values_uv[:, :sample_count],
            quality=block.quality,
            markers=block.markers,
            simulated=block.simulated,
        )


def save_eeg_block(path: str | Path, block: EEGSampleBlock) -> Path:
    """Save a standard block and metadata without pickled objects."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = json.dumps(block.metadata(), ensure_ascii=False, sort_keys=True)
    with target.open("wb") as stream:
        np.savez_compressed(stream, values_uv=block.values_uv, metadata=np.array(metadata))
    return target


def save_eeg_block_csv(
    path: str | Path,
    block: EEGSampleBlock,
    *,
    tag: str | float | int | None,
) -> Path:
    """Write a rectangular, human-auditable companion to the lossless NPZ.

    Unlike the historical JustSsvep file, every row has the same explicit
    channel/tag/timestamp schema. Timestamps use the block's nanosecond clock.
    """

    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    step_ns = 1_000_000_000 / block.sample_rate_hz
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow((*block.channel_names, "tag", "timestamp"))
        for sample_index, values in enumerate(block.values_uv.T):
            writer.writerow(
                (
                    *(float(value) for value in values),
                    "" if tag is None else tag,
                    block.start_timestamp_ns + round(sample_index * step_ns),
                )
            )
    return target


def load_eeg_block(path: str | Path) -> EEGSampleBlock:
    """Load one standard NPZ block with strict metadata validation."""
    target = Path(path).expanduser().resolve()
    try:
        with np.load(target, allow_pickle=False) as archive:
            values = np.asarray(archive["values_uv"], dtype=np.float64)
            metadata = json.loads(str(archive["metadata"].item()))
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"EEG replay file is invalid: {target}") from error
    if not isinstance(metadata, dict) or metadata.get("schema_version") != "1.0":
        raise ValueError("Unsupported EEG replay schema.")
    return EEGSampleBlock(
        device_id=str(metadata["device_id"]),
        start_timestamp_ns=int(metadata["start_timestamp_ns"]),
        sample_rate_hz=float(metadata["sample_rate_hz"]),
        channel_names=tuple(str(item) for item in metadata["channel_names"]),
        values_uv=values,
        quality={
            str(name): float(value) for name, value in dict(metadata.get("quality", {})).items()
        },
        markers=tuple(SignalMarker.from_dict(item) for item in metadata.get("markers", [])),
        simulated=bool(metadata.get("simulated", False)),
    )
