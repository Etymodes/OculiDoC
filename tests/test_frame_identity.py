"""Camera-frame identity and save-guard tests."""

from datetime import UTC, datetime

import numpy as np
import pytest

from oculidoc.devices import (
    CameraFramePacket,
    DeviceTimestamp,
)
from oculidoc.vision.frame_identity import (
    FrameSaveGuard,
    build_camera_frame_key,
)


def create_packet(
    *,
    frame_index: int = 12,
    monotonic_timestamp_ns: int = 1_000_000,
) -> CameraFramePacket:
    """Create one deterministic frame packet."""
    return CameraFramePacket(
        timestamp=DeviceTimestamp(
            sequence=frame_index,
            monotonic_timestamp_ns=(monotonic_timestamp_ns),
            utc_timestamp=datetime(
                2026,
                7,
                15,
                12,
                0,
                0,
                tzinfo=UTC,
            ),
        ),
        frame_index=frame_index,
        image=np.zeros(
            (48, 64, 3),
            dtype=np.uint8,
        ),
    )


def test_same_packet_has_same_frame_key() -> None:
    packet = create_packet()

    first = build_camera_frame_key(
        packet=packet,
        camera_index=0,
        backend_name="DSHOW",
    )
    second = build_camera_frame_key(
        packet=packet,
        camera_index=0,
        backend_name="dshow",
    )

    assert first == second
    assert len(first) == 64


def test_different_frame_has_different_key() -> None:
    first = build_camera_frame_key(
        packet=create_packet(frame_index=12),
        camera_index=0,
        backend_name="DSHOW",
    )
    second = build_camera_frame_key(
        packet=create_packet(frame_index=13),
        camera_index=0,
        backend_name="DSHOW",
    )

    assert first != second


def test_save_guard_blocks_only_marked_frames() -> None:
    guard = FrameSaveGuard()

    assert guard.was_saved("frame-a") is False

    guard.mark_saved("frame-a")

    assert guard.was_saved("frame-a") is True
    assert guard.was_saved("frame-b") is False

    guard.clear()

    assert guard.was_saved("frame-a") is False


@pytest.mark.parametrize(
    "frame_key",
    ["", "   "],
)
def test_save_guard_rejects_empty_keys(
    frame_key: str,
) -> None:
    guard = FrameSaveGuard()

    with pytest.raises(ValueError):
        guard.mark_saved(frame_key)
