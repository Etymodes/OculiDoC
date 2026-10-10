"""Standalone optional-provider process; stdout is reserved for feature packets.

Run with the provider's own Python, without importing the OculiDoC environment.
Camera images stay in memory. Calibration and task recording belong to the parent.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from time import monotonic_ns, sleep
from typing import Any


def create_extractor(provider: str, config: dict[str, Any], stack: ExitStack) -> Any:
    """Load OculiDoC's independent estimator in the isolated vision environment."""
    if provider != "acies":
        raise ValueError("摄像头引擎已升级为 ACIES，请安装 ACIES 并重新校准。")
    if __package__:
        from oculidoc.devices.acies import AciesExtractor
    else:
        # The frozen application also ships both standalone source files together.
        from acies import AciesExtractor  # type: ignore[no-redef,import-not-found]

    estimator = AciesExtractor(config["face_model"])
    stack.callback(estimator.close)
    return estimator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    output = sys.stdout
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        with redirect_stdout(sys.stderr), ExitStack() as stack:
            import cv2
            import numpy as np

            extract = create_extractor(args.provider, config["providers"][args.provider], stack)
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
            camera = cv2.VideoCapture(config.get("camera_index", 0), backend)
            stack.callback(camera.release)
            if not camera.isOpened():
                raise RuntimeError("摄像头无法打开：检查权限、编号和其他占用程序。")
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, config.get("camera_width", 1280))
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, config.get("camera_height", 720))
            sequence = 0
            while True:
                success, frame = camera.read()
                timestamp = monotonic_ns()
                if not success:
                    raise RuntimeError("摄像头已断开或无法读取帧。")
                observation = extract(frame)
                features = observation["features"]
                if features is not None:
                    features = np.asarray(features, dtype=float).reshape(-1)
                    if (
                        features.size == 0
                        or features.size > 2048
                        or not np.isfinite(features).all()
                    ):
                        features = None
                output.write(
                    json.dumps(
                        {
                            **observation,
                            "sequence": sequence,
                            "timestamp_ns": timestamp,
                            "features": None if features is None else features.tolist(),
                        },
                        allow_nan=False,
                    )
                    + "\n"
                )
                output.flush()
                sequence += 1
                sleep(0.005)
    except Exception as error:
        output.write(json.dumps({"error": f"{type(error).__name__}: {error}"}) + "\n")
        output.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
