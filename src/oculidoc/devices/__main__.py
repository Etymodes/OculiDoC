"""Command-line device diagnostic report."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from oculidoc.devices.diagnostics import (
    build_diagnostic_report,
    write_diagnostic_report,
)


def _gibibytes(value: int) -> float:
    return value / (1024**3)


def build_parser() -> argparse.ArgumentParser:
    """Create the device diagnostic command parser."""
    parser = argparse.ArgumentParser(
        prog="python -m oculidoc.devices",
        description=("Inspect system resources and probe camera indices."),
    )
    parser.add_argument(
        "--max-camera-index",
        type=int,
        default=3,
        help="Highest camera index to probe. Default: 3.",
    )
    parser.add_argument(
        "--skip-cameras",
        action="store_true",
        help="Collect system information without opening cameras.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report output path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete report as JSON.",
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Run system and camera diagnostics."""
    args = build_parser().parse_args(argv)

    if args.max_camera_index < 0:
        raise SystemExit("--max-camera-index cannot be negative.")

    report = build_diagnostic_report(
        max_camera_index=args.max_camera_index,
        scan_cameras=not args.skip_cameras,
    )

    if args.output is not None:
        output_path = write_diagnostic_report(
            report,
            args.output,
        )
        print(f"Report: {output_path.resolve()}")

    if args.json:
        print(
            json.dumps(
                report.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    system = report.system

    print("OculiDoC device diagnostics")
    print(f"Host: {system.hostname}")
    print(f"OS: {system.operating_system} {system.operating_system_version}")
    print(f"Machine: {system.machine}")
    print(f"Processor: {system.processor}")
    print(f"Python: {system.python_version}")
    print(
        "CPU: "
        f"{system.physical_cpu_count or 'unknown'} physical / "
        f"{system.logical_cpu_count} logical"
    )
    print(
        "Memory: "
        f"{_gibibytes(system.memory_available_bytes):.1f} GiB "
        f"available / "
        f"{_gibibytes(system.memory_total_bytes):.1f} GiB total"
    )
    print(f"Disk free: {_gibibytes(system.disk_free_bytes):.1f} GiB")

    if system.nvidia_gpu_names:
        for gpu_name in system.nvidia_gpu_names:
            print(f"NVIDIA GPU: {gpu_name}")
    else:
        print("NVIDIA GPU: not detected by nvidia-smi")

    if args.skip_cameras:
        print("Camera scan: skipped")
        return 0

    print("Cameras:")

    for camera in report.cameras:
        dimensions = (
            f"{camera.width_px}x{camera.height_px}"
            if (camera.width_px is not None and camera.height_px is not None)
            else "unknown resolution"
        )
        fps = f"{camera.fps:.2f} FPS" if camera.fps is not None else "unknown FPS"

        print(
            f"  [{camera.index}] "
            f"{camera.status.value}; "
            f"{camera.backend or 'unknown backend'}; "
            f"{dimensions}; {fps}"
        )

        if camera.message:
            print(f"      {camera.message}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
