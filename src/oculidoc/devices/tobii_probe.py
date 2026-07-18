"""Command-line probe for Tobii Eye Tracker 5."""

from __future__ import annotations

import argparse
import sys
from time import monotonic, sleep

from oculidoc.devices.tobii_stream_engine import (
    TobiiStreamEngineDevice,
    discover_tobii_stream_engine_dll,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Probe a Tobii Eye Tracker 5 through the native Stream Engine.")
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=8.0,
    )
    parser.add_argument(
        "--dll",
        default=None,
    )
    args = parser.parse_args()

    print("=== OCULIDOC TOBII NATIVE PROBE ===")
    print(f"PYTHON={sys.version.split()[0]}")
    print(f"PLATFORM={sys.platform}")

    library_path = discover_tobii_stream_engine_dll(args.dll)

    print(f"STREAM_ENGINE_DLL={library_path or 'NOT_FOUND'}")

    if library_path is None:
        print("TOBII_NATIVE_PROBE=DLL_NOT_FOUND")
        return 2

    device = TobiiStreamEngineDevice(library_path=library_path)

    valid_count = 0
    invalid_count = 0
    sample_count = 0
    first_sample_printed = False

    try:
        device.connect()

        print(f"DEVICE_NAME={device.info.name}")
        print(f"DEVICE_URL={device.device_url}")

        device.start_stream()

        deadline = monotonic() + max(1.0, args.seconds)

        while monotonic() < deadline:
            try:
                sample = device.read_sample()
            except TimeoutError:
                sleep(0.002)
                continue

            sample_count += 1

            if sample.gaze_valid:
                valid_count += 1
            else:
                invalid_count += 1

            if not first_sample_printed:
                print(
                    "FIRST_SAMPLE="
                    f"x={sample.gaze_x_normalized},"
                    f"y={sample.gaze_y_normalized},"
                    f"valid={sample.gaze_valid}"
                )
                first_sample_printed = True
    except Exception as error:
        print(f"TOBII_NATIVE_ERROR={type(error).__name__}: {error}")
        print("TOBII_NATIVE_PROBE=FAIL")
        return 1
    finally:
        device.disconnect()

    print(f"SAMPLES={sample_count}")
    print(f"VALID={valid_count}")
    print(f"INVALID={invalid_count}")

    if valid_count <= 0:
        print("TOBII_NATIVE_PROBE=NO_VALID_GAZE")
        return 3

    print("TOBII_NATIVE_PROBE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
