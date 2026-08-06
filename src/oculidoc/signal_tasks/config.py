"""Auditable configuration and capability matrix for neural-signal tasks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile

from oculidoc.bci.ssvep.registry import DecoderRegistry
from oculidoc.signals.models import SignalParadigm, SignalSourceKind


class SignalTaskKind(StrEnum):
    """Tasks delivered independently from the existing gaze task family."""

    EEG_QUALITY = "eeg_quality"
    SSVEP_FREQUENCY_SCAN = "ssvep_frequency_scan"
    SSVEP_SINGLE_TARGET = "ssvep_single_target"
    SSVEP_BINARY_CHOICE = "ssvep_binary_choice"
    SSVEP_FOUR_TARGET = "ssvep_four_target"
    SSVEP_VALIDATION = "ssvep_validation"
    MI_PROTOCOL = "mi_protocol"


@dataclass(frozen=True, slots=True)
class SignalTaskCapability:
    """One explicit task-to-paradigm/source/decoder compatibility rule."""

    task_kind: SignalTaskKind
    title: str
    paradigm: SignalParadigm
    allowed_sources: tuple[SignalSourceKind, ...]
    frequency_count: int | None = None
    decoder_required: bool = False


_ALL_V013_SOURCES = (
    SignalSourceKind.MYLIAN_BRIDGE,
    SignalSourceKind.LOCAL_BRIDGE,
    SignalSourceKind.REPLAY,
    SignalSourceKind.SIMULATION,
)

SIGNAL_TASK_CAPABILITIES: tuple[SignalTaskCapability, ...] = (
    SignalTaskCapability(
        SignalTaskKind.EEG_QUALITY,
        "EEG 信号质量检查",
        SignalParadigm.PASSIVE_EEG,
        _ALL_V013_SOURCES,
    ),
    SignalTaskCapability(
        SignalTaskKind.SSVEP_FREQUENCY_SCAN,
        "SSVEP 频率扫描",
        SignalParadigm.SSVEP,
        _ALL_V013_SOURCES,
        decoder_required=True,
    ),
    SignalTaskCapability(
        SignalTaskKind.SSVEP_SINGLE_TARGET,
        "SSVEP 单目标",
        SignalParadigm.SSVEP,
        _ALL_V013_SOURCES,
        frequency_count=1,
        decoder_required=True,
    ),
    SignalTaskCapability(
        SignalTaskKind.SSVEP_BINARY_CHOICE,
        "SSVEP 二目标",
        SignalParadigm.SSVEP,
        _ALL_V013_SOURCES,
        frequency_count=2,
        decoder_required=True,
    ),
    SignalTaskCapability(
        SignalTaskKind.SSVEP_FOUR_TARGET,
        "SSVEP 四目标",
        SignalParadigm.SSVEP,
        _ALL_V013_SOURCES,
        frequency_count=4,
        decoder_required=True,
    ),
    SignalTaskCapability(
        SignalTaskKind.SSVEP_VALIDATION,
        "SSVEP 解码验证",
        SignalParadigm.SSVEP,
        _ALL_V013_SOURCES,
        decoder_required=True,
    ),
    SignalTaskCapability(
        SignalTaskKind.MI_PROTOCOL,
        "运动想象独立协议",
        SignalParadigm.MI,
        _ALL_V013_SOURCES,
    ),
)

_CAPABILITY_BY_KIND = {item.task_kind: item for item in SIGNAL_TASK_CAPABILITIES}


@dataclass(frozen=True, slots=True)
class SignalTaskConfig:
    """All parameters required to reproduce one independent signal task."""

    task_kind: SignalTaskKind
    source_kind: SignalSourceKind
    sample_rate_hz: float = 250.0
    channel_names: tuple[str, ...] = ("O1", "Oz", "O2")
    duration_seconds: float = 2.0
    frequencies_hz: tuple[float, ...] = ()
    decoder_name: str = "fbcca"
    trial_count: int = 2
    seed: int = 13
    source_path: str | None = None
    model_path: str | None = None
    refresh_rate_hz: float = 60.0
    screen_index: int = 0
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        task_kind = SignalTaskKind(self.task_kind)
        source_kind = SignalSourceKind(self.source_kind)
        capability = _CAPABILITY_BY_KIND[task_kind]
        channels = tuple(name.strip() for name in self.channel_names)
        frequencies = tuple(float(value) for value in self.frequencies_hz)
        decoder_name = self.decoder_name.strip().casefold()
        source_path = self.source_path.strip() if self.source_path else None
        model_path = self.model_path.strip() if self.model_path else None

        if source_kind not in capability.allowed_sources:
            raise ValueError(f"{task_kind.value} does not support source {source_kind.value}.")
        if self.sample_rate_hz <= 0 or self.duration_seconds <= 0:
            raise ValueError("Signal sample rate and duration must be positive.")
        if (
            not channels
            or any(not name for name in channels)
            or len(set(channels)) != len(channels)
        ):
            raise ValueError("Signal channel_names must contain unique non-empty names.")
        if isinstance(self.trial_count, bool) or self.trial_count < 1 or self.trial_count > 100:
            raise ValueError("Signal trial_count must be from 1 to 100.")
        if task_kind is SignalTaskKind.SSVEP_FREQUENCY_SCAN and self.trial_count < 2:
            raise ValueError("SSVEP frequency scan requires at least two rounds for calibration.")
        if self.refresh_rate_hz <= 0 or self.screen_index < 0:
            raise ValueError("Signal display settings are invalid.")
        if len(set(frequencies)) != len(frequencies) or any(value <= 0 for value in frequencies):
            raise ValueError("SSVEP frequencies must be unique positive values.")
        if capability.paradigm is SignalParadigm.SSVEP:
            if not frequencies:
                raise ValueError("SSVEP tasks require configured frequencies.")
            if max(frequencies) >= self.refresh_rate_hz / 2:
                raise ValueError("SSVEP frequencies must remain below half the refresh rate.")
            if (
                capability.frequency_count is not None
                and len(frequencies) != capability.frequency_count
            ):
                raise ValueError(
                    f"{task_kind.value} requires {capability.frequency_count} frequencies."
                )
            if decoder_name not in DecoderRegistry.names():
                raise ValueError(f"Unsupported SSVEP decoder: {decoder_name}")
            if decoder_name in {"trca", "etrca"} and model_path is None:
                raise ValueError(f"{decoder_name} requires a patient calibration model path.")
        elif frequencies:
            raise ValueError(f"{task_kind.value} does not accept SSVEP frequencies.")
        if source_kind in {
            SignalSourceKind.REPLAY,
            SignalSourceKind.LOCAL_BRIDGE,
            SignalSourceKind.MYLIAN_BRIDGE,
        }:
            if source_path is None:
                raise ValueError(f"{source_kind.value} requires a local source path.")

        object.__setattr__(self, "task_kind", task_kind)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "channel_names", channels)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "decoder_name", decoder_name)
        object.__setattr__(self, "source_path", source_path)
        object.__setattr__(self, "model_path", model_path)

    @property
    def capability(self) -> SignalTaskCapability:
        return _CAPABILITY_BY_KIND[self.task_kind]

    @property
    def paradigm(self) -> SignalParadigm:
        return self.capability.paradigm

    @property
    def simulated(self) -> bool:
        return self.source_kind is SignalSourceKind.SIMULATION

    @property
    def device_id(self) -> str:
        return {
            SignalSourceKind.SIMULATION: "engineering-simulator",
            SignalSourceKind.REPLAY: "replay-source",
            SignalSourceKind.LOCAL_BRIDGE: "local-json-bridge",
            SignalSourceKind.MYLIAN_BRIDGE: "mylian-local-bridge",
        }[self.source_kind]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_kind": self.task_kind.value,
            "source_kind": self.source_kind.value,
            "sample_rate_hz": self.sample_rate_hz,
            "channel_names": list(self.channel_names),
            "duration_seconds": self.duration_seconds,
            "frequencies_hz": list(self.frequencies_hz),
            "decoder_name": self.decoder_name,
            "trial_count": self.trial_count,
            "seed": self.seed,
            "source_path": self.source_path,
            "model_path": self.model_path,
            "refresh_rate_hz": self.refresh_rate_hz,
            "screen_index": self.screen_index,
        }

    @classmethod
    def from_dict(cls, value: object) -> SignalTaskConfig:
        if not isinstance(value, dict) or value.get("schema_version") != "1.0":
            raise ValueError("Unsupported signal task configuration schema.")
        return cls(
            task_kind=SignalTaskKind(str(value["task_kind"])),
            source_kind=SignalSourceKind(str(value["source_kind"])),
            sample_rate_hz=float(value.get("sample_rate_hz", 250.0)),
            channel_names=tuple(str(item) for item in value.get("channel_names", [])),
            duration_seconds=float(value.get("duration_seconds", 2.0)),
            frequencies_hz=tuple(float(item) for item in value.get("frequencies_hz", [])),
            decoder_name=str(value.get("decoder_name", "fbcca")),
            trial_count=int(value.get("trial_count", 2)),
            seed=int(value.get("seed", 13)),
            source_path=(str(value["source_path"]) if value.get("source_path") else None),
            model_path=(str(value["model_path"]) if value.get("model_path") else None),
            refresh_rate_hz=float(value.get("refresh_rate_hz", 60.0)),
            screen_index=int(value.get("screen_index", 0)),
        )

    def write(self, path: str | Path) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(self.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary_path.replace(target)
        finally:
            temporary_path.unlink(missing_ok=True)
        return target

    @classmethod
    def read(cls, path: str | Path) -> SignalTaskConfig:
        target = Path(path).expanduser().resolve()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Signal task configuration is invalid: {target}") from error
        return cls.from_dict(payload)
