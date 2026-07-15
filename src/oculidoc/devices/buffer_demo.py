"""Demonstrate buffered gaze-to-frame matching."""

import argparse
from collections.abc import Sequence
from dataclasses import replace
from math import isclose

from oculidoc.devices.coordinator import DeviceCoordinator
from oculidoc.devices.matching import GazeSampleBuffer
from oculidoc.devices.simulated import (
    SimulatedCameraDevice,
    SimulatedEyeTrackerDevice,
)


def build_parser() -> argparse.ArgumentParser:
    """Create the buffered matching demonstration parser."""
    parser = argparse.ArgumentParser(
        prog="python -m oculidoc.devices.buffer_demo",
        description=("Match high-rate gaze samples to lower-rate frames."),
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--camera-rate-hz",
        type=float,
        default=30.0,
    )
    parser.add_argument(
        "--gaze-rate-hz",
        type=float,
        default=120.0,
    )
    parser.add_argument(
        "--offset-ms",
        type=float,
        default=2.0,
    )
    parser.add_argument(
        "--jitter-ms",
        type=float,
        default=0.5,
    )
    parser.add_argument(
        "--max-skew-ms",
        type=float,
        default=5.0,
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Run deterministic multi-rate timestamp matching."""
    args = build_parser().parse_args(argv)

    if args.frames < 0:
        raise SystemExit("--frames cannot be negative.")

    if args.camera_rate_hz <= 0 or args.gaze_rate_hz <= 0:
        raise SystemExit("Sampling rates must be positive.")

    if args.jitter_ms < 0:
        raise SystemExit("--jitter-ms cannot be negative.")

    if args.max_skew_ms < 0:
        raise SystemExit("--max-skew-ms cannot be negative.")

    sampling_ratio = args.gaze_rate_hz / args.camera_rate_hz
    samples_per_frame = round(sampling_ratio)

    if not isclose(
        sampling_ratio,
        samples_per_frame,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise SystemExit("The demonstration requires an integer gaze-to-camera rate ratio.")

    camera = SimulatedCameraDevice(
        fps=args.camera_rate_hz,
        max_frames=args.frames,
    )
    tracker = SimulatedEyeTrackerDevice(
        sample_rate_hz=args.gaze_rate_hz,
        max_samples=(args.frames * samples_per_frame),
    )
    coordinator = DeviceCoordinator([camera, tracker])
    buffer = GazeSampleBuffer(
        capacity=max(
            32,
            samples_per_frame * 8,
        )
    )

    offset_ns = round(args.offset_ms * 1_000_000)
    jitter_ns = round(args.jitter_ms * 1_000_000)
    max_skew_ns = round(args.max_skew_ms * 1_000_000)

    print("OculiDoC buffered timestamp matching")
    print(f"Camera: {args.camera_rate_hz:.1f} Hz")
    print(f"Eye tracker: {args.gaze_rate_hz:.1f} Hz")
    print(f"Samples per frame: {samples_per_frame}")
    print(f"Eye offset: {args.offset_ms:.3f} ms")
    print(f"Eye jitter: +/-{args.jitter_ms:.3f} ms")
    print(f"Tolerance: {args.max_skew_ms:.3f} ms")
    print("")

    matches = []

    try:
        coordinator.connect_and_start()

        for frame_number in range(args.frames):
            frame = camera.read_frame()

            frame_jitter_ns = jitter_ns if frame_number % 2 == 0 else -jitter_ns

            for _ in range(samples_per_frame):
                sample = tracker.read_sample()
                source_time_ns = sample.timestamp.source_timestamp_ns

                if source_time_ns is None:
                    raise RuntimeError("Simulated gaze lacks source time.")

                adjusted_time_ns = source_time_ns + offset_ns + frame_jitter_ns

                if adjusted_time_ns < 0:
                    raise RuntimeError("Adjusted source time became negative.")

                adjusted_timestamp = replace(
                    sample.timestamp,
                    source_timestamp_ns=(adjusted_time_ns),
                )
                adjusted_sample = replace(
                    sample,
                    timestamp=adjusted_timestamp,
                )
                buffer.add(adjusted_sample)

            match = buffer.match_nearest(
                frame,
                max_skew_ns=max_skew_ns,
            )
            matches.append(match)

            skew_ms = match.skew_ns / 1_000_000 if match.skew_ns is not None else float("nan")
            gaze_sequence = match.sample.timestamp.sequence if match.sample is not None else None
            basis = match.timestamp_basis.value if match.timestamp_basis is not None else "none"

            print(
                f"frame={frame.frame_index:03d} "
                f"gaze={gaze_sequence!s:>3} "
                f"basis={basis:<6} "
                f"skew={skew_ms:.3f} ms "
                f"status={match.status.value}"
            )
    finally:
        if not coordinator.all_disconnected:
            coordinator.stop_and_disconnect()

    synchronized_count = sum(match.synchronized for match in matches)
    skews_ms = [match.skew_ns / 1_000_000 for match in matches if match.skew_ns is not None]

    print("")
    print(f"Synchronized: {synchronized_count}/{len(matches)}")

    if skews_ms:
        print(f"Skew range: {min(skews_ms):.3f}-{max(skews_ms):.3f} ms")

    print(f"Devices disconnected: {coordinator.all_disconnected}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
