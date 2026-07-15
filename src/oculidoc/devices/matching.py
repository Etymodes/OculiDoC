"""Timestamp-domain-aware gaze-to-frame matching."""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from oculidoc.devices.contracts import (
    CameraFramePacket,
    EyeTrackerSample,
)


class TimestampBasis(StrEnum):
    """Clock domain used to compare acquisition packets."""

    SOURCE = "source"
    HOST = "host"


class MatchStatus(StrEnum):
    """Outcome of matching one frame to buffered gaze."""

    MATCHED = "matched"
    OUT_OF_TOLERANCE = "out_of_tolerance"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class GazeFrameMatch:
    """Result of matching a frame to its nearest gaze sample."""

    frame: CameraFramePacket
    sample: EyeTrackerSample | None
    status: MatchStatus
    timestamp_basis: TimestampBasis | None
    skew_ns: int | None
    max_skew_ns: int

    def __post_init__(self) -> None:
        if self.max_skew_ns < 0:
            raise ValueError("max_skew_ns cannot be negative.")

        if self.status is MatchStatus.EMPTY:
            if (
                self.sample is not None
                or self.timestamp_basis is not None
                or self.skew_ns is not None
            ):
                raise ValueError("An empty match cannot contain sample metadata.")

            return

        if self.sample is None or self.timestamp_basis is None or self.skew_ns is None:
            raise ValueError("A non-empty match requires sample metadata.")

        if self.skew_ns < 0:
            raise ValueError("skew_ns cannot be negative.")

        if self.status is MatchStatus.MATCHED and self.skew_ns > self.max_skew_ns:
            raise ValueError("A matched sample exceeds the tolerance.")

        if self.status is MatchStatus.OUT_OF_TOLERANCE and self.skew_ns <= self.max_skew_ns:
            raise ValueError("An out-of-tolerance sample is within tolerance.")

    @property
    def synchronized(self) -> bool:
        """Return whether the match satisfies the tolerance."""
        return self.status is MatchStatus.MATCHED

    def to_summary_dict(self) -> dict[str, object]:
        """Return metadata without serializing image pixels."""
        return {
            "frame_index": self.frame.frame_index,
            "frame_sequence": self.frame.timestamp.sequence,
            "gaze_sequence": (self.sample.timestamp.sequence if self.sample is not None else None),
            "gaze_valid": (self.sample.gaze_valid if self.sample is not None else None),
            "status": self.status.value,
            "timestamp_basis": (
                self.timestamp_basis.value if self.timestamp_basis is not None else None
            ),
            "skew_ns": self.skew_ns,
            "max_skew_ns": self.max_skew_ns,
            "synchronized": self.synchronized,
        }


class GazeSampleBuffer:
    """Fixed-capacity buffer of recent eye-tracker samples."""

    def __init__(self, capacity: int = 512) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive.")

        self._capacity = capacity
        self._samples: deque[EyeTrackerSample] = deque(maxlen=capacity)

    @property
    def capacity(self) -> int:
        """Return the maximum number of buffered samples."""
        return self._capacity

    @property
    def samples(self) -> tuple[EyeTrackerSample, ...]:
        """Return a stable snapshot of buffered samples."""
        return tuple(self._samples)

    def __len__(self) -> int:
        return len(self._samples)

    def clear(self) -> None:
        """Remove all buffered samples."""
        self._samples.clear()

    def add(self, sample: EyeTrackerSample) -> None:
        """Append a gaze sample and evict the oldest if full."""
        self._samples.append(sample)

    def extend(
        self,
        samples: Iterable[EyeTrackerSample],
    ) -> None:
        """Append multiple samples in acquisition order."""
        self._samples.extend(samples)

    def match_nearest(
        self,
        frame: CameraFramePacket,
        *,
        max_skew_ns: int,
    ) -> GazeFrameMatch:
        """Find the nearest buffered gaze sample."""
        if max_skew_ns < 0:
            raise ValueError("max_skew_ns cannot be negative.")

        if not self._samples:
            return GazeFrameMatch(
                frame=frame,
                sample=None,
                status=MatchStatus.EMPTY,
                timestamp_basis=None,
                skew_ns=None,
                max_skew_ns=max_skew_ns,
            )

        frame_timestamp = frame.timestamp
        frame_clock_id = frame_timestamp.source_clock_id

        source_candidates = [
            sample
            for sample in self._samples
            if (
                frame_clock_id is not None
                and sample.timestamp.source_clock_id == frame_clock_id
                and sample.timestamp.source_timestamp_ns is not None
            )
        ]

        if frame_timestamp.source_timestamp_ns is not None and source_candidates:
            timestamp_basis = TimestampBasis.SOURCE
            frame_time_ns = frame_timestamp.source_timestamp_ns
            candidates = source_candidates

            def sample_time_ns(
                sample: EyeTrackerSample,
            ) -> int:
                value = sample.timestamp.source_timestamp_ns

                if value is None:
                    raise RuntimeError("A source candidate lacks source time.")

                return value

        else:
            timestamp_basis = TimestampBasis.HOST
            frame_time_ns = frame_timestamp.monotonic_timestamp_ns
            candidates = list(self._samples)

            def sample_time_ns(
                sample: EyeTrackerSample,
            ) -> int:
                return sample.timestamp.monotonic_timestamp_ns

        nearest_sample = min(
            candidates,
            key=lambda sample: (
                abs(sample_time_ns(sample) - frame_time_ns),
                sample.timestamp.sequence,
            ),
        )

        skew_ns = abs(sample_time_ns(nearest_sample) - frame_time_ns)

        status = MatchStatus.MATCHED if skew_ns <= max_skew_ns else MatchStatus.OUT_OF_TOLERANCE

        return GazeFrameMatch(
            frame=frame,
            sample=nearest_sample,
            status=status,
            timestamp_basis=timestamp_basis,
            skew_ns=skew_ns,
            max_skew_ns=max_skew_ns,
        )
