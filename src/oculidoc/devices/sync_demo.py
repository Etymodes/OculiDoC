"""Run a paired simulated camera and gaze acquisition."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from oculidoc.devices.simulated import (
    SimulatedCameraDevice,
    SimulatedEyeTrackerDevice,
)
from oculidoc.devices.synchronization import (
    PairedAcquisitionRunner,
)


def build_parser() -> argparse.ArgumentParser:
    """Create the demonstration command parser."""
    parser = argparse.ArgumentParser(
        prog="python -m oculidoc.devices.sync_demo",
        description=("Collect paired simulated camera and gaze samples."),
    )
    parser.add_argument(
        "--pairs",
        type=int,
        default=10,
        help="Number of paired samples. Default: 10.",
    )
    parser.add_argument(
        "--rate-hz",
        type=float,
        default=30.0,
        help=("Shared simulated camera and gaze rate. Default: 30."),
    )
    parser.add_argument(
        "--width",
        type=int,
        default=320,
        help="Simulated image width. Default: 320.",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=240,
        help="Simulated image height. Default: 240.",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Pace the simulated streams in real time.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON metadata output path.",
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Run the paired acquisition demonstration."""
    args = build_parser().parse_args(argv)

    if args.pairs < 0:
        raise SystemExit("--pairs cannot be negative.")

    camera = SimulatedCameraDevice(
        width_px=args.width,
        height_px=args.height,
        fps=args.rate_hz,
        max_frames=args.pairs,
        realtime=args.realtime,
    )
    eye_tracker = SimulatedEyeTrackerDevice(
        sample_rate_hz=args.rate_hz,
        max_samples=args.pairs,
        realtime=args.realtime,
    )
    runner = PairedAcquisitionRunner(
        camera,
        eye_tracker,
    )

    packets = runner.collect(args.pairs)

    print("OculiDoC paired acquisition demo")
    print(f"Pairs: {len(packets)}")
    print(f"Rate: {args.rate_hz:.2f} Hz")
    print(f"Image: {args.width}x{args.height} BGR8")
    print("")

    for packet in packets:
        sample = packet.gaze_sample
        source_skew = (
            f"{packet.source_skew_ns / 1_000_000:.3f} ms"
            if packet.source_skew_ns is not None
            else "unavailable"
        )

        if sample.gaze_valid:
            gaze_text = f"({sample.gaze_x_normalized:.3f}, {sample.gaze_y_normalized:.3f})"
        else:
            gaze_text = "invalid"

        print(
            f"[{packet.pair_index:03d}] "
            f"frame={packet.camera_frame.frame_index:03d} "
            f"gaze={gaze_text} "
            f"host_skew="
            f"{packet.host_skew_ns / 1_000_000:.3f} ms "
            f"source_skew={source_skew}"
        )

    if args.output is not None:
        args.output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        payload = {
            "schema_version": "1.0",
            "pair_count": len(packets),
            "rate_hz": args.rate_hz,
            "image_width_px": args.width,
            "image_height_px": args.height,
            "pairs": [packet.to_summary_dict() for packet in packets],
        }
        args.output.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print("")
        print(f"Report: {args.output.resolve()}")

    print("")
    print(f"Devices disconnected: {runner.devices_disconnected}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
