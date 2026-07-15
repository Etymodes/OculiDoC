"""Capture frames from a real OpenCV camera."""

import argparse
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter

import cv2

from oculidoc.devices.opencv_camera import (
    OpenCVCameraDevice,
)

_BACKENDS = {
    "auto": None,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
}


def build_parser() -> argparse.ArgumentParser:
    """Create the real-camera demonstration parser."""
    parser = argparse.ArgumentParser(
        prog="python -m oculidoc.devices.camera_demo",
        description=("Capture timestamped frames from an OpenCV camera."),
    )
    parser.add_argument(
        "--index",
        type=int,
        default=0,
    )
    parser.add_argument(
        "--backend",
        choices=tuple(_BACKENDS),
        default="dshow",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=30,
    )
    parser.add_argument(
        "--width",
        type=int,
    )
    parser.add_argument(
        "--height",
        type=int,
    )
    parser.add_argument(
        "--fps",
        type=float,
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Optional path for the final captured frame.",
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Capture a finite set of real camera frames."""
    args = build_parser().parse_args(argv)

    if args.frames <= 0:
        raise SystemExit("--frames must be positive.")

    camera = OpenCVCameraDevice(
        index=args.index,
        backend=_BACKENDS[args.backend],
        requested_width_px=args.width,
        requested_height_px=args.height,
        requested_fps=args.fps,
    )

    packets = []
    started_at = perf_counter()

    try:
        camera.connect()

        print("OculiDoC OpenCV camera demo")
        print(f"Index: {camera.index}")
        print(f"Backend: {camera.backend_name or 'unknown'}")
        print(
            "Reported mode: "
            f"{camera.actual_width_px or 'unknown'}x"
            f"{camera.actual_height_px or 'unknown'}; "
            f"{camera.actual_fps or 'unknown'} FPS"
        )

        camera.start_stream()

        for _ in range(args.frames):
            packets.append(camera.read_frame())
    finally:
        if camera.state.value == "streaming":
            camera.stop_stream()

        if camera.state.value == "connected":
            camera.disconnect()

    elapsed_seconds = perf_counter() - started_at
    effective_fps = len(packets) / elapsed_seconds if elapsed_seconds > 0 else 0.0

    print(f"Captured: {len(packets)} frame(s)")
    print(f"Elapsed: {elapsed_seconds:.3f} seconds")
    print(f"Effective rate: {effective_fps:.2f} FPS")

    if packets:
        final_packet = packets[-1]

        print(
            "Final frame: "
            f"{final_packet.width_px}x"
            f"{final_packet.height_px}; "
            f"sequence={final_packet.timestamp.sequence}"
        )

        if args.snapshot is not None:
            args.snapshot.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            write_ok = cv2.imwrite(
                str(args.snapshot),
                final_packet.image,
            )

            if not write_ok:
                raise RuntimeError("OpenCV could not save the snapshot.")

            print(f"Snapshot: {args.snapshot.resolve()}")

    print(f"Camera disconnected: {camera.state.value == 'disconnected'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
