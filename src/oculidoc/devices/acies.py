"""ACIES: original, quality-aware iris geometry and personal screen mapping.

The optional MediaPipe model supplies face/iris landmarks, not gaze predictions.
This module does not import EyeTrax, WebEyeTrack, EyeTheia or their weights/code.
Scores describe engineering signal quality; they are not calibrated probabilities.
Images are used in memory only. Targets are never inputs to feature extraction.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import pi
from time import monotonic_ns
from typing import Any

import numpy as np

ACIES_VERSION = "0.6.0"
# Geometry is unchanged: a temporal-filter update does not invalidate a sound map.
CALIBRATION_FEATURE_VERSION = "0.3.0"
COMPATIBLE_MODEL_VERSIONS = frozenset({"0.3.0", "0.4.0", "0.5.0", "0.5.1", ACIES_VERSION})
# Last three values are binocular diagnostics, deliberately excluded from gaze fits.
FEATURE_COUNT = 19
POSE_COLUMNS = (5, 6, 7, 8, 9, 10, 13, 14)
OBSERVATION_LABELS = {
    "unavailable": "眼部信息不足",
    "uncalibrated": "双眼一致性尚未建立基线",
    "monocular": "单眼估计，无法比较双眼",
    "consistent": "双眼相对状态接近校准基线",
    "binocular_changed": "双眼相对状态持续改变，聚焦状态未知",
    "image_detail_low": "眼部图像细节低于校准时",
    "reflection": "眼区强反光，估计质量降低",
    "pose_outside_calibration": "头部位置超出已校准范围",
    "pose_unavailable": "头部角度暂不可用",
    "comparison_unavailable": "双眼状态暂无法比较",
    "observing_change": "正在观察短时变化",
    "eye_mapping_unavailable": "当前可见眼尚无可用映射",
}
PROFILE_LIMITS = {
    "strict": (0.15, 0.30),
    "balanced": (0.20, 0.35),
    "tolerant": (0.25, 0.45),
}
PROFILE_LABELS = {
    "strict": "严格 · 平均 15% / 最大 30%",
    "balanced": "标准 · 平均 20% / 最大 35%",
    "tolerant": "宽容 · 平均 25% / 最大 45%",
}
MIN_SIGNAL_QUALITY = 0.18


@dataclass(frozen=True)
class MappingParameters:
    """Explicit training-only controls for paired accuracy experiments."""

    ridge_scale: float = 1.0
    lid_penalty: float = 4.0
    stationary_penalty: float = 1.0
    use_lids: bool = True

    def __post_init__(self) -> None:
        values = (self.ridge_scale, self.lid_penalty, self.stationary_penalty)
        if (
            not np.isfinite(values).all()
            or self.ridge_scale <= 0
            or self.lid_penalty <= 0
            or self.stationary_penalty < 0
            or type(self.use_lids) is not bool
        ):
            raise ValueError("Invalid mapping experiment parameters")


def validation_passes(mean: float, maximum: float, profile: str) -> bool:
    limits = PROFILE_LIMITS[profile]
    return bool(
        np.isfinite([mean, maximum]).all()
        and 0 <= mean <= limits[0]
        and mean <= maximum <= limits[1]
    )


def _eye_measurement(
    gray: Any,
    points: Any,
    corners: tuple[int, int],
    lids: tuple[int, int],
    iris: int,
    diagnostics: dict[str, Any],
) -> tuple[Any, float, str]:
    """Measure one visible eye in its local, roll-corrected coordinate system."""
    a, b = points[list(corners), :2]
    width = float(np.linalg.norm(b - a))
    diagnostics["width_px"] = width
    if width < 6:
        return None, 0.0, "eye_too_small"
    horizontal = (b - a) / width
    vertical = np.array([-horizontal[1], horizontal[0]])
    upper, lower = points[list(lids), :2]
    aperture = abs(float((lower - upper) @ vertical))
    openness = aperture / width
    diagnostics["openness"] = openness
    # Accept a narrow but visibly open slit; do not infer gaze through closed lids.
    if aperture < 1.1 or openness < 0.035:
        return None, 0.0, "eye_closed_or_occluded"
    center = (upper + lower) * 0.5
    anchor = (a + b) * 0.5
    iris_center = points[iris, :2]
    u = float((iris_center - anchor) @ horizontal / width)
    lid_offset = float((iris_center - center) @ vertical / width)
    if abs(u) > 0.48 or abs(lid_offset) > max(0.04, openness * 0.65):
        return None, 0.0, "iris_outside_lids"

    # Image support prevents accepting a landmark network's iris hallucination
    # on a uniform/covered eye. Bright spectacle glints are excluded from moments.
    h, w = gray.shape
    radius = width * 0.55
    x0, x1 = max(0, int(center[0] - radius)), min(w, int(center[0] + radius) + 1)
    y0, y1 = max(0, int(center[1] - radius)), min(h, int(center[1] + radius) + 1)
    if x1 <= x0 or y1 <= y0:
        return None, 0.0, "eye_outside_frame"
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dx, dy = xx - center[0], yy - center[1]
    local_x = dx * horizontal[0] + dy * horizontal[1]
    local_y = dx * vertical[0] + dy * vertical[1]
    visible = (local_x / (width * 0.48)) ** 2 + (local_y / max(aperture * 0.5, 0.8)) ** 2 <= 1
    pixels = gray[y0:y1, x0:x1].astype(float)
    values = pixels[visible]
    if len(values) < 6:
        return None, 0.0, "insufficient_eye_pixels"
    low, high = np.percentile(values, [10, 90])
    contrast = float(high - low)
    glare = float(np.mean(values >= 245))
    # Dimensionless image-detail cue, not the eye's optical accommodation.
    # Use adjacent pixels inside the aperture; ignore ROI borders and bright glints.
    supported = visible & (pixels < 245)
    pairs_x = supported[:, 1:] & supported[:, :-1]
    pairs_y = supported[1:, :] & supported[:-1, :]
    gradients = np.concatenate(
        (
            np.abs(np.diff(pixels, axis=1))[pairs_x],
            np.abs(np.diff(pixels, axis=0))[pairs_y],
        )
    )
    detail = float(np.mean(gradients) / max(contrast, 1)) if len(gradients) else 0.0
    diagnostics.update(glare_ratio=glare, contrast=contrast, image_detail=detail)
    if contrast < 3 or glare > 0.80:
        return None, 0.0, "no_iris_image_support"
    dark = visible & (pixels <= low + 0.35 * contrast) & (pixels < 245)
    support = np.maximum(high - pixels, 0) * dark
    mass = float(support.sum())
    if mass > 0:
        dark_center = np.array([(support * xx).sum(), (support * yy).sum()]) / mass
        # Small image-supported correction, not unrestricted eyelash tracking.
        if np.linalg.norm(dark_center - iris_center) < width * 0.18:
            correction = dark_center - iris_center
            # A slit clips the iris image vertically. Its dark centroid follows
            # the lid aperture, so use that correction only as the eye opens.
            vertical_support = float(np.clip((openness - 0.08) / 0.10, 0, 1))
            iris_center = iris_center + 0.15 * (
                (correction @ horizontal) * horizontal
                + vertical_support * (correction @ vertical) * vertical
            )
    u = float((iris_center - anchor) @ horizontal / width)
    # Eye corners are the reference: subtracting the moving lid midpoint can
    # cancel vertical iris motion. Do not divide by a changing/tiny aperture.
    v = float((iris_center - anchor) @ vertical / width)
    lid_center = float((center - anchor) @ vertical / width)
    resolution = float(np.clip(width / 24, 0.25, 1))
    opening = float(np.clip(openness / 0.18, 0.25, 1))
    texture = float(np.clip(contrast / 35, 0.25, 1))
    quality = resolution * (0.4 + 0.6 * opening) * (0.4 + 0.6 * texture) * (1 - glare * 0.65)
    diagnostics["quality"] = float(np.clip(quality, 0, 1))
    return np.array([u, v, lid_center, openness]), diagnostics["quality"], "visible"


def face_pose(matrix: Any) -> tuple[float, float] | None:
    """Yaw/pitch proxies from MediaPipe's optional canonical-face transform.

    Remove uniform scale first. These are model-derived angles, not measured
    clinical head angles or a calibrated camera-to-screen geometry.
    """
    if matrix is None:
        return None
    transform = np.asarray(matrix, dtype=float)
    if transform.shape != (4, 4) or not np.isfinite(transform).all():
        return None
    rotation = transform[:3, :3]
    scales = np.linalg.norm(rotation, axis=0)
    if np.min(scales) < 1e-6:
        return None
    rotation = rotation / scales
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=0.08) or np.linalg.det(rotation) < 0:
        return None
    yaw = float(np.arctan2(-rotation[2, 0], np.hypot(rotation[0, 0], rotation[1, 0])))
    pitch = float(np.arctan2(rotation[2, 1], rotation[2, 2]))
    return yaw, pitch


def extract_observation(gray: Any, landmarks: Any, matrix: Any = None) -> dict[str, Any]:
    """Extract original binocular/monocular features from actual image evidence."""
    invalid: dict[str, Any] = {
        "features": None,
        "confidence": 0.0,
        "mode": "unavailable",
        "left_eye_valid": False,
        "right_eye_valid": False,
        "preview": None,
        "reason": "no_face",
        "algorithm_version": ACIES_VERSION,
        "eye_diagnostics": {},
        "eye_features": {"left": None, "right": None},
        "screen_focus": "unknown",
    }
    points = np.asarray(landmarks, dtype=float)
    if points.shape != (478, 3) or not np.isfinite(points).all():
        return invalid
    if gray.ndim != 2 or gray.size == 0:
        raise ValueError("ACIES needs a non-empty grayscale frame.")
    height, width = gray.shape
    points = points * [width, height, width]
    eye_diagnostics: dict[str, Any] = {"left": {}, "right": {}}
    right, qr, rr = _eye_measurement(
        gray, points, (33, 133), (159, 145), 468, eye_diagnostics["right"]
    )
    left, ql, rl = _eye_measurement(
        gray, points, (362, 263), (386, 374), 473, eye_diagnostics["left"]
    )
    eye_diagnostics["left"].update(valid=ql >= MIN_SIGNAL_QUALITY, reason=rl)
    eye_diagnostics["right"].update(valid=qr >= MIN_SIGNAL_QUALITY, reason=rr)
    invalid["eye_diagnostics"] = eye_diagnostics
    left_valid, right_valid = ql >= MIN_SIGNAL_QUALITY, qr >= MIN_SIGNAL_QUALITY
    if not left_valid and not right_valid:
        return {**invalid, "reason": f"{rl};{rr}"}
    pairs = [
        (value, quality)
        for value, quality in ((left, ql), (right, qr))
        if value is not None and quality >= MIN_SIGNAL_QUALITY
    ]
    fused = sum(value * quality for value, quality in pairs) / sum(q for _, q in pairs)
    confidence = max(ql, qr) * (1.0 if left_valid and right_valid else 0.7)
    # A person's normal binocular offset need not be zero. Compare against a
    # pose-conditioned personal baseline later, not a universal alignment rule.
    if confidence < MIN_SIGNAL_QUALITY:
        return {**invalid, "reason": "weak_or_conflicting_iris_evidence"}
    outer = points[263, :2] - points[33, :2]
    face_width = float(np.linalg.norm(outer))
    if face_width < 20:
        return {**invalid, "reason": "face_too_small"}
    axis = outer / face_width
    down = np.array([-axis[1], axis[0]])
    middle = (points[263, :2] + points[33, :2]) / 2
    nose = points[1, :2] - middle
    u, v, lid_center, openness = map(float, fused)
    pose = face_pose(matrix)
    delta = left[:2] - right[:2] if left_valid and right_valid else np.zeros(2)
    features = [
        u,
        v,
        u * u,
        v * v,
        u * v,
        float(nose @ axis / face_width),
        float(nose @ down / face_width),
        float(np.arctan2(axis[1], axis[0])),
        float(middle[0] / width),
        float(middle[1] / height),
        float(np.log(face_width / width)),
        lid_center,
        openness,
        pose[0] if pose is not None else 0.0,
        pose[1] if pose is not None else 0.0,
        float(pose is not None),
        float(delta[0]),
        float(delta[1]),
        float(left_valid and right_valid),
    ]
    mode = (
        "binocular" if left_valid and right_valid else ("left_eye" if left_valid else "right_eye")
    )
    # Keep each eye in its own coordinate system until it is mapped to screen
    # space. Raw iris offsets differ between eyes; changing a quality weight or
    # losing one eye must not silently change the mapping's coordinate origin.
    eye_features = {}
    for name, value, valid in (("left", left, left_valid), ("right", right, right_valid)):
        row = None
        if valid:
            eu, ev, elid, eopen = map(float, value)
            row = features.copy()
            row[:5] = [eu, ev, eu * eu, ev * ev, eu * ev]
            row[11:13] = [elid, eopen]
        eye_features[name] = row
    return {
        "features": features,
        "confidence": float(confidence),
        "mode": mode,
        "left_eye_valid": left_valid,
        "right_eye_valid": right_valid,
        # Uncalibrated geometry preview only; never used for tasks or training labels.
        "preview": [float(np.clip(0.5 - 2.4 * u, 0, 1)), float(np.clip(0.5 + 3.2 * v, 0, 1))],
        "reason": "visible" if confidence >= 0.5 else "limited_eye_evidence",
        "algorithm_version": ACIES_VERSION,
        "eye_diagnostics": eye_diagnostics,
        "eye_features": eye_features,
        "screen_focus": "unknown",
    }


class AciesExtractor:
    """Optional image frontend using only the generic MediaPipe Face Landmarker."""

    def __init__(self, face_model: str) -> None:
        import mediapipe as mp  # type: ignore[import-not-found]  # Isolated optional dependency.

        self.mp = mp
        vision = mp.tasks.vision
        self.landmarker = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=face_model),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.4,
                min_face_presence_confidence=0.4,
                min_tracking_confidence=0.4,
                output_facial_transformation_matrixes=True,
            )
        )
        self._timestamp_ms = -1

    def __call__(self, frame: Any) -> dict[str, Any]:
        import cv2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._timestamp_ms = max(self._timestamp_ms + 1, monotonic_ns() // 1_000_000)
        result = self.landmarker.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb), self._timestamp_ms
        )
        landmarks = (
            [(p.x, p.y, p.z) for p in result.face_landmarks[0]] if result.face_landmarks else []
        )
        matrices = result.facial_transformation_matrixes
        observation = extract_observation(
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
            landmarks,
            matrices[0] if len(matrices) else None,
        )
        observation["frame_size"] = [int(frame.shape[1]), int(frame.shape[0])]
        return observation

    def close(self) -> None:
        self.landmarker.close()


def fit_mapping(
    features: list[list[float]],
    targets: list[tuple[float, float]],
    qualities: list[float] | None = None,
    *,
    minimum_points: int = 9,
    parameters: MappingParameters | None = None,
) -> dict[str, Any]:
    """Fit additive eye/head models from within-target variation.

    All selection uses leave-location-out training folds, never validation data.
    Short robust blocks retain head movement at the same target. Locations
    receive equal total weight and all repeats of a held-out target stay out
    of that fold. Glare and binocular-difference cues cannot become gaze labels.
    """
    parameters = parameters or MappingParameters()
    x, y = np.asarray(features, dtype=float), np.asarray(targets, dtype=float)
    if x.ndim != 2 or y.shape != (len(x), 2) or len(x) < minimum_points * 5:
        raise ValueError("校准有效样本不足。")
    q = np.ones(len(x)) if qualities is None else np.asarray(qualities, dtype=float)
    if (
        not np.isfinite(x).all()
        or not np.isfinite(y).all()
        or q.shape != (len(x),)
        or not np.isfinite(q).all()
        or np.any((q <= 0) | (q > 1))
    ):
        raise ValueError("校准包含无效数据。")
    locations, groups = np.unique(y, axis=0, return_inverse=True)
    if len(locations) < minimum_points:
        raise ValueError("必须完成全部九个校准位置。")
    if np.any((y < 0) | (y > 1)):
        raise ValueError("校准目标必须位于屏幕内。")
    # A single median per target erased the data needed to distinguish head
    # movement from eye movement. Keep up to 12 short medians per location.
    centers = np.asarray([np.median(x[groups == i], axis=0) for i in range(len(locations))])
    representatives: list[Any] = []
    reliabilities: list[float] = []
    group_ids: list[int] = []
    noise_rows = np.zeros_like(x)
    stationary_rows = np.zeros_like(x)
    raw_reliability = np.zeros(len(x))
    for i in range(len(locations)):
        indices = np.flatnonzero(groups == i)
        if len(indices) < 5:
            raise ValueError("每个校准位置至少需要五个有效样本。")
        blocks = np.array_split(indices, min(12, max(1, len(indices) // 5)))
        block_quality = np.asarray([np.mean(q[block]) for block in blocks])
        block_quality /= block_quality.sum()
        for block in blocks:
            center = np.median(x[block], axis=0)
            representatives.append(center)
            residual = x[block] - center
            limit = np.maximum(4 * 1.4826 * np.median(np.abs(residual), axis=0), 1e-4)
            noise_rows[block] = np.clip(residual, -limit, limit)
        raw_reliability[indices] = q[indices] / q[indices].sum()
        # Long, same-target variation matters too: short blocks alone miss
        # drift and changes between repeated visits. Penalize the *joint*
        # screen displacement, allowing measured eye/head effects to cancel.
        deviation = x[indices] - centers[i]
        limit = np.maximum(4 * 1.4826 * np.median(np.abs(deviation), axis=0), 1e-4)
        stationary_rows[indices] = np.clip(deviation, -limit, limit)
        reliabilities.extend(block_quality)
        group_ids.extend([i] * len(blocks))
    rows = np.asarray(representatives)
    reliability = np.asarray(reliabilities)
    row_groups = np.asarray(group_ids)
    labels = locations[row_groups]
    mean = np.average(rows, axis=0, weights=reliability)
    scale = np.maximum(np.sqrt(np.average((rows - mean) ** 2, axis=0, weights=reliability)), 1e-4)
    if np.max(centers.std(axis=0)) < 1e-5:
        raise ValueError("各校准位置没有可区分的眼部变化，请调整姿势或光照。")

    def solve(
        rows: Any,
        labels: Any,
        reliability: Any,
        alpha: float,
        penalties: Any,
        noise: Any = None,
        noise_weights: Any = None,
        stationary: Any = None,
    ) -> Any:
        fold_mean = np.average(rows, axis=0, weights=reliability)
        fold_scale = np.maximum(
            np.sqrt(np.average((rows - fold_mean) ** 2, axis=0, weights=reliability)), 1e-4
        )
        design = np.column_stack(((rows - fold_mean) / fold_scale, np.ones(len(rows))))
        penalty = np.diag(np.append(penalties * alpha, 1e-8))
        if noise is not None:
            # Penalize amplification of correlated within-block variation.
            # This uses training frames only, in each fold's own units. A joint
            # eye/head motion that cancels in screen space is not penalized twice.
            normalized_noise = noise / fold_scale
            penalty[:-1, :-1] += (
                2 * normalized_noise.T @ (normalized_noise * noise_weights[:, None])
            )
            if stationary is not None:
                normalized_stationary = stationary / fold_scale
                penalty[:-1, :-1] += (
                    parameters.stationary_penalty
                    * normalized_stationary.T
                    @ (normalized_stationary * noise_weights[:, None])
                )
        weights = np.linalg.solve(
            design.T @ (design * reliability[:, None]) + penalty,
            design.T @ (labels * reliability),
        )
        return weights, fold_mean, fold_scale

    candidates = tuple(value * parameters.ridge_scale for value in (0.03, 0.1, 0.3, 1.0, 3.0, 10.0))
    bases = {"linear": list(range(x.shape[1]))}
    penalties = np.ones(x.shape[1])
    if x.shape[1] == FEATURE_COUNT:
        pose_columns = list(POSE_COLUMNS)
        if not np.all(rows[:, 15] == 1):
            pose_columns = [c for c in pose_columns if c < 13]
        bases = {
            "iris": [0, 1],
            "iris_lids": [0, 1, 11, 12],
            "iris_quadratic": [0, 1, 2, 3, 4],
            "iris_head": [0, 1, *pose_columns],
        }
        if 13 in pose_columns:
            bases.update(
                iris_angles=[0, 1, 13, 14],
                iris_lids_angles=[0, 1, 11, 12, 13, 14],
                iris_pose_position=[0, 1, 7, 8, 9, 10, 13, 14],
            )
        penalties[2:5] = 4
        penalties[11:13] = parameters.lid_penalty
        if not parameters.use_lids:
            bases = {name: columns for name, columns in bases.items() if 11 not in columns}
    weights = np.zeros((x.shape[1] + 1, 2))
    axis_models: dict[str, Any] = {}
    for axis, name in enumerate(("horizontal", "vertical")):
        trials = []
        for basis, columns in bases.items():
            selected_rows = rows[:, columns]
            for alpha in candidates:
                errors = []
                for i in range(len(locations)):
                    mask = row_groups != i
                    raw_mask = groups != i
                    fitted, fold_mean, fold_scale = solve(
                        selected_rows[mask],
                        labels[mask, axis],
                        reliability[mask],
                        alpha,
                        penalties[columns],
                        noise_rows[raw_mask][:, columns],
                        raw_reliability[raw_mask],
                        stationary_rows[raw_mask][:, columns],
                    )
                    design = np.column_stack(
                        (
                            (x[~raw_mask][:, columns] - fold_mean) / fold_scale,
                            np.ones((~raw_mask).sum()),
                        )
                    )
                    prediction = design @ fitted
                    errors.append(
                        float(
                            np.average(
                                # Bound gross tracking failures in training-only
                                # model selection, retaining all frames. Final
                                # independent validation errors remain uncapped.
                                np.minimum(np.abs(prediction - locations[i, axis]), 0.5),
                                weights=raw_reliability[~raw_mask],
                            )
                        )
                    )
                trials.append((float(np.mean(errors)), basis, alpha, errors))
        best = min(trials, key=lambda item: item[0])
        # Prefer fewer features within one standard error of the best fold score.
        # Sub-half-percent differences are not enough evidence to add nuisance
        # features from only nine locations (even for perfectly synthetic data).
        allowance = max(0.005, float(np.std(best[3]) / np.sqrt(len(locations))))
        eligible = [item for item in trials if item[0] <= best[0] + allowance]
        score, basis, alpha, _ = min(
            eligible, key=lambda item: (len(bases[item[1]]), -item[2], item[0])
        )
        columns = bases[basis]
        fitted, _, _ = solve(
            rows[:, columns],
            labels[:, axis],
            reliability,
            alpha,
            penalties[columns],
            noise_rows[:, columns],
            raw_reliability,
            stationary_rows[:, columns],
        )
        weights[columns, axis] = fitted[:-1]
        weights[-1, axis] = fitted[-1]
        axis_models[name] = {
            "basis": basis,
            "columns": columns,
            "regularization": alpha,
            "cv_capped_mae": score,
            "cv_error_cap": 0.5,
            "cv_evaluation": "all held-out frames; whole location excluded",
        }

    diagnostics = {}
    for axis, name in enumerate(("horizontal", "vertical")):
        if x.shape[1] <= axis:
            continue
        within = [
            np.median(np.abs(x[groups == i, axis] - centers[i, axis]))
            for i in range(len(locations))
        ]
        diagnostics[name] = {
            "iris_feature_span": float(np.ptp(centers[:, axis])),
            "within_point_mad": float(np.median(within)),
        }
    model: dict[str, Any] = {
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "weights": weights.tolist(),
        "mapping_method": (
            "ACIES within-fixation regularized mapping / frame-level leave-location-out"
        ),
        "feature_version": CALIBRATION_FEATURE_VERSION,
        "axis_models": axis_models,
        "training_signal": diagnostics,
    }
    if x.shape[1] == FEATURE_COUNT:
        model["pose_required"] = any(13 in item["columns"] for item in axis_models.values())
        support_columns = [7, 8, 9, 10, *([13, 14] if np.all(rows[:, 15] == 1) else [])]
        model["pose_support"] = {
            "columns": support_columns,
            "low": np.min(rows[:, support_columns], axis=0).tolist(),
            "high": np.max(rows[:, support_columns], axis=0).tolist(),
            "margin": [
                0.15,
                0.05,
                0.05,
                0.15,
                *([0.15, 0.15] if len(support_columns) == 6 else []),
            ],
        }
        # Personal binocular offset conditioned on gaze geometry and head pose.
        # No assumption of zero disparity; no interpretation as accommodation.
        mask = rows[:, 18] == 1
        if mask.sum() >= 15:
            columns = [0, 1, *pose_columns]
            baseline_weights = []
            for axis in (16, 17):
                fitted, baseline_mean, baseline_scale = solve(
                    rows[mask][:, columns],
                    rows[mask, axis],
                    reliability[mask],
                    0.3,
                    np.ones(len(columns)),
                )
                baseline_weights.append(fitted)
            design = np.column_stack(
                ((rows[mask][:, columns] - baseline_mean) / baseline_scale, np.ones(mask.sum()))
            )
            baseline_matrix = np.asarray(baseline_weights).T
            residual = rows[mask, 16:18] - design @ baseline_matrix
            model["binocular_baseline"] = {
                "columns": columns,
                "mean": baseline_mean.tolist(),
                "scale": baseline_scale.tolist(),
                "weights": baseline_matrix.tolist(),
                "tolerance": np.maximum(
                    np.quantile(np.abs(residual), 0.95, axis=0) * 3, [0.035, 0.025]
                ).tolist(),
            }
    return model


def validate_observation_model(model: dict[str, Any]) -> None:
    """Reject corrupt optional diagnostic models before opening a camera."""
    if type(model.get("pose_required", False)) is not bool:
        raise ValueError("Invalid pose requirement")
    detail = model.get("image_detail_reference", 0)
    if not isinstance(detail, (int, float)) or not np.isfinite(detail) or detail < 0:
        raise ValueError("Invalid image-detail reference")
    for key in ("pose_support", "binocular_baseline"):
        if key not in model:
            continue
        value = model[key]
        if not isinstance(value, dict):
            raise ValueError("Invalid observation model")
        columns = value["columns"]
        if (
            not isinstance(columns, list)
            or not columns
            or any(type(c) is not int or not 0 <= c < 15 for c in columns)
            or len(set(columns)) != len(columns)
        ):
            raise ValueError("Invalid observation columns")
        fields = ("low", "high", "margin") if key == "pose_support" else ("mean", "scale")
        for field in fields:
            array = np.asarray(value[field], dtype=float)
            if array.shape != (len(columns),) or not np.isfinite(array).all():
                raise ValueError("Invalid observation coefficients")
        if key == "pose_support":
            if np.any(np.array(value["low"]) > value["high"]) or np.any(
                np.array(value["margin"]) < 0
            ):
                raise ValueError("Invalid pose support")
        else:
            weights = np.asarray(value["weights"], dtype=float)
            tolerance = np.asarray(value["tolerance"], dtype=float)
            if (
                weights.shape != (len(columns) + 1, 2)
                or not np.isfinite(weights).all()
                or tolerance.shape != (2,)
                or not np.isfinite(tolerance).all()
                or np.any(tolerance <= 0)
                or np.any(np.array(value["scale"]) <= 0)
            ):
                raise ValueError("Invalid binocular baseline")


class ObservationMonitor:
    """Engineering observations only; never labels optical focus or attention.

    Sustained changes are relative to this person's calibrated binocular state,
    after pose conditioning. Blinks and missing data clear temporal evidence.
    """

    def __init__(self) -> None:
        self._candidate = ""
        self._since = 0
        self._last = 0

    def reset(self) -> None:
        self._candidate = ""
        self._since = self._last = 0

    def update(self, packet: dict[str, Any], model: dict[str, Any]) -> str:
        features = packet.get("features")
        if features is None:
            self.reset()
            return "unavailable"
        x = np.asarray(features, dtype=float)
        if model.get("pose_required") and x[15] != 1:
            self.reset()
            return "pose_unavailable"
        state = "uncalibrated"
        if packet.get("mode") != "binocular":
            state = "monocular"
        elif baseline := model.get("binocular_baseline"):
            if x[15] != 1 and any(c in (13, 14) for c in baseline["columns"]):
                # An eye-only gaze model may still have a pose-conditioned
                # diagnostic baseline. Missing angles are not measured zeros.
                state = "comparison_unavailable"
            else:
                normalized = (x[baseline["columns"]] - baseline["mean"]) / baseline["scale"]
                expected = np.append(normalized, 1) @ np.asarray(baseline["weights"])
                changed = np.any(np.abs(x[16:18] - expected) > baseline["tolerance"])
                state = "binocular_changed" if changed else "consistent"
        eyes = [item for item in packet.get("eye_diagnostics", {}).values() if item.get("valid")]
        reference = model.get("image_detail_reference", 0)
        if (
            eyes
            and reference > 0.01
            and max(e.get("image_detail", 0) for e in eyes) < reference * 0.35
        ):
            state = "image_detail_low"
        if eyes and max(e.get("glare_ratio", 0) for e in eyes) > 0.35:
            state = "reflection"
        if support := model.get("pose_support"):
            pose = x[support["columns"]]
            observed = np.array([x[15] == 1 or c not in (13, 14) for c in support["columns"]])
            if np.any(observed & (pose < np.array(support["low"]) - support["margin"])) or np.any(
                observed & (pose > np.array(support["high"]) + support["margin"])
            ):
                state = "pose_outside_calibration"
        timestamp = int(packet["timestamp_ns"])
        if state in {"consistent", "uncalibrated", "monocular", "comparison_unavailable"}:
            self.reset()
            return state
        if state != self._candidate or not 0 < timestamp - self._last <= 300_000_000:
            self._candidate, self._since = state, timestamp
        self._last = timestamp
        return state if timestamp - self._since >= 500_000_000 else "observing_change"


class GazeSmoother:
    """Causal median + signed-velocity 1-Euro-style filtering, in screen units.

    Casiez et al., CHI 2012: https://gery.casiez.net/1euro/
    Original implementation of the published principle, not copied source.
    A three-frame median rejects isolated spikes; filtered signed derivatives
    distinguish alternating jitter from sustained motion. No target, calibration
    or future frames are used, and no coordinates are extrapolated through loss.
    """

    def __init__(
        self,
        *,
        min_cutoff: float = 0.45,
        speed_gain: float = 4.0,
        derivative_cutoff: float = 1.0,
        median_window: int = 3,
    ) -> None:
        if (
            not np.isfinite([min_cutoff, speed_gain, derivative_cutoff]).all()
            or min_cutoff <= 0
            or speed_gain < 0
            or derivative_cutoff <= 0
            or type(median_window) is not int
            or median_window not in (1, 3, 5)
        ):
            raise ValueError("Invalid gaze filter parameters")
        self.min_cutoff, self.speed_gain = min_cutoff, speed_gain
        self.derivative_cutoff, self.median_window = derivative_cutoff, median_window
        self.point: Any = None
        self.timestamp_ns = 0
        self._history: deque[Any] = deque(maxlen=median_window)
        self._last_input: Any = None
        self._velocity = np.zeros(2)

    def reset(self) -> None:
        self.point = None
        self.timestamp_ns = 0
        self._history.clear()
        self._last_input = None
        self._velocity = np.zeros(2)

    def update(
        self, point: tuple[float, float], timestamp_ns: int, quality: float
    ) -> tuple[float, float]:
        current = np.asarray(point, dtype=float)
        if current.shape != (2,) or not np.isfinite(current).all() or not np.isfinite(quality):
            self.reset()
            raise ValueError("Invalid gaze smoothing sample")
        dt = (timestamp_ns - self.timestamp_ns) / 1e9
        if self.point is None or not 0 < dt <= 0.25:
            self.reset()
            self.point = current.copy()
            self._last_input = current.copy()
            self._history.append(current.copy())
        else:
            self._history.append(current.copy())
            if len(self._history) == self.median_window:
                current = np.median(np.asarray(self._history), axis=0)
            derivative = (current - self._last_input) / dt
            derivative_alpha = 1 / (1 + 1 / (2 * pi * self.derivative_cutoff * dt))
            self._velocity += derivative_alpha * (derivative - self._velocity)
            self._last_input = current.copy()
            quality = float(np.clip(quality, 0, 1))
            cutoff = self.min_cutoff + 0.30 * quality + self.speed_gain * np.abs(self._velocity)
            alpha = 1 / (1 + 1 / (2 * pi * cutoff * dt))
            self.point += alpha * (current - self.point)
        self.timestamp_ns = timestamp_ns
        return float(self.point[0]), float(self.point[1])
