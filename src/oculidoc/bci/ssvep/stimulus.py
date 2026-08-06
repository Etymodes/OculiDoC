"""Frame-indexed SSVEP luminance states."""

from __future__ import annotations

from math import pi, sin

from oculidoc.bci.ssvep.config import SsvepStimulusConfig


def target_luminance(
    config: SsvepStimulusConfig,
    target_index: int,
    frame_index: int,
) -> float:
    """Return a deterministic 0..1 luminance for one display frame."""
    if not 0 <= target_index < len(config.targets):
        raise IndexError("SSVEP target index is out of range.")
    if frame_index < 0:
        raise ValueError("frame_index cannot be negative.")
    target = config.targets[target_index]
    time_seconds = frame_index / config.refresh_rate_hz
    return 0.5 + 0.5 * sin(2.0 * pi * target.frequency_hz * time_seconds + target.phase_rad)


def frame_luminances(config: SsvepStimulusConfig, frame_index: int) -> tuple[float, ...]:
    return tuple(
        target_luminance(config, index, frame_index) for index in range(len(config.targets))
    )
