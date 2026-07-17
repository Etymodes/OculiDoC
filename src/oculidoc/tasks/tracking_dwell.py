"""Dwell-state logic for moving gaze targets."""

from dataclasses import dataclass
from enum import StrEnum


class DwellPhase(StrEnum):
    """Visual phases of target fixation."""

    OUTSIDE = "outside"
    ACQUIRING = "acquiring"
    MAINTAINED = "maintained"


@dataclass(frozen=True, slots=True)
class DwellSnapshot:
    """Current moving-target fixation state."""

    phase: DwellPhase
    dwell_ms: float
    progress: float
    success_count: int


class TrackingDwellController:
    """Accumulate fixation with brief dropout tolerance."""

    def __init__(
        self,
        *,
        dwell_time_ms: int,
        dropout_grace_ms: int = 180,
    ) -> None:
        if not 100 <= dwell_time_ms <= 10_000:
            raise ValueError("dwell_time_ms must be between 100 and 10000.")

        if not 0 <= dropout_grace_ms <= 2_000:
            raise ValueError("dropout_grace_ms must be between 0 and 2000.")

        self.dwell_time_ms = dwell_time_ms
        self.dropout_grace_ms = dropout_grace_ms
        self.reset()

    def reset(self) -> None:
        self._phase = DwellPhase.OUTSIDE
        self._dwell_ms = 0.0
        self._outside_ms = 0.0
        self._last_timestamp_ns: int | None = None
        self._success_count = 0

    @property
    def snapshot(self) -> DwellSnapshot:
        return DwellSnapshot(
            phase=self._phase,
            dwell_ms=self._dwell_ms,
            progress=min(
                1.0,
                self._dwell_ms / self.dwell_time_ms,
            ),
            success_count=self._success_count,
        )

    def observe(
        self,
        inside_target: bool,
        timestamp_ns: int,
    ) -> DwellSnapshot:
        if timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative.")

        if self._last_timestamp_ns is None or timestamp_ns <= self._last_timestamp_ns:
            elapsed_ms = 0.0
        else:
            elapsed_ms = min(
                500.0,
                (timestamp_ns - self._last_timestamp_ns) / 1_000_000.0,
            )

        self._last_timestamp_ns = timestamp_ns

        if inside_target:
            self._outside_ms = 0.0

            if self._phase is DwellPhase.OUTSIDE:
                self._phase = DwellPhase.ACQUIRING
                self._dwell_ms = 0.0

            self._dwell_ms += elapsed_ms

            if self._phase is not DwellPhase.MAINTAINED and self._dwell_ms >= self.dwell_time_ms:
                self._phase = DwellPhase.MAINTAINED
                self._success_count += 1

            return self.snapshot

        if self._phase is DwellPhase.OUTSIDE:
            return self.snapshot

        self._outside_ms += elapsed_ms

        if self._outside_ms > self.dropout_grace_ms:
            self._phase = DwellPhase.OUTSIDE
            self._dwell_ms = 0.0
            self._outside_ms = 0.0

        return self.snapshot
