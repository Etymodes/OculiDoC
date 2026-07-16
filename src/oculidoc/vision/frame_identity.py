"""Stable identities and duplicate-save guards for camera frames."""

from dataclasses import dataclass, field
from datetime import UTC
from hashlib import sha256

from oculidoc.devices.contracts import (
    CameraFramePacket,
)


def build_camera_frame_key(
    *,
    packet: CameraFramePacket,
    camera_index: int,
    backend_name: str | None,
) -> str:
    """Build a deterministic identity for one captured frame."""
    if camera_index < 0:
        raise ValueError("camera_index cannot be negative.")

    timestamp = packet.timestamp
    backend = backend_name.strip().upper() if backend_name else "UNKNOWN"
    utc_timestamp = timestamp.utc_timestamp.astimezone(UTC).isoformat()

    payload = "|".join(
        (
            "oculidoc-camera-frame-v1",
            str(camera_index),
            backend,
            str(packet.frame_index),
            str(timestamp.sequence),
            str(timestamp.monotonic_timestamp_ns),
            utc_timestamp,
        )
    )

    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class FrameSaveGuard:
    """Remember frames successfully saved during this session."""

    _saved_keys: set[str] = field(default_factory=set)

    def was_saved(
        self,
        frame_key: str,
    ) -> bool:
        """Return whether a frame has already been saved."""
        normalized = frame_key.strip()

        if not normalized:
            raise ValueError("frame_key cannot be empty.")

        return normalized in self._saved_keys

    def mark_saved(
        self,
        frame_key: str,
    ) -> None:
        """Mark a frame only after all artifacts are written."""
        normalized = frame_key.strip()

        if not normalized:
            raise ValueError("frame_key cannot be empty.")

        self._saved_keys.add(normalized)

    def clear(self) -> None:
        """Clear saved-frame history."""
        self._saved_keys.clear()
