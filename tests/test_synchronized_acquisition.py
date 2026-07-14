"""Paired camera and gaze acquisition tests."""

from datetime import UTC, datetime

import pytest

from oculidoc.devices import (
    DeviceState,
    DeviceStreamEndedError,
    PairedAcquisitionPacket,
    PairedAcquisitionRunner,
    SimulatedCameraDevice,
    SimulatedEyeTrackerDevice,
)


def build_runner(
    *,
    camera_frames: int | None = None,
    gaze_samples: int | None = None,
) -> PairedAcquisitionRunner:
    """Create equal-rate simulated acquisition devices."""
    camera = SimulatedCameraDevice(
        width_px=64,
        height_px=48,
        fps=30.0,
        max_frames=camera_frames,
    )
    tracker = SimulatedEyeTrackerDevice(
        sample_rate_hz=30.0,
        max_samples=gaze_samples,
    )

    return PairedAcquisitionRunner(
        camera,
        tracker,
    )


def test_runner_collects_timestamped_pairs() -> None:
    runner = build_runner(
        camera_frames=3,
        gaze_samples=3,
    )

    packets = runner.collect(3)

    assert len(packets) == 3
    assert [packet.pair_index for packet in packets] == [0, 1, 2]
    assert [packet.camera_frame.frame_index for packet in packets] == [0, 1, 2]
    assert [packet.gaze_sample.timestamp.sequence for packet in packets] == [0, 1, 2]

    assert all(packet.host_skew_ns >= 0 for packet in packets)
    assert all(packet.source_skew_ns == 0 for packet in packets)
    assert runner.devices_disconnected is True


def test_zero_pairs_does_not_touch_devices() -> None:
    runner = build_runner()

    packets = runner.collect(0)

    assert packets == ()
    assert runner.camera.state is DeviceState.DISCONNECTED
    assert runner.eye_tracker.state is DeviceState.DISCONNECTED


def test_runner_rejects_negative_pair_count() -> None:
    runner = build_runner()

    with pytest.raises(
        ValueError,
        match="pair_count",
    ):
        runner.collect(-1)


def test_runner_cleans_up_when_stream_ends() -> None:
    runner = build_runner(
        camera_frames=1,
        gaze_samples=10,
    )

    with pytest.raises(
        DeviceStreamEndedError,
    ):
        runner.collect(2)

    assert runner.devices_disconnected is True


def test_pair_summary_does_not_include_image_pixels() -> None:
    runner = build_runner(
        camera_frames=1,
        gaze_samples=1,
    )

    packet = runner.collect(1)[0]
    summary = packet.to_summary_dict()

    assert summary["pair_index"] == 0
    assert summary["camera"]["width_px"] == 64
    assert summary["camera"]["height_px"] == 48
    assert summary["gaze"]["valid"] is True
    assert "image" not in summary["camera"]


def test_pair_validates_index_and_timestamp() -> None:
    runner = build_runner(
        camera_frames=1,
        gaze_samples=1,
    )
    packet = runner.collect(1)[0]

    with pytest.raises(
        ValueError,
        match="pair_index",
    ):
        PairedAcquisitionPacket(
            pair_index=-1,
            camera_frame=packet.camera_frame,
            gaze_sample=packet.gaze_sample,
        )

    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        PairedAcquisitionPacket(
            pair_index=0,
            camera_frame=packet.camera_frame,
            gaze_sample=packet.gaze_sample,
            paired_at=datetime(
                2026,
                7,
                14,
            ),
        )

    normalized = PairedAcquisitionPacket(
        pair_index=0,
        camera_frame=packet.camera_frame,
        gaze_sample=packet.gaze_sample,
        paired_at=datetime(
            2026,
            7,
            14,
            12,
            0,
            tzinfo=UTC,
        ),
    )

    assert normalized.paired_at.tzinfo is UTC
