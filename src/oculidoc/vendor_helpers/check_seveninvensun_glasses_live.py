"""Capture aSeeGlasses Plus SDK samples without starting aSeeStudio."""

from __future__ import annotations

import argparse
import getpass
import json
import math
import statistics
import tempfile
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .calibrate_seveninvensun_glasses import load_calibration_bundle, subject_hash
from .seveninvensun_glasses_sdk import (
    ENVIRONMENTS,
    RESOLUTIONS,
    SEVENINVENSUN_GLASSES_RAW_PROTOCOL,
    SeveninvensunGlassesSdkCapture,
    SeveninvensunRuntimeManifest,
    SeveninvensunSdkCallError,
    classify_device_disconnect_terminal_state,
    validate_sdk_runtime,
)


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _has_usable_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return any(_has_usable_value(item) for item in value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value))
    return True


class _CaptureSummary:
    COVERAGE_FIELDS = (
        "recommended_gaze",
        "left_gaze",
        "right_gaze",
        "left_gaze_origin",
        "right_gaze_origin",
        "left_gaze_direction",
        "right_gaze_direction",
        "left_pupil_center",
        "right_pupil_center",
        "left_pupil_diameter_mm",
        "right_pupil_diameter_mm",
        "left_openness",
        "right_openness",
        "left_eyelid_distance",
        "right_eyelid_distance",
        "left_blink_state",
        "right_blink_state",
        "fixation_saccade_state",
        "imu",
    )

    def __init__(self) -> None:
        self.count = 0
        self.first_host_ns: int | None = None
        self.last_host_ns: int | None = None
        self.previous_device_timestamp: int | None = None
        self.device_timestamp_deltas: list[int] = []
        self.nonpositive_device_timestamp_deltas = 0
        self.imu_timestamps: set[int] = set()
        self.coverage: Counter[str] = Counter()

    def add(self, sample: dict[str, Any]) -> None:
        self.count += 1
        host_ns = int(sample["host_monotonic_ns"])
        self.first_host_ns = host_ns if self.first_host_ns is None else self.first_host_ns
        self.last_host_ns = host_ns

        device_timestamp = int(sample["device_timestamp_raw"])
        if self.previous_device_timestamp is not None:
            delta = device_timestamp - self.previous_device_timestamp
            self.device_timestamp_deltas.append(delta)
            if delta <= 0:
                self.nonpositive_device_timestamp_deltas += 1
        self.previous_device_timestamp = device_timestamp

        imu_timestamp = int(_nested(sample, "imu", "timestamp_raw") or 0)
        if imu_timestamp:
            self.imu_timestamps.add(imu_timestamp)

        fields = {
            "recommended_gaze": _nested(
                sample, "gaze", "recommended", "gaze_point_scene_centered_px"
            ),
            "left_gaze": _nested(sample, "gaze", "left", "gaze_point_scene_centered_px"),
            "right_gaze": _nested(sample, "gaze", "right", "gaze_point_scene_centered_px"),
            "left_gaze_origin": _nested(sample, "gaze", "left", "origin_vendor_units"),
            "right_gaze_origin": _nested(sample, "gaze", "right", "origin_vendor_units"),
            "left_gaze_direction": _nested(sample, "gaze", "left", "direction"),
            "right_gaze_direction": _nested(sample, "gaze", "right", "direction"),
            "left_pupil_center": _nested(sample, "pupil", "left", "center_normalized"),
            "right_pupil_center": _nested(sample, "pupil", "right", "center_normalized"),
            "left_pupil_diameter_mm": _nested(sample, "pupil", "left", "diameter_mm"),
            "right_pupil_diameter_mm": _nested(sample, "pupil", "right", "diameter_mm"),
            "left_openness": _nested(sample, "eye_extra", "left", "openness_percent"),
            "right_openness": _nested(sample, "eye_extra", "right", "openness_percent"),
            "left_eyelid_distance": _nested(
                sample, "eye_extra", "left", "eyelid_distance_normalized"
            ),
            "right_eyelid_distance": _nested(
                sample, "eye_extra", "right", "eyelid_distance_normalized"
            ),
            "left_blink_state": _nested(sample, "eye_extra", "left", "blink_state"),
            "right_blink_state": _nested(sample, "eye_extra", "right", "blink_state"),
            "fixation_saccade_state": (
                True if _nested(sample, "fixation_saccade", "valid") is True else None
            ),
            "imu": True if imu_timestamp else None,
        }
        for name, value in fields.items():
            if _has_usable_value(value):
                self.coverage[name] += 1

    def as_dict(self) -> dict[str, Any]:
        elapsed_seconds = None
        sample_rate_hz = None
        if (
            self.count > 1
            and self.first_host_ns is not None
            and self.last_host_ns is not None
            and self.last_host_ns > self.first_host_ns
        ):
            elapsed_seconds = (self.last_host_ns - self.first_host_ns) / 1_000_000_000
            sample_rate_hz = (self.count - 1) / elapsed_seconds
        median_delta = (
            statistics.median(self.device_timestamp_deltas)
            if self.device_timestamp_deltas
            else None
        )
        return {
            "sample_count": self.count,
            "host_elapsed_seconds": elapsed_seconds,
            "host_sample_rate_hz": sample_rate_hz,
            "device_timestamp_unit": "vendor_unspecified",
            "median_device_timestamp_delta_raw": median_delta,
            "nonpositive_device_timestamp_deltas": self.nonpositive_device_timestamp_deltas,
            "distinct_nonzero_imu_timestamps": len(self.imu_timestamps),
            "coverage": {
                name: {
                    "valid_samples": self.coverage[name],
                    "valid_ratio_percent": (
                        self.coverage[name] / self.count * 100.0 if self.count else 0.0
                    ),
                }
                for name in self.COVERAGE_FIELDS
            },
        }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing report: {path.name}")
    temporary_path = path.with_name(path.name + ".tmp")
    with temporary_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        stream.flush()
    temporary_path.replace(path)


def _runtime_integrity(manifest: SeveninvensunRuntimeManifest) -> dict[str, Any]:
    return {
        "aSeeX_sha256": manifest.aseex_sha256,
        "USERSDK_sha256": manifest.usersdk_sha256,
        "validated_file_count": manifest.validated_file_count,
        "runtime_bundle_sha256": manifest.runtime_bundle_sha256,
    }


def _live_acceptance_issues(
    details: dict[str, Any],
    *,
    expected_resolution: str,
) -> list[str]:
    issues: list[str] = []
    sample_count = int(details.get("sample_count", 0))
    sample_rate = details.get("host_sample_rate_hz")
    if sample_count < 10:
        issues.append("fewer_than_10_eye_callbacks")
    if not isinstance(sample_rate, (float, int)) or not 50.0 <= sample_rate <= 500.0:
        issues.append("host_sample_rate_outside_50_to_500_hz")
    if int(details.get("nonpositive_device_timestamp_deltas", 0)) > 0:
        issues.append("device_timestamp_not_strictly_increasing")
    if int(details.get("dropped_callback_samples", 0)) > 0:
        issues.append("callback_queue_dropped_samples")
    if int(details.get("pending_callback_samples", 0)) > 0:
        issues.append("callback_queue_not_fully_consumed")
    if int(details.get("post_stop_callback_samples", 0)) != 0:
        issues.append("callback_observed_after_stop")
    if int(details.get("received_callback_samples", 0)) != (
        sample_count
        + int(details.get("dropped_callback_samples", 0))
        + int(details.get("discarded_callback_samples", 0))
        + int(details.get("pending_callback_samples", 0))
    ):
        issues.append("callback_accounting_mismatch")
    if details.get("sdk_stop_result") != 0:
        issues.append("sdk_stop_not_confirmed_successful")
    if details.get("authorization_prompt_used") is True:
        disconnect_terminal_state = classify_device_disconnect_terminal_state(
            authorization_established=details.get("authorization_established") is True,
            sdk_stop_result=details.get("sdk_stop_result"),
            device_disconnect_result=details.get("sdk_disconnect_result"),
        )
        if (
            disconnect_terminal_state is None
            or details.get("sdk_disconnect_terminal_state") != disconnect_terminal_state
        ):
            issues.append("sdk_disconnect_not_confirmed_successful")

    coverage = details.get("coverage")
    if not isinstance(coverage, dict):
        coverage = {}
    required_fields = (
        "recommended_gaze",
        "left_gaze",
        "right_gaze",
        "left_gaze_origin",
        "right_gaze_origin",
        "left_pupil_center",
        "right_pupil_center",
        "left_pupil_diameter_mm",
        "right_pupil_diameter_mm",
        "left_openness",
        "right_openness",
        "left_eyelid_distance",
        "right_eyelid_distance",
        "left_blink_state",
        "right_blink_state",
        "left_gaze_direction",
        "right_gaze_direction",
    )
    missing_fields = [
        name
        for name in required_fields
        if not isinstance(coverage.get(name), dict)
        or int(coverage[name].get("valid_samples", 0)) <= 0
    ]
    if missing_fields:
        issues.append("missing_core_fields:" + ",".join(missing_fields))

    image_metadata = details.get("last_image_metadata")
    if not isinstance(image_metadata, dict):
        image_metadata = {}
    missing_images = [
        name for name in ("scene", "left_eye", "right_eye") if name not in image_metadata
    ]
    if missing_images:
        issues.append("missing_image_metadata:" + ",".join(missing_images))
    for name in ("scene", "left_eye", "right_eye"):
        metadata = image_metadata.get(name)
        if isinstance(metadata, dict):
            width = metadata.get("width")
            height = metadata.get("height")
            if (
                not isinstance(width, int)
                or isinstance(width, bool)
                or width <= 0
                or not isinstance(height, int)
                or isinstance(height, bool)
                or height <= 0
            ):
                issues.append(f"invalid_image_dimensions:{name}")
    expected_width, expected_height = (int(part) for part in expected_resolution.split("x", 1))
    scene_metadata = image_metadata.get("scene")
    if isinstance(scene_metadata, dict) and (
        scene_metadata.get("width") != expected_width
        or scene_metadata.get("height") != expected_height
    ):
        issues.append("scene_dimensions_do_not_match_requested_resolution")

    if (
        details.get("gyroscope_requested") is True
        and int(details.get("distinct_nonzero_imu_timestamps", 0)) < 2
    ):
        issues.append("imu_stream_not_observed")
    return issues


def _capture_live(
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], Path, SeveninvensunRuntimeManifest]:
    bundle_requested = arguments.calibration_bundle is not None

    summary = _CaptureSummary()
    raw_path = arguments.output_dir / f"asee_glasses_raw_{arguments.run_id}.ndjson"
    capture = SeveninvensunGlassesSdkCapture(arguments.sdk_root)
    version = ""
    authorization_used = False
    started = False
    received_at_capture_start = 0
    dropped_at_capture_start = 0
    discarded_at_capture_start = 0
    try:
        version = capture.load()
        if arguments.prompt_authorization:
            password = getpass.getpass("aSee SDK authorization password: ")
            try:
                capture.authorize(password)
            finally:
                password = ""
            authorization_used = True

        try:
            capture.start(
                environment=arguments.environment,
                resolution=arguments.resolution,
                gyroscope=not arguments.no_gyroscope,
            )
        except SeveninvensunSdkCallError as error:
            if error.function_name == "_7i_start" and error.result_code == -1:
                raise RuntimeError(
                    "SDK authorization is required; rerun with --prompt-authorization."
                ) from error
            raise
        started = True

        if bundle_requested:
            device_hash = capture.get_mpu_id_hash()
            left, right = load_calibration_bundle(
                arguments.calibration_bundle,
                expected_profile_hash=arguments.profile_hash,
                expected_device_hash=device_hash,
                expected_sdk_version=version,
                expected_runtime_bundle_hash=capture.manifest.runtime_bundle_sha256,
                expected_environment=arguments.environment,
                expected_resolution=arguments.resolution,
            )
            capture.apply_calibration_coefficients(
                left,
                right,
                expected_device_hash=device_hash,
            )
            left = right = b""
            (
                _capture_cutoff_ns,
                received_at_capture_start,
                dropped_at_capture_start,
                discarded_at_capture_start,
            ) = capture.begin_fresh_sample_window()

        deadline = time.monotonic() + arguments.duration
        with raw_path.open("x", encoding="utf-8", newline="\n") as stream:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                sample = capture.read_sample(timeout_seconds=max(0.001, min(0.25, remaining)))
                if sample is None:
                    continue
                stream.write(
                    json.dumps(
                        sample,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                )
                summary.add(sample)
    finally:
        capture.close()

    details = summary.as_dict()
    details.update(
        {
            "sdk_version": version,
            "sdk_started": started,
            "authorization_prompt_used": authorization_used,
            "calibration_bundle_validated_and_loaded": bundle_requested,
            "gyroscope_requested": not arguments.no_gyroscope,
            "requested_resolution": arguments.resolution,
            "dropped_callback_samples": (
                capture.dropped_callback_samples - dropped_at_capture_start
            ),
            "discarded_callback_samples": (
                capture.discarded_callback_samples - discarded_at_capture_start
            ),
            "received_callback_samples": (
                capture.received_callback_samples - received_at_capture_start
            ),
            "pre_capture_callbacks_discarded_or_dropped": received_at_capture_start,
            "pending_callback_samples": capture.pending_callback_samples,
            "post_stop_callback_samples": capture.post_stop_callback_samples,
            "last_camera_state_code": capture.camera_state,
            "last_image_metadata": capture.image_metadata,
            "sdk_stop_result": capture.stop_result,
            "sdk_disconnect_result": capture.disconnect_result,
            "authorization_established": capture.authorization_was_established,
            "sdk_disconnect_terminal_state": classify_device_disconnect_terminal_state(
                authorization_established=capture.authorization_was_established,
                sdk_stop_result=capture.stop_result,
                device_disconnect_result=capture.disconnect_result,
            ),
        }
    )
    return details, raw_path, capture.manifest


def _abi_only(
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], SeveninvensunRuntimeManifest]:
    capture = SeveninvensunGlassesSdkCapture(arguments.sdk_root)
    try:
        version = capture.load()
    finally:
        capture.close()
    return (
        {
            "sdk_version": version,
            "sdk_started": False,
            "sample_count": 0,
            "abi_only": True,
        },
        capture.manifest,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Directly capture aSeeGlasses Plus data without aSeeStudio.",
    )
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Required for live biometric capture; ABI-only defaults to a temporary directory.",
    )
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--environment", choices=tuple(ENVIRONMENTS), default="indoor")
    parser.add_argument("--resolution", choices=tuple(RESOLUTIONS), default="1280x960")
    parser.add_argument("--prompt-authorization", action="store_true")
    parser.add_argument("--calibration-bundle", type=Path)
    parser.add_argument(
        "--profile-id",
        help="Canonical opaque OculiDoC patient UUID required with --calibration-bundle.",
    )
    parser.add_argument("--no-gyroscope", action="store_true")
    parser.add_argument("--abi-only", action="store_true")
    return parser


def main() -> int:
    parser = _build_parser()
    arguments = parser.parse_args()
    if not 1.0 <= arguments.duration <= 300.0:
        parser.error("--duration must be between 1 and 300 seconds.")
    bundle_requested = arguments.calibration_bundle is not None
    if bundle_requested != (arguments.profile_id is not None):
        parser.error("--calibration-bundle and --profile-id must be supplied together.")
    if arguments.abi_only and bundle_requested:
        parser.error("--calibration-bundle is not valid with --abi-only.")
    if arguments.profile_id is not None:
        try:
            arguments.profile_hash = subject_hash(arguments.profile_id)
        except ValueError as error:
            parser.error(str(error))
        arguments.profile_id = ""
    else:
        arguments.profile_hash = ""
    if arguments.output_dir is None:
        if not arguments.abi_only:
            parser.error("--output-dir is required for live biometric capture.")
        arguments.output_dir = Path(tempfile.gettempdir()) / "OculiDoC_aSeeGlasses"
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    arguments.run_id = f"{timestamp}_{uuid.uuid4().hex[:8]}"
    report_path = arguments.output_dir / f"asee_glasses_report_{arguments.run_id}.json"

    report: dict[str, Any] = {
        "protocol": SEVENINVENSUN_GLASSES_RAW_PROTOCOL,
        "run_time_utc": datetime.now(UTC).isoformat(),
        "mode": "abi_only" if arguments.abi_only else "live_capture",
        "device_family": "aSeeGlasses Plus",
        "a8_supported": False,
        "studio_required": False,
        "helper_process_required": True,
        "coordinate_space": "scene_camera_centered_px",
        "screen_mapping_status": "not_mapped",
        "runtime_integrity": {},
        "caveats": [
            "Device and IMU timestamp units are not stated by this SDK release.",
            "Quaternion component order and IMU units remain vendor-unspecified.",
            "Gaze-origin units/axes and the scene Y-axis direction require a live "
            "reference-point test.",
            "Scene gaze is not patient-screen gaze and cannot drive an OculiDoC task yet.",
            "Studio export velocities and event indices are derived fields, not raw SDK fields.",
        ],
    }
    raw_path = (
        None
        if arguments.abi_only
        else arguments.output_dir / f"asee_glasses_raw_{arguments.run_id}.ndjson"
    )
    try:
        if arguments.abi_only:
            details, manifest = _abi_only(arguments)
        else:
            details, captured_path, manifest = _capture_live(arguments)
            raw_path = captured_path
        report["runtime_integrity"] = _runtime_integrity(manifest)
        report["capture"] = details
        if arguments.abi_only:
            report["statuses"] = {
                "abi": "PASS",
                "hardware_transport": "NOT_RUN",
                "field_coverage": "NOT_RUN",
                "scene_to_screen_mapping": "NOT_IMPLEMENTED",
            }
            result = "PASS"
        else:
            issues = _live_acceptance_issues(
                details,
                expected_resolution=arguments.resolution,
            )
            report["acceptance_issues"] = issues
            report["statuses"] = {
                "abi": "PASS",
                "hardware_transport": (
                    "PASS" if int(details.get("sample_count", 0)) > 0 else "FAIL"
                ),
                "field_coverage": (
                    "PASS"
                    if not any(issue.startswith("missing_core_fields:") for issue in issues)
                    else "PARTIAL"
                ),
                "scene_to_screen_mapping": "NOT_IMPLEMENTED",
            }
            result = "PASS" if not issues else "REVIEW_REQUIRED"
        report["result"] = result
        exit_code = 0 if result == "PASS" else 2
    except Exception as error:  # noqa: BLE001 - hardware acceptance must always emit a report
        report["result"] = "FAIL"
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        try:
            report["runtime_integrity"] = _runtime_integrity(
                validate_sdk_runtime(arguments.sdk_root)
            )
        except Exception:  # noqa: BLE001 - preserve the original hardware error
            pass
        exit_code = 1

    if raw_path is not None and raw_path.is_file():
        report["raw_output"] = raw_path.name
    _write_json(report_path, report)
    print(f"OCULIDOC_SEVENINVENSUN_GLASSES_REPORT={report_path}")
    if raw_path is not None and raw_path.is_file():
        print(f"OCULIDOC_SEVENINVENSUN_GLASSES_RAW={raw_path}")
    if arguments.abi_only:
        print(f"OCULIDOC_SEVENINVENSUN_GLASSES_ABI={report['result']}")
        print("OCULIDOC_SEVENINVENSUN_GLASSES_DIRECT=NOT_RUN")
    else:
        print(f"OCULIDOC_SEVENINVENSUN_GLASSES_DIRECT={report['result']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
