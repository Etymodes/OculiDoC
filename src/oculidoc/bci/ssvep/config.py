"""Reproducible SSVEP stimulus configuration."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, pi, sqrt


@dataclass(frozen=True, slots=True)
class SsvepTarget:
    target_id: str
    label: str
    frequency_hz: float
    phase_rad: float
    position_x: float
    position_y: float

    def __post_init__(self) -> None:
        if not self.target_id.strip() or not self.label.strip():
            raise ValueError("SSVEP target identity cannot be empty.")
        if self.frequency_hz <= 0:
            raise ValueError("SSVEP target frequency must be positive.")
        if not 0.0 <= self.position_x <= 1.0 or not 0.0 <= self.position_y <= 1.0:
            raise ValueError("SSVEP target positions must be normalized.")

    def to_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "label": self.label,
            "frequency_hz": self.frequency_hz,
            "phase_rad": self.phase_rad,
            "position_x": self.position_x,
            "position_y": self.position_y,
        }

    @classmethod
    def from_dict(cls, value: object) -> SsvepTarget:
        if not isinstance(value, dict):
            raise TypeError("SSVEP target must be an object.")
        return cls(
            target_id=str(value["target_id"]),
            label=str(value["label"]),
            frequency_hz=float(value["frequency_hz"]),
            phase_rad=float(value["phase_rad"]),
            position_x=float(value["position_x"]),
            position_y=float(value["position_y"]),
        )


def _positions(count: int) -> tuple[tuple[float, float], ...]:
    if count < 1:
        return ()
    if count == 1:
        return ((0.5, 0.5),)
    if count == 2:
        return ((0.25, 0.5), (0.75, 0.5))
    if count == 4:
        return ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
    columns = ceil(sqrt(count))
    rows = ceil(count / columns)
    return tuple(
        ((column + 1) / (columns + 1), (row + 1) / (rows + 1))
        for row in range(rows)
        for column in range(columns)
    )[:count]


@dataclass(frozen=True, slots=True)
class SsvepStimulusConfig:
    """Every display and decoder parameter required to reproduce a run."""

    targets: tuple[SsvepTarget, ...]
    refresh_rate_hz: float = 60.0
    screen_index: int = 0
    marker_prefix: str = "ssvep"
    harmonics: int = 3
    window_seconds: float = 2.0
    delay_seconds: float = 0.14

    def __post_init__(self) -> None:
        if not self.targets:
            raise ValueError("SSVEP stimulus requires at least one target.")
        ids = tuple(target.target_id for target in self.targets)
        frequencies = tuple(target.frequency_hz for target in self.targets)
        if len(set(ids)) != len(ids) or len(set(frequencies)) != len(frequencies):
            raise ValueError("SSVEP target IDs and frequencies must be unique.")
        if self.refresh_rate_hz <= 0:
            raise ValueError("SSVEP refresh_rate_hz must be positive.")
        if max(frequencies) >= self.refresh_rate_hz / 2:
            raise ValueError("SSVEP frequencies must remain below half the refresh rate.")
        if self.screen_index < 0 or self.harmonics < 1:
            raise ValueError("SSVEP screen_index and harmonics are invalid.")
        if self.window_seconds <= 0 or self.delay_seconds < 0:
            raise ValueError("SSVEP window and delay must be non-negative with a positive window.")
        if not self.marker_prefix.strip():
            raise ValueError("SSVEP marker_prefix cannot be empty.")

    @property
    def frequencies_hz(self) -> tuple[float, ...]:
        return tuple(target.frequency_hz for target in self.targets)

    @property
    def phases_rad(self) -> tuple[float, ...]:
        return tuple(target.phase_rad for target in self.targets)

    @classmethod
    def for_frequencies(
        cls,
        frequencies_hz: tuple[float, ...],
        *,
        labels: tuple[str, ...] | None = None,
        refresh_rate_hz: float = 60.0,
        screen_index: int = 0,
        harmonics: int = 3,
        window_seconds: float = 2.0,
        delay_seconds: float = 0.14,
    ) -> SsvepStimulusConfig:
        if labels is not None and len(labels) != len(frequencies_hz):
            raise ValueError("SSVEP labels must match the frequency count.")
        positions = _positions(len(frequencies_hz))
        targets = tuple(
            SsvepTarget(
                target_id=f"target-{index + 1}",
                label=(labels[index] if labels is not None else f"{frequency:g} Hz"),
                frequency_hz=float(frequency),
                phase_rad=index * pi / 2.0,
                position_x=positions[index][0],
                position_y=positions[index][1],
            )
            for index, frequency in enumerate(frequencies_hz)
        )
        return cls(
            targets=targets,
            refresh_rate_hz=refresh_rate_hz,
            screen_index=screen_index,
            harmonics=harmonics,
            window_seconds=window_seconds,
            delay_seconds=delay_seconds,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "targets": [target.to_dict() for target in self.targets],
            "refresh_rate_hz": self.refresh_rate_hz,
            "screen_index": self.screen_index,
            "marker_prefix": self.marker_prefix,
            "harmonics": self.harmonics,
            "window_seconds": self.window_seconds,
            "delay_seconds": self.delay_seconds,
        }

    @classmethod
    def from_dict(cls, value: object) -> SsvepStimulusConfig:
        if not isinstance(value, dict) or not isinstance(value.get("targets"), list):
            raise TypeError("SSVEP stimulus configuration must contain targets.")
        return cls(
            targets=tuple(SsvepTarget.from_dict(item) for item in value["targets"]),
            refresh_rate_hz=float(value.get("refresh_rate_hz", 60.0)),
            screen_index=int(value.get("screen_index", 0)),
            marker_prefix=str(value.get("marker_prefix", "ssvep")),
            harmonics=int(value.get("harmonics", 3)),
            window_seconds=float(value.get("window_seconds", 2.0)),
            delay_seconds=float(value.get("delay_seconds", 0.14)),
        )
