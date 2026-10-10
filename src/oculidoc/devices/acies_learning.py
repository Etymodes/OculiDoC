"""Patient-bound ACIES training history and frozen holdout comparison.

Only numeric training features are retained. Validation targets are used for
scoring frozen models, never for fitting or changing their predictions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from oculidoc.devices.acies import (
    ACIES_VERSION,
    CALIBRATION_FEATURE_VERSION,
    COMPATIBLE_MODEL_VERSIONS,
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
MAX_TRAINING_ROUNDS = 5
MAX_TARGET_SAMPLES = 60
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
    counts = Counter(tuple(row) for row in y.tolist())
    if len(counts) < 9 or min(counts.values()) < 5:
        raise ValueError("ACIES 训练至少需要九个不同目标，每个目标五个有效样本。")
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
    temporary = path.with_name(f".{path.stem}-{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        # Linking a fully written file publishes it atomically and, unlike
        # replace(), cannot overwrite a round created by a simultaneous caller.
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise ValueError("该 ACIES 训练轮次已存在，不会覆盖。") from error
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _load_round(path: Path, identity: str) -> dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != 1
        or record.get("identity") != identity
        or record.get("feature_version") != CALIBRATION_FEATURE_VERSION
        or record.get("algorithm_version") not in COMPATIBLE_MODEL_VERSIONS
        or record.get("round_id") != path.stem
    ):
        raise ValueError("ACIES 历史训练档案与当前受试者或特征版本不匹配。")
    created_at = datetime.fromisoformat(record["created_at"])
    if created_at.tzinfo is None:
        raise ValueError("ACIES 历史训练时间缺少时区。")
    x, y, q, eyes = _training_rows(
        record.get("features"),
        record.get("targets"),
        record.get("qualities"),
        record.get("eye_features"),
    )
    return {
        "round_id": path.stem,
        "created_at": created_at.timestamp(),
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
    rounds = sorted(rounds, key=lambda record: (record["created_at"], record["round_id"]))[
        -(MAX_TRAINING_ROUNDS - 1) :
    ]
    rounds.append(current)
    round_ids = [record["round_id"] for record in rounds]
    if len(rounds) < 2:
        return None, round_ids
    # Sampling is deterministic and preserves chronological order within a
    # round. Matching targets get the same number of rows in every round, so
    # camera frame rate or a longer visit cannot dominate another visit.
    target_limits: dict[tuple[float, float], int] = {}
    for record in rounds:
        for target, count in Counter(record["targets"]).items():
            target_limits[target] = min(target_limits.get(target, MAX_TARGET_SAMPLES), count)
    features, targets, qualities, eyes = [], [], [], []
    for record in rounds:
        grouped: dict[tuple[float, float], list[int]] = {}
        for index, target in enumerate(record["targets"]):
            grouped.setdefault(target, []).append(index)
        selected: list[int] = []
        for target, indices in grouped.items():
            positions = np.linspace(0, len(indices) - 1, target_limits[target], dtype=int)
            selected.extend(indices[int(position)] for position in positions)
        for index in sorted(selected):
            features.append(record["features"][index])
            targets.append(record["targets"][index])
            qualities.append(record["qualities"][index])
            eyes.append(record["eye_features"][index])
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
    """Score every frozen model against the same unmodified holdout packets.

    ``coverage`` and per-point ``valid_ratio`` count finite raw predictions,
    including coordinates outside the screen. ``output_coverage`` separately
    counts in-screen coordinates that the application can actually display.
    Out-of-screen errors are never removed from any accuracy statistic.
    """
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
                or np.any((target < 0) | (target > 1))
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
                if (
                    not isinstance(quality, (int, float))
                    or not np.isfinite(quality)
                    or not MIN_SIGNAL_QUALITY <= quality <= 1
                ):
                    continue
                try:
                    features = packet["features"]
                    if model.get("pose_required") and features[15] != 1:
                        continue
                    prediction = predict_packet(model, packet)
                    if prediction is not None and np.isfinite(prediction).all():
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
            eye_errors = {
                eye_name: {
                    "sample_count": len(values),
                    "mean_error": float(np.mean(values)),
                    "max_error": float(np.max(values)),
                }
                for eye_name, values in per_eye.items()
                if values
            }
            if predictions:
                prediction_array = np.asarray(predictions)
                in_screen_count = int(
                    np.all((prediction_array >= 0) & (prediction_array <= 1), axis=1).sum()
                )
                residuals = prediction_array - target
                errors = np.linalg.norm(residuals, axis=1)
                all_errors.extend(errors.tolist())
                point = {
                    "target": target.tolist(),
                    "sample_count": len(predictions),
                    "valid_ratio": len(predictions) / max(len(packets), 1),
                    "finite_coverage": len(predictions) / max(len(packets), 1),
                    "in_screen_sample_count": in_screen_count,
                    "output_coverage": in_screen_count / max(len(packets), 1),
                    "mean_error": float(np.mean(errors)),
                    "max_error": float(np.max(errors)),
                    "mean_prediction": np.mean(predictions, axis=0).tolist(),
                    "horizontal_mae": float(np.mean(np.abs(residuals[:, 0]))),
                    "vertical_mae": float(np.mean(np.abs(residuals[:, 1]))),
                    "horizontal_bias": float(np.mean(residuals[:, 0])),
                    "vertical_bias": float(np.mean(residuals[:, 1])),
                    "eye_errors": eye_errors,
                }
            else:
                point = {
                    "target": target.tolist(),
                    "sample_count": 0,
                    "valid_ratio": 0.0,
                    "finite_coverage": 0.0,
                    "in_screen_sample_count": 0,
                    "output_coverage": 0.0,
                    "mean_error": None,
                    "max_error": None,
                    "mean_prediction": None,
                    "horizontal_mae": None,
                    "vertical_mae": None,
                    "horizontal_bias": None,
                    "vertical_bias": None,
                    "eye_errors": eye_errors,
                }
            per_point.append(point)
        unique_targets = {tuple(point["target"]) for point in per_point}
        eligible = len(unique_targets) >= 9 and all(
            point["sample_count"] >= 5 for point in per_point
        )
        mean_error = (
            float(np.mean([point["mean_error"] for point in per_point])) if eligible else None
        )
        maximum = float(np.max(all_errors)) if eligible else None
        # Each location has equal influence; a longer easy segment cannot hide
        # loss of coverage at the difficult edge locations.
        coverage = float(np.mean([point["valid_ratio"] for point in per_point]))
        output_coverage = float(np.mean([point["output_coverage"] for point in per_point]))
        centers = [point for point in per_point if tuple(point["target"]) == (0.5, 0.5)]
        edges = [point for point in per_point if tuple(point["target"]) != (0.5, 0.5)]
        center_error = (
            float(np.mean([point["mean_error"] for point in centers]))
            if eligible and centers
            else None
        )
        edge_error = (
            float(np.mean([point["mean_error"] for point in edges])) if eligible and edges else None
        )
        axes = {
            key: float(np.mean([point[key] for point in per_point])) if eligible else None
            for key in ("horizontal_mae", "vertical_mae", "horizontal_bias", "vertical_bias")
        }
        eye_validation = {}
        for eye_name in model.get("eye_models", {}):
            values = [point["eye_errors"].get(eye_name, {}) for point in per_point]
            complete = len(unique_targets) >= 9 and all(
                value.get("sample_count", 0) >= 5 for value in values
            )
            eye_mean = (
                float(np.mean([value["mean_error"] for value in values])) if complete else None
            )
            eye_max = float(np.max([value["max_error"] for value in values])) if complete else None
            eye_validation[eye_name] = {
                "complete": complete,
                "validated": bool(
                    eye_mean is not None
                    and eye_max is not None
                    and validation_passes(eye_mean, eye_max, profile)
                ),
                "mean_error": eye_mean,
                "max_error": eye_max,
                "sample_count": sum(value.get("sample_count", 0) for value in values),
            }
        results[name] = {
            "eligible": eligible,
            "per_point": per_point,
            "mean_error": mean_error,
            "max_error": maximum,
            "p90_error": float(np.percentile(all_errors, 90)) if eligible else None,
            "coverage": coverage,
            "finite_coverage": coverage,
            "output_coverage": output_coverage,
            "in_screen_coverage": output_coverage,
            "in_screen_output_coverage": output_coverage,
            "frame_coverage": total_predictions / max(total_packets, 1),
            "center_error": center_error,
            "edge_error": edge_error,
            "eye_validation": eye_validation,
            **axes,
            "meets_profile": bool(
                mean_error is not None
                and maximum is not None
                and validation_passes(mean_error, maximum, profile)
            ),
            # A model cannot improve its rank merely by omitting difficult frames.
            "selection_score": mean_error + 0.25 * (1 - coverage)
            if mean_error is not None
            else None,
            "promotion_reasons": [],
        }
    return results


def choose_model(
    evaluations: dict[str, dict[str, Any]],
    incumbent_name: str = "previous",
    fallback_name: str = "current",
) -> str:
    """Keep the incumbent unless a frozen contender materially improves it.

    These are conservative, within-round selection rules, not evidence of
    improvement in another session. No model parameters or gaze coordinates
    are modified, and a precision threshold crossing alone does not win.
    """
    if not evaluations:
        raise ValueError("没有可比较的 ACIES 模型。")
    for score in evaluations.values():
        score["promotion_reasons"] = []
    reference_name = (
        incumbent_name
        if evaluations.get(incumbent_name, {}).get("eligible")
        else fallback_name
        if fallback_name in evaluations
        else next(iter(evaluations))
    )
    reference = evaluations[reference_name]
    if not reference.get("eligible"):
        reference["promotion_reasons"].append("基准验证不完整；本轮不自动替换模型。")
        return reference_name
    selected = reference_name
    qualifying = []
    keys = (
        "mean_error",
        "center_error",
        "edge_error",
        "coverage",
        "output_coverage",
        "p90_error",
        "max_error",
    )
    for name, score in evaluations.items():
        if name == reference_name:
            continue
        reasons = score["promotion_reasons"]
        if not score.get("eligible"):
            reasons.append("未完成九个不同目标的充分验证。")
            continue
        if any(
            not isinstance(value, (int, float)) or not np.isfinite(value)
            for record in (reference, score)
            for value in (record.get(key) for key in keys)
        ):
            reasons.append("基准或候选缺少完整误差与覆盖率指标。")
            continue
        if reference["mean_error"] - score["mean_error"] < 0.005 - 1e-12:
            reasons.append("平均误差改善不足 0.5 个百分点。")
        for key, label in (("center_error", "中心"), ("edge_error", "边缘")):
            if score[key] - reference[key] > 0.005 + 1e-12:
                reasons.append(f"{label}误差退步超过 0.5 个百分点。")
        if reference["coverage"] - score["coverage"] > 0.01 + 1e-12:
            reasons.append("有限预测覆盖率下降超过 1 个百分点。")
        if reference["output_coverage"] - score["output_coverage"] > 0.01 + 1e-12:
            reasons.append("屏幕内实际输出覆盖率下降超过 1 个百分点。")
        for key, label in (("p90_error", "P90"), ("max_error", "最大")):
            if score[key] - reference[key] > 0.01 + 1e-12:
                reasons.append(f"{label}误差退步超过 1 个百分点。")
        if reference.get("meets_profile") and not score.get("meets_profile"):
            reasons.append("基准已达到精度目标，候选尚未达到。")
        if not reasons:
            qualifying.append(name)
            reasons.append("平均误差明显改善，中心、边缘、覆盖率及尾部误差均未明显退步。")
    if qualifying:
        selected = min(qualifying, key=lambda name: (evaluations[name]["mean_error"], name))
        evaluations[selected]["promotion_reasons"].append("本轮选择此冻结候选。")
        reference["promotion_reasons"].append(f"本轮选择 {selected}；保留基准版本以便回退。")
    else:
        reference["promotion_reasons"].append("没有候选满足保守改进规则，保留基准模型。")
    return selected
