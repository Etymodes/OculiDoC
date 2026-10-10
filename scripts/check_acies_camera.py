"""Opt-in real-camera connectivity check; save counters only, never image/feature data."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from time import monotonic

from oculidoc.devices.acies import ACIES_VERSION
from oculidoc.devices.webcam_gaze import WebcamFeatureProcess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-camera", action="store_true", required=True)
    args = parser.parse_args()
    process = WebcamFeatureProcess(args.config, "acies", args.runtime)
    modes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    summary: dict[str, object] = {
        "algorithm": "ACIES",
        "version": ACIES_VERSION,
        "images_saved": False,
        "features_saved": False,
        "calibration": "NOT_RUN_REQUIRES_PARTICIPANT",
        "frames": 0,
    }
    qualities = []
    head_pose_frames = 0
    eye_details: list[float] = []
    glare_ratios: list[float] = []
    start = monotonic()
    result = 1
    try:
        process.start()
        while monotonic() - start < 25 and sum(modes.values()) < 60:
            try:
                packet = process.read()
            except TimeoutError:
                continue
            modes[packet["mode"]] += 1
            reasons[packet["reason"]] += 1
            qualities.append(packet["confidence"])
            if packet["features"] is not None and packet["features"][15] == 1:
                head_pose_frames += 1
            for eye in packet.get("eye_diagnostics", {}).values():
                if eye.get("valid"):
                    eye_details.append(eye.get("image_detail", 0))
                    glare_ratios.append(eye.get("glare_ratio", 0))
            summary["frame_size"] = packet.get("frame_size")
        count = sum(modes.values())
        summary.update(
            frames=count,
            modes=dict(modes),
            reasons=dict(reasons),
            mean_signal_quality=sum(qualities) / count if count else 0,
            head_pose_frames=head_pose_frames,
            mean_eye_image_detail=sum(eye_details) / len(eye_details) if eye_details else None,
            mean_eye_glare_ratio=sum(glare_ratios) / len(glare_ratios) if glare_ratios else None,
            screen_focus="unknown",
            status="FRAMES_OBSERVED" if count else "NO_FRAMES",
        )
        result = 0 if count else 1
    except Exception as error:
        summary.update(status="FAILED", error=str(error))
    finally:
        process.close()
        summary.update(
            process_released=process.process is None, elapsed_seconds=round(monotonic() - start, 3)
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
