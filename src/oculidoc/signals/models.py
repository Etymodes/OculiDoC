"""Device-neutral signal data contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

import numpy as np
from numpy.typing import NDArray


class SignalParadigm(StrEnum):
    """Interaction-level paradigms, independent from device vendors."""

    GAZE = "gaze"
    PASSIVE_EEG = "passive_eeg"
    SSVEP = "ssvep"
    MI = "mi"
    P300 = "p300"


class SignalSourceKind(StrEnum):
    """Supported EEG source families for v0.1.3."""

    SIMULATION = "simulation"
    REPLAY = "replay"
    LOCAL_BRIDGE = "local_bridge"
    MYLIAN_BRIDGE = "mylian_bridge"


@dataclass(frozen=True, slots=True)
class SignalMarker:
    """One timestamped task or stimulus marker."""

    name: str
    timestamp_ns: int
    value: str | float | int | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("Signal marker name cannot be empty.")
        if isinstance(self.timestamp_ns, bool) or self.timestamp_ns < 0:
            raise ValueError("Signal marker timestamp_ns cannot be negative.")
        object.__setattr__(self, "name", name)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "timestamp_ns": self.timestamp_ns,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> SignalMarker:
        if not isinstance(value, dict):
            raise TypeError("Signal marker must be an object.")
        return cls(
            name=str(value["name"]),
            timestamp_ns=int(value["timestamp_ns"]),
            value=value.get("value"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class EEGSampleBlock:
    """One contiguous channels-by-samples EEG block in microvolts."""

    device_id: str
    start_timestamp_ns: int
    sample_rate_hz: float
    channel_names: tuple[str, ...]
    values_uv: NDArray[np.float64]
    quality: Mapping[str, float] = field(default_factory=dict)
    markers: tuple[SignalMarker, ...] = ()
    simulated: bool = False

    def __post_init__(self) -> None:
        device_id = self.device_id.strip()
        channel_names = tuple(name.strip() for name in self.channel_names)
        values = np.asarray(self.values_uv, dtype=np.float64)

        if not device_id:
            raise ValueError("EEG device_id cannot be empty.")
        if isinstance(self.start_timestamp_ns, bool) or self.start_timestamp_ns < 0:
            raise ValueError("EEG start_timestamp_ns cannot be negative.")
        if not np.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("EEG sample_rate_hz must be positive and finite.")
        if not channel_names or any(not name for name in channel_names):
            raise ValueError("EEG channel_names cannot be empty.")
        if len(set(channel_names)) != len(channel_names):
            raise ValueError("EEG channel_names must be unique.")
        if values.ndim != 2:
            raise ValueError("EEG values_uv must have shape channels x samples.")
        if values.shape[0] != len(channel_names):
            raise ValueError("EEG values_uv channel count does not match channel_names.")
        if values.shape[1] == 0:
            raise ValueError("EEG values_uv must contain at least one sample.")

        normalized_quality: dict[str, float] = {}
        for name, raw_value in self.quality.items():
            channel_name = str(name).strip()
            quality = float(raw_value)
            if channel_name not in channel_names:
                raise ValueError(f"EEG quality references an unknown channel: {channel_name}")
            if not np.isfinite(quality) or not 0.0 <= quality <= 1.0:
                raise ValueError("EEG quality values must be finite values from 0 to 1.")
            normalized_quality[channel_name] = quality

        values = values.copy()
        values.setflags(write=False)
        object.__setattr__(self, "device_id", device_id)
        object.__setattr__(self, "channel_names", channel_names)
        object.__setattr__(self, "values_uv", values)
        object.__setattr__(self, "quality", MappingProxyType(normalized_quality))
        object.__setattr__(
            self, "markers", tuple(sorted(self.markers, key=lambda item: item.timestamp_ns))
        )

    @property
    def sample_count(self) -> int:
        return int(self.values_uv.shape[1])

    @property
    def duration_seconds(self) -> float:
        return self.sample_count / self.sample_rate_hz

    @property
    def end_timestamp_ns(self) -> int:
        return self.start_timestamp_ns + round(self.duration_seconds * 1_000_000_000)

    @property
    def valid_sample_ratio(self) -> float:
        return float(np.isfinite(self.values_uv).sum() / self.values_uv.size)

    def metadata(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "device_id": self.device_id,
            "start_timestamp_ns": self.start_timestamp_ns,
            "sample_rate_hz": self.sample_rate_hz,
            "channel_names": list(self.channel_names),
            "quality": dict(self.quality),
            "markers": [marker.to_dict() for marker in self.markers],
            "simulated": self.simulated,
            "sample_count": self.sample_count,
        }
