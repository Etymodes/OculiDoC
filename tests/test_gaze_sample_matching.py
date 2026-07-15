"""Timestamp-aware gaze buffer matching tests."""

from dataclasses import replace

import pytest

from oculidoc.devices import (
    GazeSampleBuffer,
    MatchStatus,
    SimulatedCameraDevice,
    SimulatedEyeTrackerDevice,
    TimestampBasis,
)


def create_packets():
    """Create one frame and four deterministic gaze samples."""
    camera = SimulatedCameraDevice()
    tracker = SimulatedEyeTrackerDevice(
        sample_rate_hz=120.0,
    )

    camera.connect()
    tracker.connect()
    camera.start_stream()
    tracker.start_stream()

    frame = camera.read_frame()
    samples = [tracker.read_sample() for _ in range(4)]

    camera.stop_stream()
    tracker.stop_stream()
    camera.disconnect()
    tracker.disconnect()

    return frame, samples


def test_buffer_evicts_oldest_sample() -> None:
    _, samples = create_packets()
    buffer = GazeSampleBuffer(capacity=2)

    buffer.extend(samples[:3])

    assert len(buffer) == 2
    assert [sample.timestamp.sequence for sample in buffer.samples] == [1, 2]


def test_match_uses_shared_source_clock() -> None:
    frame, samples = create_packets()
    buffer = GazeSampleBuffer()

    adjusted_samples = []

    for index, sample in enumerate(samples):
        timestamp = replace(
            sample.timestamp,
            source_timestamp_ns=(2_000_000 + index * 8_333_333),
        )
        adjusted_samples.append(
            replace(
                sample,
                timestamp=timestamp,
            )
        )

    buffer.extend(adjusted_samples)

    match = buffer.match_nearest(
        frame,
        max_skew_ns=5_000_000,
    )

    assert match.status is MatchStatus.MATCHED
    assert match.timestamp_basis is TimestampBasis.SOURCE
    assert match.sample is adjusted_samples[0]
    assert match.skew_ns == 2_000_000
    assert match.synchronized is True


def test_different_source_clocks_fall_back_to_host() -> None:
    frame, samples = create_packets()
    sample = samples[0]

    frame = replace(
        frame,
        timestamp=replace(
            frame.timestamp,
            monotonic_timestamp_ns=1_000_000,
            source_timestamp_ns=10_000_000,
            source_clock_id="camera-clock",
        ),
    )
    sample = replace(
        sample,
        timestamp=replace(
            sample.timestamp,
            monotonic_timestamp_ns=1_500_000,
            source_timestamp_ns=10_000_000,
            source_clock_id="tracker-clock",
        ),
    )

    buffer = GazeSampleBuffer()
    buffer.add(sample)

    match = buffer.match_nearest(
        frame,
        max_skew_ns=1_000_000,
    )

    assert match.status is MatchStatus.MATCHED
    assert match.timestamp_basis is TimestampBasis.HOST
    assert match.skew_ns == 500_000


def test_match_can_be_out_of_tolerance() -> None:
    frame, samples = create_packets()
    sample = samples[0]

    sample = replace(
        sample,
        timestamp=replace(
            sample.timestamp,
            source_timestamp_ns=20_000_000,
        ),
    )

    buffer = GazeSampleBuffer()
    buffer.add(sample)

    match = buffer.match_nearest(
        frame,
        max_skew_ns=5_000_000,
    )

    assert match.status is MatchStatus.OUT_OF_TOLERANCE
    assert match.skew_ns == 20_000_000
    assert match.synchronized is False


def test_empty_buffer_returns_empty_match() -> None:
    frame, _ = create_packets()
    buffer = GazeSampleBuffer()

    match = buffer.match_nearest(
        frame,
        max_skew_ns=5_000_000,
    )

    assert match.status is MatchStatus.EMPTY
    assert match.sample is None
    assert match.skew_ns is None
    assert match.timestamp_basis is None


def test_match_summary_excludes_image_pixels() -> None:
    frame, samples = create_packets()
    buffer = GazeSampleBuffer()
    buffer.add(samples[0])

    match = buffer.match_nearest(
        frame,
        max_skew_ns=5_000_000,
    )
    summary = match.to_summary_dict()

    assert summary["frame_index"] == 0
    assert summary["gaze_sequence"] == 0
    assert summary["gaze_valid"] is True
    assert "image" not in summary


def test_buffer_validates_configuration() -> None:
    with pytest.raises(
        ValueError,
        match="capacity",
    ):
        GazeSampleBuffer(capacity=0)

    frame, _ = create_packets()
    buffer = GazeSampleBuffer()

    with pytest.raises(
        ValueError,
        match="max_skew_ns",
    ):
        buffer.match_nearest(
            frame,
            max_skew_ns=-1,
        )
