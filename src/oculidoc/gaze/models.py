"""Eye-gaze data models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GazeSample:
    """One timestamped gaze sample in screen-pixel coordinates."""

    timestamp_ns: int
    x_px: float
    y_px: float
    valid: bool
    source: str
