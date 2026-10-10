"""Standalone three-point aSeeGlasses Plus calibration helper.

This module is a one-shot child process. It never maps scene-camera gaze onto an
OculiDoC patient screen and never saves scene images or raw gaze samples.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import math
import os
import re
import sys
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .seveninvensun_glasses_sdk import (
    ENVIRONMENTS,
    RESOLUTION_DIMENSIONS,
    RESOLUTIONS,
    SeveninvensunGlassesSdkCapture,
    SeveninvensunSdkError,
    classify_device_disconnect_terminal_state,
)

CALIBRATION_PROTOCOL = "oculidoc-seveninvensun-glasses-calibration-v1"
CALIBRATION_POINT_COUNT = 3
COEFFICIENT_SIZE = 1024
MINIMUM_VERIFICATION_SAMPLES = 30
MINIMUM_VALID_GAZE_SAMPLES = 10
MINIMUM_VALID_GAZE_RATIO = 0.20
MAX_SCENE_FRAME_AGE_SECONDS = 1.0
PROFILE_ID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
POINT_GUIDANCE = ("左上区域", "右上区域", "下方中央区域")


def map_aspect_fit_click(
    *,
    widget_width: float,
    widget_height: float,
    image_width: int,
    image_height: int,
    click_x: float,
    click_y: float,
) -> tuple[float, float] | None:
    """Map a click through aspect-fit letterboxing into native top-left image pixels."""
    if min(widget_width, widget_height, image_width, image_height) <= 0:
        return None
    scale = min(widget_width / image_width, widget_height / image_height)
    rendered_width = image_width * scale
    rendered_height = image_height * scale
    left = (widget_width - rendered_width) / 2.0
    top = (widget_height - rendered_height) / 2.0
    if not (left <= click_x < left + rendered_width):
        return None
    if not (top <= click_y < top + rendered_height):
        return None
    return ((click_x - left) / scale, (click_y - top) / scale)


def subject_hash(profile_id: str) -> str:
    """Hash a canonical opaque patient UUID without treating the hash as anonymization."""
    if not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError("profile id must be a canonical opaque patient UUID.")
    parsed = uuid.UUID(profile_id)
    if parsed.int == 0 or str(parsed) != profile_id.lower():
        raise ValueError("profile id must be a canonical non-zero opaque patient UUID.")
    return hashlib.sha256(b"oculidoc-asee-profile-v1\x00" + parsed.bytes).hexdigest()


def scene_frame_is_fresh(
    frame: Any,
    *,
    now_monotonic_ns: int | None = None,
    maximum_age_seconds: float = MAX_SCENE_FRAME_AGE_SECONDS,
) -> bool:
    """Return true only for a recent, complete BGR frame from this decoder contract."""
    if frame is None or maximum_age_seconds <= 0:
        return False
    expected_size = int(frame.width) * int(frame.height) * 3
    if frame.decoder_result_raw != expected_size or len(frame.bgr_bytes) != expected_size:
        return False
    now = time.perf_counter_ns() if now_monotonic_ns is None else now_monotonic_ns
    age_ns = now - int(frame.host_monotonic_ns)
    return 0 <= age_ns <= maximum_age_seconds * 1_000_000_000


def calibration_geometry_issues(
    points: Iterable[tuple[float, float]],
    *,
    image_width: int,
    image_height: int,
) -> list[str]:
    """Reject duplicate/near-collinear scene targets before native collection."""
    point_list = list(points)
    issues: list[str] = []
    if image_width <= 0 or image_height <= 0:
        return ["invalid_scene_dimensions"]
    if len(point_list) > CALIBRATION_POINT_COUNT:
        return ["too_many_calibration_points"]
    for x, y in point_list:
        if not math.isfinite(x) or not math.isfinite(y):
            issues.append("nonfinite_calibration_point")
        elif not (0 <= x < image_width and 0 <= y < image_height):
            issues.append("calibration_point_outside_scene")
    minimum_distance = min(image_width, image_height) * 0.20
    for first_index, first in enumerate(point_list):
        for second in point_list[first_index + 1 :]:
            if math.dist(first, second) < minimum_distance:
                issues.append("calibration_points_too_close")
    if len(point_list) == CALIBRATION_POINT_COUNT:
        first, second, third = point_list
        doubled_area = abs(
            (second[0] - first[0]) * (third[1] - first[1])
            - (third[0] - first[0]) * (second[1] - first[1])
        )
        if doubled_area < image_width * image_height * 0.10:
            issues.append("calibration_triangle_area_too_small")
    return list(dict.fromkeys(issues))


def evaluate_post_calibration(
    samples: Iterable[dict[str, Any]],
    *,
    scene_width: int,
    scene_height: int,
    received_callback_samples: int,
    dropped_callback_samples: int,
    discarded_callback_samples: int,
    pending_callback_samples: int,
    post_stop_callback_samples: int,
) -> dict[str, Any]:
    """Apply an engineering output-validity gate, not an accuracy/clinical claim."""
    sample_list = list(samples)
    valid_counts = {"recommended": 0, "left": 0, "right": 0}
    score_counts = {"recommended": 0, "left": 0, "right": 0}
    timestamps: list[int] = []
    for sample in sample_list:
        timestamp = sample.get("device_timestamp_raw")
        if isinstance(timestamp, int) and not isinstance(timestamp, bool):
            timestamps.append(timestamp)
        validity = sample.get("validity")
        gaze = sample.get("gaze")
        if not isinstance(validity, dict) or not isinstance(gaze, dict):
            continue
        for name, source_name in (
            ("recommended", "eye"),
            ("left", "left"),
            ("right", "right"),
        ):
            payload = gaze.get(name)
            if validity.get(source_name) is not True or not isinstance(payload, dict):
                continue
            point = payload.get("gaze_point_scene_top_left_px")
            if (
                isinstance(point, list)
                and len(point) >= 2
                and all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in point[:2]
                )
                and 0 <= float(point[0]) < scene_width
                and 0 <= float(point[1]) < scene_height
            ):
                valid_counts[name] += 1
            score = payload.get("calibration_score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                if math.isfinite(float(score)):
                    score_counts[name] += 1

    nonpositive_deltas = sum(
        current <= previous for previous, current in zip(timestamps, timestamps[1:], strict=False)
    )
    issues: list[str] = []
    if len(sample_list) < MINIMUM_VERIFICATION_SAMPLES:
        issues.append("fewer_than_30_fresh_callbacks")
    if len(timestamps) != len(sample_list):
        issues.append("missing_device_timestamp")
    for name in ("recommended", "left", "right"):
        if valid_counts[name] < MINIMUM_VALID_GAZE_SAMPLES:
            issues.append(f"fewer_than_10_valid_{name}_gaze_samples")
        if sample_list and valid_counts[name] / len(sample_list) < MINIMUM_VALID_GAZE_RATIO:
            issues.append(f"valid_{name}_gaze_ratio_below_20_percent")
    if nonpositive_deltas:
        issues.append("device_timestamp_not_strictly_increasing")
    if dropped_callback_samples:
        issues.append("callback_queue_dropped_samples")
    if pending_callback_samples:
        issues.append("callback_queue_not_fully_consumed")
    if post_stop_callback_samples:
        issues.append("callback_observed_after_stop")
    if received_callback_samples != (
        len(sample_list)
        + dropped_callback_samples
        + discarded_callback_samples
        + pending_callback_samples
    ):
        issues.append("callback_accounting_mismatch")

    total = len(sample_list)
    return {
        "result": "PASS" if not issues else "REVIEW_REQUIRED",
        "sample_count": total,
        "valid_samples": valid_counts,
        "calibration_score_samples": score_counts,
        "valid_ratio_percent": {
            name: count / total * 100.0 if total else 0.0 for name, count in valid_counts.items()
        },
        "nonpositive_device_timestamp_deltas": nonpositive_deltas,
        "received_callback_samples": received_callback_samples,
        "dropped_callback_samples": dropped_callback_samples,
        "discarded_callback_samples": discarded_callback_samples,
        "pending_callback_samples": pending_callback_samples,
        "post_stop_callback_samples": post_stop_callback_samples,
        "issues": issues,
    }


def verification_summary_is_publishable(summary: Any) -> bool:
    """Validate the complete technical gate instead of trusting its result label."""
    if not isinstance(summary, dict) or summary.get("result") != "PASS":
        return False
    if summary.get("issues") != []:
        return False

    def strict_int(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    sample_count = strict_int(summary.get("sample_count"))
    received = strict_int(summary.get("received_callback_samples"))
    dropped = strict_int(summary.get("dropped_callback_samples"))
    discarded = strict_int(summary.get("discarded_callback_samples"))
    pending = strict_int(summary.get("pending_callback_samples"))
    post_stop = strict_int(summary.get("post_stop_callback_samples"))
    nonpositive = strict_int(summary.get("nonpositive_device_timestamp_deltas"))
    if (
        sample_count is None
        or sample_count < MINIMUM_VERIFICATION_SAMPLES
        or received is None
        or dropped != 0
        or discarded is None
        or discarded < 0
        or pending != 0
        or post_stop != 0
        or nonpositive != 0
        or received != sample_count + discarded
    ):
        return False
    valid_counts = summary.get("valid_samples")
    valid_ratios = summary.get("valid_ratio_percent")
    if not isinstance(valid_counts, dict) or not isinstance(valid_ratios, dict):
        return False
    for name in ("recommended", "left", "right"):
        count = strict_int(valid_counts.get(name))
        ratio = valid_ratios.get(name)
        if count is None or count < MINIMUM_VALID_GAZE_SAMPLES:
            return False
        if (
            not isinstance(ratio, (int, float))
            or isinstance(ratio, bool)
            or not math.isfinite(float(ratio))
            or not MINIMUM_VALID_GAZE_RATIO * 100.0 <= float(ratio) <= 100.0
        ):
            return False
        expected_ratio = count / sample_count * 100.0
        if count > sample_count or not math.isclose(
            float(ratio),
            expected_ratio,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            return False
    return True


def native_cleanup_is_publishable(metadata: Any) -> bool:
    """Require successful native teardown and a stable device binding before persistence."""
    if not isinstance(metadata, dict):
        return False
    cleanup = metadata.get("native_cleanup_results")
    if not isinstance(cleanup, dict):
        return False
    disconnect_result = cleanup.get("device_disconnect")
    terminal_state = classify_device_disconnect_terminal_state(
        authorization_established=metadata.get("authorization_established") is True,
        sdk_stop_result=cleanup.get("sdk_stop"),
        device_disconnect_result=disconnect_result,
    )
    return (
        metadata.get("device_binding_rechecked_before_stop") is True
        and terminal_state is not None
        and metadata.get("device_disconnect_terminal_state") == terminal_state
        and cleanup
        == {
            "calibration_complete": 0,
            "sdk_stop": 0,
            "device_disconnect": disconnect_result,
            "scene_decoder_release": 0,
            "scene_decoder_error": None,
        }
    )


def _write_bytes(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    _write_bytes(path, encoded)


def cleanup_stale_bundle_temps(
    output_dir: Path,
    *,
    minimum_age_seconds: float = 24 * 60 * 60,
) -> int:
    """Remove only exact temporary files created by this calibration publisher."""
    if minimum_age_seconds < 0:
        raise ValueError("minimum_age_seconds cannot be negative.")
    if not output_dir.exists():
        return 0
    pattern = re.compile(r"\.asee_glasses_[0-9a-f]{64}\.aseecal\.[0-9a-f]{32}\.tmp\Z")
    removed = 0
    oldest_allowed_mtime = time.time() - minimum_age_seconds
    for candidate in output_dir.iterdir():
        if (
            pattern.fullmatch(candidate.name)
            and candidate.is_file()
            and not candidate.is_symlink()
            and candidate.stat().st_mtime <= oldest_allowed_mtime
        ):
            candidate.unlink()
            removed += 1
    return removed


def save_verified_calibration_bundle(
    output_dir: Path,
    *,
    profile_hash: str,
    device_hash: str,
    left: bytes,
    right: bytes,
    metadata: dict[str, Any],
) -> Path:
    """Atomically replace one profile-bound, device-bound calibration bundle."""
    if len(left) != COEFFICIENT_SIZE or len(right) != COEFFICIENT_SIZE:
        raise ValueError("Both calibration coefficients must be exactly 1024 bytes.")
    if not HASH_PATTERN.fullmatch(profile_hash) or not HASH_PATTERN.fullmatch(device_hash):
        raise ValueError("Profile and device bindings must be SHA-256 hex digests.")
    runtime = metadata.get("runtime_integrity")
    verification = metadata.get("verification")
    if not isinstance(runtime, dict) or not HASH_PATTERN.fullmatch(
        str(runtime.get("runtime_bundle_sha256", ""))
    ):
        raise ValueError("A full SDK runtime binding is required.")
    if not verification_summary_is_publishable(verification):
        raise ValueError("Only a technical output-validity PASS may be published.")
    if not native_cleanup_is_publishable(metadata):
        raise ValueError("Successful native cleanup and stable device binding are required.")
    if not isinstance(metadata.get("sdk_version"), str) or not metadata["sdk_version"]:
        raise ValueError("An SDK version binding is required.")
    if metadata.get("environment") not in ENVIRONMENTS:
        raise ValueError("A valid environment binding is required.")
    if metadata.get("resolution") not in RESOLUTIONS:
        raise ValueError("A valid resolution binding is required.")

    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_stale_bundle_temps(output_dir)
    final_path = output_dir / f"asee_glasses_{profile_hash}.aseecal"
    temporary_path = output_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp"
    manifest = {
        **metadata,
        "protocol": CALIBRATION_PROTOCOL,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "device_family": "aSeeGlasses Plus",
        "profile_id_sha256": profile_hash,
        "device_mpu_id_sha256": device_hash,
        "point_count": CALIBRATION_POINT_COUNT,
        "coordinate_space": "scene_camera_centered_px",
        "scene_y_axis": "positive_down",
        "screen_mapping_status": "not_mapped",
        "biometric_calibration_material": True,
        "technical_output_validity_only": True,
        "coefficients": {
            "left": {
                "encoding": "base64",
                "size_bytes": len(left),
                "sha256": hashlib.sha256(left).hexdigest(),
                "data": base64.b64encode(left).decode("ascii"),
            },
            "right": {
                "encoding": "base64",
                "size_bytes": len(right),
                "sha256": hashlib.sha256(right).hexdigest(),
                "data": base64.b64encode(right).decode("ascii"),
            },
        },
    }
    try:
        _write_json(temporary_path, manifest)
        os.replace(temporary_path, final_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return final_path


def load_calibration_bundle(
    bundle_path: Path,
    *,
    expected_profile_hash: str,
    expected_device_hash: str,
    expected_sdk_version: str,
    expected_runtime_bundle_hash: str,
    expected_environment: str,
    expected_resolution: str,
) -> tuple[bytes, bytes]:
    """Validate every safety binding before returning the in-memory coefficient pair."""
    for value in (
        expected_profile_hash,
        expected_device_hash,
        expected_runtime_bundle_hash,
    ):
        if not HASH_PATTERN.fullmatch(value):
            raise SeveninvensunSdkError("Calibration bundle expectations are invalid.")
    if bundle_path.is_symlink() or not bundle_path.is_file():
        raise SeveninvensunSdkError("Calibration bundle is not a regular file.")
    if bundle_path.stat().st_size > 128 * 1024:
        raise SeveninvensunSdkError("Calibration bundle exceeds the allowed size.")
    try:
        manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SeveninvensunSdkError("Calibration bundle cannot be decoded.") from error
    if not isinstance(manifest, dict):
        raise SeveninvensunSdkError("Calibration bundle root must be an object.")

    expected_fields = {
        "protocol": CALIBRATION_PROTOCOL,
        "device_family": "aSeeGlasses Plus",
        "profile_id_sha256": expected_profile_hash,
        "device_mpu_id_sha256": expected_device_hash,
        "sdk_version": expected_sdk_version,
        "environment": expected_environment,
        "resolution": expected_resolution,
        "point_count": CALIBRATION_POINT_COUNT,
        "coordinate_space": "scene_camera_centered_px",
        "screen_mapping_status": "not_mapped",
        "biometric_calibration_material": True,
        "technical_output_validity_only": True,
    }
    for field, expected in expected_fields.items():
        actual = manifest.get(field)
        if isinstance(expected, str) and isinstance(actual, str):
            matched = hmac.compare_digest(actual, expected)
        else:
            matched = type(actual) is type(expected) and actual == expected
        if not matched:
            raise SeveninvensunSdkError(f"Calibration bundle binding mismatch: {field}.")
    runtime = manifest.get("runtime_integrity")
    if not isinstance(runtime, dict) or not hmac.compare_digest(
        str(runtime.get("runtime_bundle_sha256", "")),
        expected_runtime_bundle_hash,
    ):
        raise SeveninvensunSdkError("Calibration bundle runtime binding mismatch.")
    verification = manifest.get("verification")
    if not verification_summary_is_publishable(verification):
        raise SeveninvensunSdkError("Calibration bundle lacks a technical validity PASS.")
    if not native_cleanup_is_publishable(manifest):
        raise SeveninvensunSdkError("Calibration bundle lacks verified native cleanup.")

    coefficients = manifest.get("coefficients")
    if not isinstance(coefficients, dict) or set(coefficients) != {"left", "right"}:
        raise SeveninvensunSdkError("Calibration bundle coefficient pair is invalid.")
    decoded: list[bytes] = []
    for eye in ("left", "right"):
        entry = coefficients.get(eye)
        if not isinstance(entry, dict) or entry.get("encoding") != "base64":
            raise SeveninvensunSdkError("Calibration bundle coefficient encoding is invalid.")
        encoded = entry.get("data")
        try:
            raw = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else b""
        except ValueError as error:
            raise SeveninvensunSdkError(
                "Calibration bundle coefficient encoding is invalid."
            ) from error
        digest = hashlib.sha256(raw).hexdigest()
        if (
            len(raw) != COEFFICIENT_SIZE
            or entry.get("size_bytes") != COEFFICIENT_SIZE
            or not isinstance(entry.get("sha256"), str)
            or not hmac.compare_digest(entry["sha256"], digest)
        ):
            raise SeveninvensunSdkError("Calibration bundle coefficient integrity failed.")
        decoded.append(raw)
    return decoded[0], decoded[1]


def write_attempt_report(output_dir: Path, payload: dict[str, Any]) -> Path:
    """Write a non-biometric failure/review report without overwriting another run."""
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:8]}"
    final_path = output_dir / f"asee_glasses_calibration_attempt_{run_id}.json"
    temporary = final_path.with_name(f".{final_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_json(temporary, payload)
        temporary.replace(final_path)
    finally:
        temporary.unlink(missing_ok=True)
    return final_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone aSeeGlasses Plus three-point scene calibration.",
    )
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--profile-id",
        required=True,
        help="Canonical opaque OculiDoC patient UUID; never use a name or record number.",
    )
    parser.add_argument("--environment", choices=tuple(ENVIRONMENTS), default="indoor")
    parser.add_argument("--resolution", choices=tuple(RESOLUTIONS), default="1280x960")
    parser.add_argument("--verification-seconds", type=float, default=5.0)
    parser.add_argument("--point-timeout-seconds", type=float, default=20.0)
    return parser


def _run_qt(
    arguments: argparse.Namespace,
    *,
    profile_hash: str,
) -> int:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    class SceneLabel(QtWidgets.QLabel):
        native_clicked = QtCore.Signal(float, float)

        def __init__(self) -> None:
            super().__init__()
            self._image: Any = None
            self._native_size: tuple[int, int] | None = None
            self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.setMinimumSize(640, 480)
            self.setStyleSheet("background:#111; border:1px solid #555;")
            self.setText("等待眼镜场景画面……")

        def set_scene_frame(self, frame: Any) -> None:
            image = QtGui.QImage(
                frame.bgr_bytes,
                frame.width,
                frame.height,
                frame.width * 3,
                QtGui.QImage.Format.Format_BGR888,
            ).copy()
            self._image = image
            self._native_size = (frame.width, frame.height)
            self._render()

        def _render(self) -> None:
            if self._image is None:
                return
            pixmap = QtGui.QPixmap.fromImage(self._image).scaled(
                self.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            self.setPixmap(pixmap)

        def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            super().resizeEvent(event)
            self._render()

        def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if event.button() != QtCore.Qt.MouseButton.LeftButton or self._native_size is None:
                return
            width, height = self._native_size
            position = event.position()
            mapped = map_aspect_fit_click(
                widget_width=float(self.width()),
                widget_height=float(self.height()),
                image_width=width,
                image_height=height,
                click_x=float(position.x()),
                click_y=float(position.y()),
            )
            if mapped is not None:
                self.native_clicked.emit(*mapped)

    class CalibrationWindow(QtWidgets.QWidget):
        def __init__(
            self,
            capture: SeveninvensunGlassesSdkCapture,
            sdk_version: str,
            device_hash: str,
        ) -> None:
            super().__init__()
            self.capture = capture
            self.sdk_version = sdk_version
            self.device_hash = device_hash
            self.state = "ready"
            self.current_index = 0
            self.finish_status: dict[str, int | None] = {"left": None, "right": None}
            self.progress_status = {"left": 0, "right": 0}
            self.point_deadline = 0.0
            self.verification_deadline = 0.0
            self.verification_sequence_start = 0
            self.received_at_verification_start = 0
            self.verification_samples: list[dict[str, Any]] = []
            self.dropped_at_verification_start = 0
            self.discarded_at_verification_start = 0
            self.pending_coefficients: tuple[bytes, bytes] | None = None
            self.vendor_score: str | None = None
            self.clicked_points: list[dict[str, list[float]]] = []
            self.current_clicked_point: dict[str, list[float]] | None = None
            self.last_frame_sequence = -1
            self.exit_code = 2

            self.setWindowTitle("OculiDoC · aSeeGlasses Plus 三点校准")
            self.resize(1000, 820)
            layout = QtWidgets.QVBoxLayout(self)
            title = QtWidgets.QLabel("aSeeGlasses Plus · 场景相机三点校准")
            title.setStyleSheet("font-size:22px;font-weight:600;")
            layout.addWidget(title)
            self.instructions = QtWidgets.QLabel(
                "让佩戴者注视现实中的清晰目标；操作者在场景画面中点击同一目标。"
                f" 当前患者 UUID 指纹：{profile_hash[:12]}。"
            )
            self.instructions.setWordWrap(True)
            layout.addWidget(self.instructions)
            self.scene = SceneLabel()
            self.scene.native_clicked.connect(self._on_scene_clicked)
            layout.addWidget(self.scene, 1)

            progress_layout = QtWidgets.QHBoxLayout()
            self.point_labels: list[Any] = []
            for index in range(1, CALIBRATION_POINT_COUNT + 1):
                label = QtWidgets.QLabel(f"点 {index}：待开始")
                progress_layout.addWidget(label)
                self.point_labels.append(label)
            layout.addLayout(progress_layout)

            self.status = QtWidgets.QLabel("设备已连接；等待第一帧场景画面。")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            button_layout = QtWidgets.QHBoxLayout()
            self.start_button = QtWidgets.QPushButton("开始三点校准")
            self.start_button.setEnabled(False)
            self.start_button.clicked.connect(self._begin)
            button_layout.addWidget(self.start_button)
            close_button = QtWidgets.QPushButton("取消并关闭")
            close_button.clicked.connect(self.close)
            button_layout.addWidget(close_button)
            layout.addLayout(button_layout)

            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self._safe_poll)
            self.timer.start(33)

        def _safe_poll(self) -> None:
            try:
                self._poll()
            except Exception as error:  # noqa: BLE001 - contain Qt timer/native failures
                self._fail("校准状态轮询失败", error)

        def _poll(self) -> None:
            frame = self.capture.latest_scene_frame()
            frame_is_fresh = scene_frame_is_fresh(frame)
            if frame is not None and frame_is_fresh and frame.sequence != self.last_frame_sequence:
                self.last_frame_sequence = frame.sequence
                self.scene.set_scene_frame(frame)
                if self.state == "ready":
                    self.start_button.setEnabled(self.capture.scene_decoder_running)
                    self.status.setText("场景画面已就绪。点击“开始三点校准”。")
            if self.state == "ready":
                self.start_button.setEnabled(frame_is_fresh and self.capture.scene_decoder_running)
            if self.state in {"waiting_click", "collecting", "verifying"} and (
                not frame_is_fresh or not self.capture.scene_decoder_running
            ):
                detail = self.capture.scene_decoder_error or "场景画面已停止或超过 1 秒未更新"
                raise SeveninvensunSdkError(detail)

            while True:
                event = self.capture.read_calibration_event()
                if event is None:
                    break
                self._handle_calibration_event(event)

            if self.state == "collecting" and time.monotonic() >= self.point_deadline:
                self.capture.cancel_calibration_point(timeout_seconds=3.0)
                self.state = "waiting_click"
                self.current_clicked_point = None
                self.point_labels[self.current_index - 1].setText(
                    f"点 {self.current_index}：超时，请重试"
                )
                self.status.setText("该点未在限定时间内完成；请重新注视并点击同一目标。")

            if self.state == "verifying":
                self._drain_verification_samples()
                if time.monotonic() >= self.verification_deadline:
                    self._finish_verification()

        def _begin(self) -> None:
            frame = self.capture.latest_scene_frame()
            if not scene_frame_is_fresh(frame) or not self.capture.scene_decoder_running:
                self.status.setText("场景画面尚未稳定，请等待实时画面。")
                return
            try:
                self.capture.begin_calibration(CALIBRATION_POINT_COUNT)
            except Exception as error:  # noqa: BLE001 - show the native SDK result
                self._fail("无法开始校准", error)
                return
            self.state = "waiting_click"
            self.current_index = 1
            self.start_button.setEnabled(False)
            self.point_labels[0].setText("点 1：等待点击")
            self.status.setText(
                f"佩戴者注视{POINT_GUIDANCE[0]}的现实目标；操作者在场景画面中点击它。"
            )

        def _on_scene_clicked(self, native_x: float, native_y: float) -> None:
            if self.state != "waiting_click":
                return
            frame = self.capture.latest_scene_frame()
            if not scene_frame_is_fresh(frame) or frame is None:
                self.status.setText("场景画面已过期，请等待实时画面后再点击。")
                return
            candidate_points = [
                (float(point["top_left_px"][0]), float(point["top_left_px"][1]))
                for point in self.clicked_points
            ]
            candidate_points.append((native_x, native_y))
            geometry_issues = calibration_geometry_issues(
                candidate_points,
                image_width=frame.width,
                image_height=frame.height,
            )
            if geometry_issues:
                self.status.setText(
                    "目标分布过近或接近共线；请按左上、右上、下方中央重新选择当前点。"
                )
                return
            centered_x = native_x - frame.width / 2.0
            centered_y = native_y - frame.height / 2.0
            self.finish_status = {"left": None, "right": None}
            self.progress_status = {"left": 0, "right": 0}
            try:
                self.capture.start_calibration_point(
                    self.current_index,
                    centered_x,
                    centered_y,
                )
            except Exception as error:  # noqa: BLE001 - the same point remains retryable
                self._fail("校准点未安全启动", error)
                return
            self.current_clicked_point = {
                "top_left_px": [native_x, native_y],
                "centered_px": [centered_x, centered_y],
            }
            self.state = "collecting"
            self.point_deadline = time.monotonic() + arguments.point_timeout_seconds
            self.point_labels[self.current_index - 1].setText(
                f"点 {self.current_index}：左 0% / 右 0%"
            )
            self.status.setText("保持注视，等待左右眼均完成。")

        def _handle_calibration_event(self, event: Any) -> None:
            if self.state != "collecting" or event.index != self.current_index:
                return
            if event.kind == "progress":
                self.progress_status[event.eye] = event.value
                self.point_labels[self.current_index - 1].setText(
                    f"点 {self.current_index}：左 {self.progress_status['left']}% / "
                    f"右 {self.progress_status['right']}%"
                )
                return
            if event.kind != "finish":
                return
            self.finish_status[event.eye] = event.value
            if any(value is None for value in self.finish_status.values()):
                return
            if self.finish_status == {"left": 0, "right": 0}:
                if self.current_clicked_point is not None:
                    self.clicked_points.append(self.current_clicked_point)
                self.current_clicked_point = None
                self.point_labels[self.current_index - 1].setText(f"点 {self.current_index}：完成")
                if self.current_index < CALIBRATION_POINT_COUNT:
                    self.current_index += 1
                    self.state = "waiting_click"
                    self.point_labels[self.current_index - 1].setText(
                        f"点 {self.current_index}：等待点击"
                    )
                    self.status.setText(
                        f"佩戴者注视{POINT_GUIDANCE[self.current_index - 1]}的现实目标，"
                        "操作者在画面中点击它。"
                    )
                else:
                    self._compute_and_verify()
            else:
                self.state = "waiting_click"
                self.current_clicked_point = None
                self.point_labels[self.current_index - 1].setText(
                    f"点 {self.current_index}：失败，请重试"
                )
                self.status.setText("左右眼未同时成功；保持佩戴位置后重试同一点。")

        def _compute_and_verify(self) -> None:
            self.state = "computing"
            self.status.setText("正在计算并应用左右眼校准系数……")
            try:
                left, right, self.vendor_score = self.capture.compute_calibration_coefficients()
                self.capture.apply_calibration_coefficients(
                    left,
                    right,
                    expected_device_hash=self.device_hash,
                )
            except Exception as error:  # noqa: BLE001 - never persist a partial pair
                self._fail("校准系数计算或应用失败", error)
                return
            self.pending_coefficients = (left, right)
            self.verification_samples = []
            (
                _verification_cutoff_ns,
                self.verification_sequence_start,
                self.dropped_at_verification_start,
                self.discarded_at_verification_start,
            ) = self.capture.begin_fresh_sample_window()
            self.received_at_verification_start = self.verification_sequence_start
            self.verification_deadline = time.monotonic() + arguments.verification_seconds
            self.state = "verifying"
            self.status.setText(
                f"系数已临时应用；请正视前方 {arguments.verification_seconds:g} 秒。"
            )

        def _drain_verification_samples(self) -> None:
            for _ in range(8192):
                sample = self.capture.read_sample(timeout_seconds=0.0)
                if sample is None:
                    return
                if int(sample["sequence"]) >= self.verification_sequence_start:
                    self.verification_samples.append(sample)

        def _finish_verification(self) -> None:
            try:
                ending_device_hash = self.capture.get_mpu_id_hash()
                device_binding_stable = hmac.compare_digest(
                    ending_device_hash,
                    self.device_hash,
                )
                self.capture.stop()
                self._drain_verification_samples()
                authorization_established = self.capture.authorization_was_established
                disconnect_terminal_state = classify_device_disconnect_terminal_state(
                    authorization_established=authorization_established,
                    sdk_stop_result=self.capture.stop_result,
                    device_disconnect_result=self.capture.disconnect_result,
                )
                scene_width, scene_height = RESOLUTION_DIMENSIONS[arguments.resolution]
                verification = evaluate_post_calibration(
                    self.verification_samples,
                    scene_width=scene_width,
                    scene_height=scene_height,
                    received_callback_samples=(
                        self.capture.received_callback_samples - self.received_at_verification_start
                    ),
                    dropped_callback_samples=(
                        self.capture.dropped_callback_samples - self.dropped_at_verification_start
                    ),
                    discarded_callback_samples=(
                        self.capture.discarded_callback_samples
                        - self.discarded_at_verification_start
                    ),
                    pending_callback_samples=self.capture.pending_callback_samples,
                    post_stop_callback_samples=self.capture.post_stop_callback_samples,
                )
                cleanup_issues: list[str] = []
                if not device_binding_stable:
                    cleanup_issues.append("device_binding_changed_during_calibration")
                if self.capture.calibration_complete_result != 0:
                    cleanup_issues.append("calibration_complete_not_confirmed_successful")
                if self.capture.stop_result != 0:
                    cleanup_issues.append("sdk_stop_not_confirmed_successful")
                if not authorization_established:
                    cleanup_issues.append("authorization_not_confirmed_successful")
                if disconnect_terminal_state is None:
                    cleanup_issues.append("device_disconnect_not_confirmed_successful")
                if self.capture.scene_decoder_release_result != 0:
                    cleanup_issues.append("scene_decoder_release_not_confirmed_successful")
                if self.capture.scene_decoder_error is not None:
                    cleanup_issues.append("scene_decoder_thread_error")
                if cleanup_issues:
                    verification["issues"].extend(cleanup_issues)
                    verification["result"] = "REVIEW_REQUIRED"
                metadata = {
                    "sdk_version": self.sdk_version,
                    "runtime_integrity": {
                        "aSeeX_sha256": self.capture.manifest.aseex_sha256,
                        "USERSDK_sha256": self.capture.manifest.usersdk_sha256,
                        "validated_file_count": self.capture.manifest.validated_file_count,
                        "runtime_bundle_sha256": (self.capture.manifest.runtime_bundle_sha256),
                    },
                    "environment": arguments.environment,
                    "resolution": arguments.resolution,
                    "clicked_points": self.clicked_points,
                    "vendor_score_raw": self.vendor_score,
                    "verification": verification,
                    "scene_decoder_return_codes_raw": {
                        str(code): count
                        for code, count in self.capture.scene_decode_return_codes.items()
                    },
                    "dropped_scene_images": self.capture.dropped_scene_images,
                    "rejected_scene_images": self.capture.rejected_scene_images,
                    "native_cleanup_results": {
                        "calibration_complete": self.capture.calibration_complete_result,
                        "sdk_stop": self.capture.stop_result,
                        "device_disconnect": self.capture.disconnect_result,
                        "scene_decoder_release": self.capture.scene_decoder_release_result,
                        "scene_decoder_error": self.capture.scene_decoder_error,
                    },
                    "authorization_established": authorization_established,
                    "device_disconnect_terminal_state": disconnect_terminal_state,
                    "device_binding_rechecked_before_stop": True,
                    "scene_to_screen_mapping": "NOT_IMPLEMENTED",
                }
                if verification["result"] != "PASS":
                    self.pending_coefficients = None
                    report = write_attempt_report(
                        arguments.output_dir,
                        {
                            "protocol": CALIBRATION_PROTOCOL,
                            "result": "REVIEW_REQUIRED",
                            "profile_id_sha256": profile_hash,
                            **metadata,
                        },
                    )
                    self.state = "review_required"
                    self.exit_code = 2
                    self.status.setText("技术输出复验未通过，未保存系数。请关闭后重新运行。")
                    print(f"OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION_REPORT={report}")
                    print("OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION=REVIEW_REQUIRED")
                    QtWidgets.QMessageBox.warning(
                        self,
                        "需要复核",
                        "短时技术复验未满足输出有效性与清理条件；系数没有保存。",
                    )
                    return
                if self.pending_coefficients is None:
                    raise SeveninvensunSdkError("Verified calibration pair is unavailable.")
                bundle = save_verified_calibration_bundle(
                    arguments.output_dir,
                    profile_hash=profile_hash,
                    device_hash=self.device_hash,
                    left=self.pending_coefficients[0],
                    right=self.pending_coefficients[1],
                    metadata=metadata,
                )
                self.state = "passed"
                self.exit_code = 0
                self.pending_coefficients = None
                self.status.setText("技术输出复验通过。注意：这不是精度结论，且未完成屏幕映射。")
                print(f"OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION_BUNDLE={bundle}")
                print("OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION=PASS")
                QtWidgets.QMessageBox.information(
                    self,
                    "校准通过",
                    "已原子保存患者与设备绑定的系数。该结果不是精度或临床结论，"
                    "场景注视仍不能驱动患者屏幕任务。",
                )
            except Exception as error:  # noqa: BLE001 - emit a non-biometric report
                self._fail("短时复验或成对保存失败", error)

        def _fail(self, title: str, error: Exception) -> None:
            if self.state == "failed":
                return
            self.state = "failed"
            self.exit_code = 1
            self.pending_coefficients = None
            self.timer.stop()
            cleanup_error: Exception | None = None
            try:
                self.capture.close()
            except Exception as close_error:  # noqa: BLE001 - retain the primary failure
                cleanup_error = close_error
            self.status.setText(f"{title}：{error}")
            report = write_attempt_report(
                arguments.output_dir,
                {
                    "protocol": CALIBRATION_PROTOCOL,
                    "result": "FAIL",
                    "profile_id_sha256": profile_hash,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "cleanup_error_type": (
                        type(cleanup_error).__name__ if cleanup_error is not None else None
                    ),
                    "cleanup_error": str(cleanup_error) if cleanup_error is not None else None,
                    "native_cleanup_results": {
                        "calibration_complete": self.capture.calibration_complete_result,
                        "sdk_stop": self.capture.stop_result,
                        "device_disconnect": self.capture.disconnect_result,
                        "scene_decoder_release": self.capture.scene_decoder_release_result,
                        "scene_decoder_error": self.capture.scene_decoder_error,
                    },
                    "authorization_established": self.capture.authorization_was_established,
                    "device_disconnect_terminal_state": (
                        classify_device_disconnect_terminal_state(
                            authorization_established=(self.capture.authorization_was_established),
                            sdk_stop_result=self.capture.stop_result,
                            device_disconnect_result=self.capture.disconnect_result,
                        )
                    ),
                    "post_stop_callback_samples": self.capture.post_stop_callback_samples,
                    "screen_mapping_status": "not_mapped",
                },
            )
            print(f"OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION_REPORT={report}")
            print("OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION=FAIL")
            QtWidgets.QMessageBox.critical(self, title, str(error))

        def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            self.timer.stop()
            try:
                self.capture.close()
            except Exception as error:  # noqa: BLE001 - process exits after displaying status
                self.status.setText(f"设备清理需要复核：{error}")
            event.accept()

    app = QtWidgets.QApplication(sys.argv[:1])
    try:
        cleanup_stale_bundle_temps(arguments.output_dir)
    except OSError as error:
        QtWidgets.QMessageBox.critical(None, "校准输出目录不可用", str(error))
        return 1
    password, accepted = QtWidgets.QInputDialog.getText(
        None,
        "aSee SDK 授权",
        "请输入厂商明确交付的 UKey login password：",
        QtWidgets.QLineEdit.EchoMode.Password,
    )
    if not accepted or not password:
        print("OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION=CANCELLED")
        return 2

    capture = SeveninvensunGlassesSdkCapture(arguments.sdk_root)
    try:
        sdk_version = capture.load()
        try:
            capture.authorize(password)
        finally:
            password = ""
        capture.start_scene_decoder(arguments.resolution)
        capture.start(
            environment=arguments.environment,
            resolution=arguments.resolution,
            gyroscope=False,
        )
        device_hash = capture.get_mpu_id_hash()
    except Exception as error:  # noqa: BLE001 - show authorization/runtime errors without secrets
        password = ""
        try:
            capture.close()
        except Exception:
            pass
        report = write_attempt_report(
            arguments.output_dir,
            {
                "protocol": CALIBRATION_PROTOCOL,
                "result": "FAIL",
                "profile_id_sha256": profile_hash,
                "error_type": type(error).__name__,
                "error": str(error),
                "screen_mapping_status": "not_mapped",
            },
        )
        print(f"OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION_REPORT={report}")
        print("OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION=FAIL")
        QtWidgets.QMessageBox.critical(None, "aSeeGlasses 启动失败", str(error))
        return 1

    try:
        window = CalibrationWindow(capture, sdk_version, device_hash)
        window.show()
        app.exec()
        return int(window.exit_code)
    finally:
        try:
            capture.close()
        except Exception:
            pass


def main() -> int:
    parser = _build_parser()
    arguments = parser.parse_args()
    if not 3.0 <= arguments.verification_seconds <= 30.0:
        parser.error("--verification-seconds must be between 3 and 30.")
    if not 5.0 <= arguments.point_timeout_seconds <= 60.0:
        parser.error("--point-timeout-seconds must be between 5 and 60.")
    try:
        profile_hash = subject_hash(arguments.profile_id)
    except ValueError as error:
        parser.error(str(error))
    arguments.profile_id = ""
    return _run_qt(arguments, profile_hash=profile_hash)


if __name__ == "__main__":
    raise SystemExit(main())
