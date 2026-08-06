"""Merge timestamped task, gaze, EEG, and device events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SynchronizationMethod(StrEnum):
    HARDWARE_TRIGGER = "hardware_trigger"
    LSL = "lsl"
    TIMESTAMP = "timestamp"
    SOFTWARE_ESTIMATE = "software_estimate"


@dataclass(frozen=True, slots=True)
class CoordinatedSignalEvent:
    timestamp_ns: int
    stream: str
    event_type: str
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("Coordinated event timestamp cannot be negative.")
        if not self.stream.strip() or not self.event_type.strip():
            raise ValueError("Coordinated event identity cannot be empty.")


class SignalCoordinator:
    """Keep one stable ordered multimodal event timeline."""

    def __init__(self, method: SynchronizationMethod | str) -> None:
        self.method = SynchronizationMethod(method)
        self._events: list[CoordinatedSignalEvent] = []

    @classmethod
    def for_available_methods(
        cls,
        methods: tuple[SynchronizationMethod | str, ...],
    ) -> SignalCoordinator:
        """Choose the strongest available method using the v0.1.3 policy."""
        available = {SynchronizationMethod(method) for method in methods}
        priority = (
            SynchronizationMethod.HARDWARE_TRIGGER,
            SynchronizationMethod.LSL,
            SynchronizationMethod.TIMESTAMP,
            SynchronizationMethod.SOFTWARE_ESTIMATE,
        )
        selected = next((method for method in priority if method in available), None)
        if selected is None:
            raise ValueError("At least one synchronization method is required.")
        return cls(selected)

    def add(self, event: CoordinatedSignalEvent) -> None:
        self._events.append(event)

    def timeline(self) -> tuple[CoordinatedSignalEvent, ...]:
        return tuple(sorted(self._events, key=lambda item: (item.timestamp_ns, item.stream)))
