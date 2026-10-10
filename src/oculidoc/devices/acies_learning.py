"""Patient-bound ACIES training history and frozen holdout comparison.

Only numeric training features are retained. Validation targets are used for
scoring frozen models, never for fitting or changing their predictions.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from oculidoc.devices.acies import (
    ACIES_VERSION,
    CALIBRATION_FEATURE_VERSION,
    FEATURE_COUNT,
    MIN_SIGNAL_QUALITY,
    validation_passes,
)
from oculidoc.devices.webcam_gaze import (
    fit_calibration,
    predict_calibration,
    predict_packet,
)

MAX_ROUND_SAMPLES = 5_000
ROUND_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def _round_path(runtime: Path, identity: str, round_id: str) -> Path:
    if not identity or not ROUND_ID_PATTERN.fullmatch(round_id):
        raise ValueError("无效的 ACIES 训练轮次标识。")
    identity_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return runtime / "training_rounds" / identity_key / f"{round_id}.json"


def _training_rows(
    features: Any,
    targets: Any,
    qualities: Any,
    eye_features: Any,
) -> tuple[list[list[float]], list[tuple[float, float]], list[float], list[dict[str, Any]]]:
    try:
        x = np.asarray(features, dtype=float)
        y = np.asarray(targets, dtype=float)
        q = np.asarray(qualities, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("ACIES 训练样本包含无效数值。") from error
    count = len(x) if x.ndim else 0
    if (
        not 0 < count <= MAX_ROUND_SAMPLES
        or x.shape != (count, FEATURE_COUNT)
        or y.shape != (count, 2)
        or q.shape != (count,)
        or not np.isfinite(x).all()
        or not np.isfinite(y).all()
        or not np.isfinite(q).all()
        or np.any((y < 0) | (y > 1))
        or np.any((q <= 0) | (q > 1))
        or not isinstance(eye_features, list)
        or len(eye_features) != count
    ):
        raise ValueError("ACIES 训练样本数量、维度或范围无效。")
    eyes: list[dict[str, Any]] = []
    for item in eye_features:
        if not isinstance(item, dict) or set(item) - {"left", "right"}:
            raise ValueError("ACIES 分眼训练样本无效。")
        checked: dict[str, Any] = {}
        for name in ("left", "right"):
            row = item.get(name)
            if row is None:
                checked[name] = None
                continue
            try:
                array = np.asarray(row, dtype=float)
            except (TypeError, ValueError) as error:
                raise ValueError("ACIES 分眼训练样本无效。") from error
            if array.shape != (FEATURE_COUNT,) or not np.isfinite(array).all():
                raise ValueError("ACIES 分眼训练样本无效。")
            checked[name] = array.tolist()
        eyes.append(checked)
    return x.tolist(), [tuple(row) for row in y.tolist()], q.tolist(), eyes


def save_training_round(
    runtime: Path,
    identity: str,
    round_id: str,
    features: list[list[float]],
    targets: list[tuple[float, float]],
    qualities: list[float],
    eye_features: list[dict[str, Any]],
) -> Path:
    """Atomically retain one completed training round without images or holdout labels."""
    x, y, q, eyes = _training_rows(features, targets, qualities, eye_features)
    path = _round_path(runtime, identity, round_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("该 ACIES 训练轮次已存在，不会覆盖。")
    record = {
        "schema_version": 1,
        "identity": identity,
        "feature_version": CALIBRATION_FEATURE_VERSION,
        "algorithm_version": ACIES_VERSION,
        "round_id": round_id,
        "created_at": datetime.now(UTC).isoformat(),
        "features": x,
        "targets": y,
        "qualities": q,
        "eye_features": eyes,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)
    return path


def _load_round(path: Path, identity: str) -> dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != 1
        or record.get("identity") != identity
        or record.get("feature_version") != CALIBRATION_FEATURE_VERSION
        or record.get("round_id") != path.stem
    ):
        raise ValueError("ACIES 历史训练档案与当前受试者或特征版本不匹配。")
    x, y, q, eyes = _training_rows(
        record.get("features"),
        record.get("targets"),
        record.get("qualities"),
        record.get("eye_features"),
    )
    return {
        "round_id": path.stem,
        "features": x,
        "targets": y,
        "qualities": q,
        "eye_features": eyes,
    }


def fit_accumulated(
    runtime: Path, identity: str, current_round_id: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """Fit matching training rounds only; current round must exist and be valid."""
    current_path = _round_path(runtime, identity, current_round_id)
    current = _load_round(current_path, identity)
    rounds = []
    for path in sorted(current_path.parent.glob("*.json")):
        if path == current_path:
            continue
        try:
            rounds.append(_load_round(path, identity))
        except (OSError, ValueError, TypeError, KeyError):
            # A damaged historical file cannot poison a newly collected round.
            continue
    rounds.append(current)
    round_ids = [record["round_id"] for record in rounds]
    if len(rounds) < 2:
        return None, round_ids
    features = [row for record in rounds for row in record["features"]]
    targets = [tuple(row) for record in rounds for row in record["targets"]]
    qualities = [quality for record in rounds for quality in record["qualities"]]
    eyes = [item for record in rounds for item in record["eye_features"]]
    model = fit_calibration(features, targets, qualities, eye_features=eyes)
    model["training_round_ids"] = round_ids
    return model, round_ids


def _predict_eye(
    model: dict[str, Any], packet: dict[str, Any], name: str
) -> tuple[float, float] | None:
    child = model.get("eye_models", {}).get(name)
    row = packet.get("eye_features", {}).get(name)
    if not isinstance(child, dict) or row is None or not packet.get(f"{name}_eye_valid"):
        return None
    if child.get("pose_required") and row[15] != 1:
        return None
    return predict_calibration(child, row)


def evaluate_models(
    models: dict[str, dict[str, Any]],
    validation_batches: list[dict[str, Any]],
    profile: str,
) -> dict[str, dict[str, Any]]:
    """Score every frozen model against the same unmodified holdout packets."""
    if not validation_batches:
        raise ValueError("ACIES 独立验证为空。")
    # Check the profile even when every candidate has too little coverage.
    validation_passes(0, 0, profile)
    results: dict[str, dict[str, Any]] = {}
    for name, model in models.items():
        per_point: list[dict[str, Any]] = []
        all_errors: list[float] = []
        total_packets = 0
        total_predictions = 0
        for batch in validation_batches:
            target = np.asarray(batch["target"], dtype=float)
            packets = batch["packets"]
            if (
                target.shape != (2,)
                or not np.isfinite(target).all()
                or not isinstance(packets, list)
            ):
                raise ValueError("ACIES 独立验证数据无效。")
            total_packets += len(packets)
            predictions: list[tuple[float, float]] = []
            per_eye: dict[str, list[float]] = {"left": [], "right": []}
            for packet in packets:
                if not isinstance(packet, dict) or packet.get("features") is None:
                    continue
                quality = packet.get("confidence", 0)
                if not isinstance(quality, (int, float)) or quality < MIN_SIGNAL_QUALITY:
                    continue
                try:
                    prediction = predict_packet(model, packet)
                    if prediction is None or not np.isfinite(prediction).all():
                        continue
                    predictions.append(prediction)
                    for eye_name in per_eye:
                        eye_point = _predict_eye(model, packet, eye_name)
                        if eye_point is not None and np.isfinite(eye_point).all():
                            per_eye[eye_name].append(
                                float(np.linalg.norm(np.asarray(eye_point) - target))
                            )
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
            total_predictions += len(predictions)
            if predictions:
                residuals = np.asarray(predictions) - target
                errors = np.linalg.norm(residuals, axis=1)
                all_errors.extend(errors.tolist())
                eye_errors = {
                    eye_name: {
                        "sample_count": len(values),
                        "mean_error": float(np.mean(values)),
                        "max_error": float(np.max(values)),
                    }
                    for eye_name, values in per_eye.items()
                    if values
                }
                point = {
                    "target": target.tolist(),
                    "sample_count": len(predictions),
                    "valid_ratio": len(predictions) / max(len(packets), 1),
                    "mean_error": float(np.mean(errors)),
                    "max_error": float(np.max(errors)),
                    "mean_prediction": np.mean(predictions, axis=0).tolist(),
                    "horizontal_mae": float(np.mean(np.abs(residuals[:, 0]))),
                    "vertical_mae": float(np.mean(np.abs(residuals[:, 1]))),
                    "eye_errors": eye_errors,
                }
            else:
                point = {
                    "target": target.tolist(),
                    "sample_count": 0,
                    "valid_ratio": 0.0,
                    "mean_error": None,
                    "max_error": None,
                    "mean_prediction": None,
                    "horizontal_mae": None,
                    "vertical_mae": None,
                    "eye_errors": {},
                }
            per_point.append(point)
        eligible = all(point["sample_count"] >= 5 for point in per_point)
        mean_error = (
            float(np.mean([point["mean_error"] for point in per_point])) if eligible else None
        )
        maximum = float(np.max(all_errors)) if eligible else None
        coverage = total_predictions / max(total_packets, 1)
        results[name] = {
            "eligible": eligible,
            "per_point": per_point,
            "mean_error": mean_error,
            "max_error": maximum,
            "p90_error": float(np.percentile(all_errors, 90)) if eligible else None,
            "coverage": coverage,
            "meets_profile": bool(
                mean_error is not None
                and maximum is not None
                and validation_passes(mean_error, maximum, profile)
            ),
            # A model cannot improve its rank merely by omitting difficult frames.
            "selection_score": mean_error + 0.25 * (1 - coverage)
            if mean_error is not None
            else None,
        }
    return results


def choose_model(evaluations: dict[str, dict[str, Any]]) -> str:
    """Prefer validated mappings, then coverage-adjusted error and the retained mapping."""
    if not evaluations:
        raise ValueError("没有可比较的 ACIES 模型。")
    eligible = [name for name, score in evaluations.items() if score.get("eligible")]
    if not eligible:
        return "current" if "current" in evaluations else next(iter(evaluations))
    passing = [name for name in eligible if evaluations[name].get("meets_profile")]
    candidates = passing or eligible
    priority = {"previous": 0, "current": 1, "accumulated": 2}
    return min(
        candidates,
        key=lambda name: (evaluations[name]["selection_score"], priority.get(name, 3), name),
    )
