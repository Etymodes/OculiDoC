"""Reproducible, read-only gaze experiments and paired filter sensitivity checks."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from oculidoc.devices.acies import (
    CALIBRATION_FEATURE_VERSION,
    COMPATIBLE_MODEL_VERSIONS,
    FEATURE_COUNT,
    GazeSmoother,
    validate_observation_model,
)
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.webcam_gaze import load_calibration, predict_calibration

CONDITIONS = {
    "baseline": "正常注视：自然坐姿，看蓝点",
    "head_motion": "自然头动：看着蓝点，舒适范围轻微转头或点头",
    "blink": "眨眼测试：看蓝点，变成橙色时自然眨眼一次",
}
# New edge locations; center is intentionally repeated in a fresh recording.
TEST_POINTS = (
    (0.5, 0.5),
    (0.07, 0.07),
    (0.93, 0.07),
    (0.07, 0.93),
    (0.93, 0.93),
    (0.07, 0.5),
    (0.93, 0.5),
    (0.5, 0.07),
    (0.5, 0.93),
)
FILTER_BASELINE = {
    "min_cutoff": 0.45,
    "speed_gain": 4.0,
    "derivative_cutoff": 1.0,
    "median_window": 3,
}


def _validate_mapping(model: dict[str, Any]) -> None:
    eyes = model.get("eye_models", {})
    if not isinstance(eyes, dict) or set(eyes) - {"left", "right"}:
        raise ValueError("Invalid experiment eyes")
    for item in [model, *eyes.values()]:
        if not isinstance(item, dict) or len(item["mean"]) != FEATURE_COUNT:
            raise ValueError("Invalid experiment mapping")
        predict_calibration(item, item["mean"])
        validate_observation_model(item)


def load_paired_candidate(runtime: Path, original: dict[str, Any]) -> dict[str, Any] | None:
    """An explicitly staged research model, bound to the frozen baseline hash."""
    path = runtime / "experiment_candidates" / f"{original['identity'][:24]}.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    fingerprint = hashlib.sha256(
        json.dumps(original, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    if value["baseline_sha256"] != fingerprint:
        return None  # A later calibration starts a different comparison.
    model = value["model"]
    if (
        model.get("validated") is not False
        or model.get("task_eligible") is not False
        or model.get("identity") != original["identity"]
        or model.get("feature_version") != CALIBRATION_FEATURE_VERSION
        or model.get("algorithm_version") not in COMPATIBLE_MODEL_VERSIONS
    ):
        raise ValueError("Invalid paired candidate provenance")
    _validate_mapping(model)
    return value


def load_experiment_model(runtime: Path, identity: str, profile: str) -> dict[str, Any]:
    """Read a validated model or a clearly marked local diagnostic candidate.

    This function is exclusive to experiments. Task acquisition still uses the
    strict loader and never reads candidates. Neither path writes a calibration.
    """
    try:
        return {**load_calibration(runtime, identity, profile), "experiment_source": "validated"}
    except DeviceConnectionError:
        pass
    files = sorted(
        (runtime / "calibration_candidates").glob(f"{identity[:24]}-*.json"), reverse=True
    )
    for path in files:
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            metrics = candidate["validation_metrics"]
            model = {**candidate["model"], **metrics}
            age = (
                datetime.now(UTC) - datetime.fromisoformat(metrics["created_at"])
            ).total_seconds()
            if (
                metrics["identity"] != identity
                or not 0 <= age < 8 * 3600
                or metrics["algorithm_version"] not in COMPATIBLE_MODEL_VERSIONS
                or model.get("feature_version", CALIBRATION_FEATURE_VERSION)
                != CALIBRATION_FEATURE_VERSION
            ):
                continue
            _validate_mapping(model)
            return {
                **model,
                "validated": False,
                "experiment_source": "candidate",
                "candidate_file": path.name,
                "task_eligible": False,
            }
        except (OSError, ValueError, TypeError, KeyError):
            continue
    raise DeviceConnectionError(
        "还没有可供对照的完整校准，请先完成一轮校准；未通过也可研究其误差。"
    )


def experiment_plan(
    seed: int, round_index: int = 0, *, short: bool = False
) -> list[dict[str, Any]]:
    if type(seed) is not int or type(round_index) is not int or round_index < 0:
        raise ValueError("Invalid experiment seed or round")
    order = list(CONDITIONS)
    random.Random(seed).shuffle(order)
    # Across three rounds each condition occupies each temporal position once.
    rotation = round_index % len(order)
    order = ["baseline"] if short else order[rotation:] + order[:rotation]
    rng = random.Random(f"{seed}:{round_index}")
    result: list[dict[str, Any]] = []
    for condition in order:
        points = list(TEST_POINTS)
        rng.shuffle(points)
        result.extend(
            {"condition": condition, "target": point, "duration_s": 4.0, "settle_s": 1.0}
            for point in points
        )
    return result


def prediction_error(
    prediction: Any, target: Any, screen_size: tuple[int, int] | None = None
) -> dict[str, Any] | None:
    """Prediction minus intended target; positive is right/down, never ground truth."""
    if prediction is None:
        return None
    delta = np.asarray(prediction, dtype=float) - np.asarray(target, dtype=float)
    if delta.shape != (2,) or not np.isfinite(delta).all():
        raise ValueError("Invalid gaze error coordinates")
    result = {
        "dx": float(delta[0]),
        "dy": float(delta[1]),
        "distance": float(np.linalg.norm(delta)),
    }
    if screen_size is not None:
        if len(screen_size) != 2 or any(type(v) is not int or v <= 0 for v in screen_size):
            raise ValueError("Invalid screen size")
        pixels = delta * screen_size
        result.update(
            dx_px=float(pixels[0]),
            dy_px=float(pixels[1]),
            distance_px=float(np.linalg.norm(pixels)),
        )
    return result


def write_error_table(
    path: Path,
    plan: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    screen_size: tuple[int, int],
) -> None:
    """Human-readable numerical evidence, with blank cells for missing predictions."""
    fields = [
        "trial",
        "condition",
        "timestamp_ns",
        "elapsed_s",
        "target_x",
        "target_y",
        "target_x_px",
        "target_y_px",
    ]
    sources = {
        "raw": "raw_prediction",
        "filtered": "filtered_prediction",
        "candidate": "candidate_raw_prediction",
        "candidate_filtered": "candidate_filtered_prediction",
    }
    metrics = ("x", "y", "dx", "dy", "distance", "dx_px", "dy_px", "distance_px")
    fields.extend(f"{name}_{metric}" for name in sources for metric in metrics)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in frames:
            trial = plan[row["trial"]]
            target = trial["target"]
            item = {
                "trial": row["trial"],
                "condition": trial["condition"],
                "timestamp_ns": row["timestamp_ns"],
                "elapsed_s": row["elapsed_s"],
                "target_x": target[0],
                "target_y": target[1],
                "target_x_px": target[0] * screen_size[0],
                "target_y_px": target[1] * screen_size[1],
            }
            for name, key in sources.items():
                point = row.get(key)
                if point is None:
                    continue
                error = prediction_error(point, target, screen_size)
                assert error is not None
                item.update({f"{name}_x": point[0], f"{name}_y": point[1]})
                item.update({f"{name}_{k}": v for k, v in error.items()})
            writer.writerow(item)


def _point_metrics(
    rows: list[dict[str, Any]], target: Any, key: str, screen_size: tuple[int, int] | None = None
) -> dict[str, Any]:
    valid = [row for row in rows if row.get(key) is not None]
    result: dict[str, Any] = {
        "frames": len(rows),
        "valid_frames": len(valid),
        "coverage": len(valid) / len(rows) if rows else 0.0,
    }
    if not valid:
        return result
    xy = np.asarray([row[key] for row in valid], dtype=float)
    residual = xy - target
    distance = np.linalg.norm(residual, axis=1)
    result.update(
        mean_error=float(distance.mean()),
        p90_error=float(np.quantile(distance, 0.9)),
        max_error=float(distance.max()),
        horizontal_mae=float(np.abs(residual[:, 0]).mean()),
        vertical_mae=float(np.abs(residual[:, 1]).mean()),
        horizontal_bias=float(residual[:, 0].mean()),
        vertical_bias=float(residual[:, 1].mean()),
        scatter_rms=float(np.sqrt(np.mean(np.sum((xy - xy.mean(axis=0)) ** 2, axis=1)))),
    )
    if screen_size is not None:
        prediction_error(xy[0], target, screen_size)  # Validate units before aggregating.
        pixels = residual * screen_size
        pixel_distance = np.linalg.norm(pixels, axis=1)
        result.update(
            horizontal_bias_px=float(pixels[:, 0].mean()),
            vertical_bias_px=float(pixels[:, 1].mean()),
            horizontal_mae_px=float(np.abs(pixels[:, 0]).mean()),
            vertical_mae_px=float(np.abs(pixels[:, 1]).mean()),
            mean_error_px=float(pixel_distance.mean()),
            p90_error_px=float(np.quantile(pixel_distance, 0.9)),
            max_error_px=float(pixel_distance.max()),
        )
    # Real time windows, never the first/last N frames at an unknown frame rate.
    first = [r[key] for r in valid if r["elapsed_s"] <= rows[0]["elapsed_s"] + 0.5]
    last = [r[key] for r in valid if r["elapsed_s"] >= rows[-1]["elapsed_s"] - 0.5]
    result["drift"] = (
        float(np.linalg.norm(np.median(last, axis=0) - np.median(first, axis=0)))
        if first and last and rows[-1]["elapsed_s"] - rows[0]["elapsed_s"] >= 1.0
        else None
    )
    return result


def _arrival(rows: list[dict[str, Any]], target: Any, key: str) -> float | None:
    start = previous = None
    for row in rows:
        elapsed = row["elapsed_s"]
        point = row.get(key)
        inside = point is not None and np.linalg.norm(np.asarray(point) - target) <= 0.10
        if not inside:
            start = previous = None
        else:
            if previous is None or not 0 < elapsed - previous <= 0.1:
                start = elapsed
            previous = elapsed
            if start is not None and elapsed - start >= 0.15:
                return float(start * 1000)
    return None


def summarize_experiment(
    plan: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    screen_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    points = []
    for index, trial in enumerate(plan):
        rows = [r for r in frames if r["trial"] == index]
        steady = [r for r in rows if r["elapsed_s"] >= trial["settle_s"]]
        point = {
            "trial": index,
            "condition": trial["condition"],
            "target": trial["target"],
            "region": "center" if tuple(trial["target"]) == (0.5, 0.5) else "edge",
            "raw": _point_metrics(steady, trial["target"], "raw_prediction", screen_size),
            "filtered": _point_metrics(steady, trial["target"], "filtered_prediction", screen_size),
            "arrival_ms": _arrival(rows, trial["target"], "filtered_prediction"),
        }
        features = [
            r["features"] for r in rows if r.get("features") is not None and r["features"][15] == 1
        ]
        point["head_angle_span"] = (
            np.ptp(np.asarray(features)[:, 13:15], axis=0).tolist() if features else None
        )
        episodes, pending = [], None
        for row in rows:
            if row.get("closure_candidate"):
                if pending is None:
                    pending = row["elapsed_s"]
            elif pending is not None and row.get("filtered_prediction") is not None:
                episodes.append(
                    {
                        "unavailable_ms": (row["elapsed_s"] - pending) * 1000,
                        "first_return_error": float(
                            np.linalg.norm(np.asarray(row["filtered_prediction"]) - trial["target"])
                        ),
                    }
                )
                pending = None
        point["closure_recoveries"] = episodes
        point["unrecovered_closure"] = pending is not None
        points.append(point)
    groups = []
    for condition in dict.fromkeys(t["condition"] for t in plan):
        for region in ("center", "edge"):
            members = [p for p in points if p["condition"] == condition and p["region"] == region]
            if not members:
                continue
            group: dict[str, Any] = {
                "condition": condition,
                "region": region,
                "locations": len(members),
            }
            for key in ("raw", "filtered"):
                observed = [p[key] for p in members if "mean_error" in p[key]]
                group[key] = {
                    "locations_with_data": len(observed),
                    "coverage": float(np.mean([p[key]["coverage"] for p in members])),
                }
                for metric in (
                    "mean_error",
                    "scatter_rms",
                    "drift",
                    "horizontal_bias",
                    "vertical_bias",
                    "horizontal_mae",
                    "vertical_mae",
                    "horizontal_bias_px",
                    "vertical_bias_px",
                    "horizontal_mae_px",
                    "vertical_mae_px",
                    "mean_error_px",
                ):
                    values = [v[metric] for v in observed if v.get(metric) is not None]
                    group[key][metric] = float(np.mean(values)) if values else None
            groups.append(group)
    return {
        "coordinate_convention": "prediction minus intended target; positive x right, y down",
        "screen_size": screen_size,
        "pixel_units": "logical screen pixels; not camera pixels or visual angle",
        "points": points,
        "groups": groups,
        "note": (
            "Descriptive per-location metrics, not independent-frame inference. "
            "Arrival includes human reaction. Closure candidates also include occlusion; "
            "do not interpret as confirmed blinks."
        ),
    }


def filter_variants() -> dict[str, dict[str, Any]]:
    result = {"baseline": FILTER_BASELINE.copy()}
    for name, values in {
        "min_cutoff": (0.25, 0.75),
        "speed_gain": (2.0, 8.0),
        "derivative_cutoff": (0.5, 2.0),
        "median_window": (1, 5),
    }.items():
        for value in values:
            result[f"{name}={value}"] = {**FILTER_BASELINE, name: value}
    # Predeclared small factorial checks interaction of smoothing and speed response.
    for cutoff in (0.25, 0.75):
        for gain in (2.0, 8.0):
            result[f"interaction:{cutoff}/{gain}"] = {
                **FILTER_BASELINE,
                "min_cutoff": cutoff,
                "speed_gain": gain,
            }
    return result


def filter_sweep(plan: list[dict[str, Any]], frames: list[dict[str, Any]]) -> dict[str, Any]:
    results = {}
    for name, parameters in filter_variants().items():
        smoother = GazeSmoother(**parameters)
        replay = []
        for row in frames:
            raw = row.get("raw_prediction")
            filtered = None
            if raw is not None and all(np.isfinite(v) and 0 <= v <= 1 for v in raw):
                filtered = smoother.update(raw, row["timestamp_ns"], row["quality"])
            else:
                smoother.reset()
            replay.append({**row, "filtered_prediction": filtered})
        results[name] = {"parameters": parameters, "summary": summarize_experiment(plan, replay)}
    return {
        "variants": results,
        "auto_applied": False,
        "note": (
            "Paired replay of identical raw frames. No fitting or automatic parameter selection. "
            "Confirm any chosen setting on fresh sessions before use."
        ),
    }
