"""Tests for the isolated aSeeGlasses Plus SDK data contract."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from oculidoc.vendor_helpers import seveninvensun_glasses_sdk as sdk
from oculidoc.vendor_helpers.check_seveninvensun_glasses_live import (
    _CaptureSummary,
    _live_acceptance_issues,
    _write_json,
)


def _raw_sample() -> bytes:
    sample = sdk._EyeDataEx()
    sample.timestamp = 123_456
    sample.recommend = 1

    sample.recom_gaze.gaze_bit_mask = (1 << 0) | (1 << 3) | (1 << 4)
    sample.recom_gaze.gaze_point.x = -10.0
    sample.recom_gaze.gaze_point.y = 20.0
    sample.recom_gaze.gaze_origin.x = 1.0
    sample.recom_gaze.gaze_origin.y = 2.0
    sample.recom_gaze.gaze_origin.z = 3.0
    sample.recom_gaze.gaze_direction.z = 1.0

    sample.left_gaze.gaze_bit_mask = 1 << 0
    sample.left_gaze.gaze_point.x = -12.0
    sample.left_gaze.gaze_point.y = 18.0
    sample.right_gaze.gaze_point.x = 999.0
    sample.right_gaze.gaze_point.y = 999.0

    sample.left_pupil.pupil_bit_mask = (1 << 0) | (1 << 2) | (1 << 3)
    sample.left_pupil.pupil_center.x = 0.5
    sample.left_pupil.pupil_center.y = 0.4
    sample.left_pupil.pupil_diameter = 0.1
    sample.left_pupil.pupil_diameter_mm = 3.7
    sample.right_pupil.pupil_diameter_mm = 99.0

    sample.left_ex_data.eye_data_ex_bit_mask = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)
    sample.left_ex_data.blink = 0
    sample.left_ex_data.openness = 88.0
    sample.left_ex_data.eyelid_up = 0.2
    sample.left_ex_data.eyelid_down = 0.7

    sample.stats.timestamp = 1_000
    sample.stats.duration = 2_000
    sample.stats.count = 7
    sample.stats.state = 3
    sample.stats.center.x = -5.0
    sample.stats.center.y = 6.0

    sample.gyro.timestamp = 77
    sample.gyro.quat_data[:] = (0.1, 0.2, 0.3, 0.4)
    sample.gyro.gyro[:] = (1.0, 2.0, 3.0)
    sample.gyro.accel[:] = (0.0, 0.0, 1.0)
    sample.gyro.mag[:] = (4.0, 5.0, 6.0)
    return ctypes.string_at(ctypes.byref(sample), ctypes.sizeof(sample))


def test_official_header_structure_sizes_match_ctypes_layout() -> None:
    assert sdk.sdk_structure_sizes() == sdk.EXPECTED_STRUCTURE_SIZES
    assert sdk.sdk_eye_data_ex_offsets() == sdk.EXPECTED_EYE_DATA_EX_OFFSETS
    sdk.assert_sdk_abi_layout()


def test_runtime_allowlist_covers_complete_public_sdk_bin() -> None:
    assert len(sdk.RUNTIME_FILE_SHA256) == 24
    assert sdk.RUNTIME_FILE_SHA256["bin/aSeeX.dll"] == sdk.ASEEX_SHA256
    assert sdk.RUNTIME_FILE_SHA256["bin/USERSDK.dll"] == sdk.USERSDK_SHA256
    assert "bin/AlgInterface.dll" in sdk.RUNTIME_FILE_SHA256
    assert "bin/config/base_coe.dat" in sdk.RUNTIME_FILE_SHA256
    assert "bin/config/config.ini" in sdk.RUNTIME_FILE_SHA256


def test_decode_preserves_raw_fields_and_never_claims_screen_mapping() -> None:
    payload = sdk.decode_sdk_sample(
        _raw_sample(),
        sequence=4,
        host_monotonic_ns=10,
        host_wall_time_ns=20,
        image_metadata={
            "scene": {"width": 1280, "height": 960},
            "left_eye": {"width": 320, "height": 240},
        },
    )

    assert payload["protocol"] == sdk.SEVENINVENSUN_GLASSES_RAW_PROTOCOL
    assert payload["coordinate_space"] == "scene_camera_centered_px"
    assert payload["screen_mapping_status"] == "not_mapped"
    assert payload["recommended_eye_code_vendor"] == 1
    assert "header_defines" in payload["recommended_eye_code_status"]
    assert "manual_marks_deprecated" in payload["recommended_eye_code_status"]
    assert payload["validity"] == {"eye": True, "left": True, "right": False}
    assert payload["gaze"]["recommended"]["gaze_point_scene_centered_px"] == [
        -10.0,
        20.0,
        0.0,
    ]
    assert payload["gaze"]["recommended"]["gaze_point_scene_top_left_px"] == [
        630.0,
        500.0,
        0.0,
    ]
    assert payload["gaze"]["right"]["gaze_point_scene_centered_px"] is None
    assert payload["pupil"]["left"]["center_px"] == pytest.approx([160.0, 96.0])
    assert payload["pupil"]["left"]["diameter_px"] == pytest.approx(32.0)
    assert payload["pupil"]["left"]["diameter_mm"] == pytest.approx(3.7)
    assert payload["pupil"]["right"]["diameter_mm"] is None
    assert payload["eye_extra"]["left"]["eyelid_distance_px"] == pytest.approx(120.0)
    assert payload["fixation_saccade"]["state"] == "fixation_staying"
    assert payload["fixation_saccade"]["duration_us"] == 2_000
    assert payload["imu"]["timestamp_raw"] == 77
    assert payload["imu"]["quaternion_vendor_order"] == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert "gaze_x_normalized" not in payload
    json.dumps(payload, allow_nan=False)


def test_vendor_recommended_eye_code_never_controls_gaze_validity() -> None:
    sample = sdk._EyeDataEx.from_buffer_copy(_raw_sample())
    sample.recommend = 2
    sample.recom_gaze.gaze_bit_mask = 0
    payload = sdk.decode_sdk_sample(
        ctypes.string_at(ctypes.byref(sample), ctypes.sizeof(sample)),
        sequence=0,
        host_monotonic_ns=1,
        host_wall_time_ns=2,
    )

    assert payload["recommended_eye_code_vendor"] == 2
    assert payload["gaze"]["recommended"]["gaze_point_scene_centered_px"] is None


@pytest.mark.parametrize(
    ("bit", "field"),
    [
        (0, "gaze_point_scene_centered_px"),
        (1, "raw_point_scene_centered_px"),
        (2, "smooth_point_scene_centered_px"),
        (3, "origin_vendor_units"),
        (4, "direction"),
        (5, "confidence_re"),
    ],
)
def test_each_gaze_validity_bit_controls_only_its_documented_field(
    bit: int,
    field: str,
) -> None:
    sample = sdk._EyeDataEx()
    gaze = sample.recom_gaze
    gaze.gaze_bit_mask = 1 << bit
    gaze.gaze_point.x = 1.0
    gaze.raw_point.x = 2.0
    gaze.smooth_point.x = 3.0
    gaze.gaze_origin.x = 4.0
    gaze.gaze_direction.x = 5.0
    gaze.re = 6.0
    payload = sdk.decode_sdk_sample(
        ctypes.string_at(ctypes.byref(sample), ctypes.sizeof(sample)),
        sequence=0,
        host_monotonic_ns=1,
        host_wall_time_ns=2,
    )
    decoded = payload["gaze"]["recommended"]
    controlled_fields = {
        "gaze_point_scene_centered_px",
        "raw_point_scene_centered_px",
        "smooth_point_scene_centered_px",
        "origin_vendor_units",
        "direction",
        "confidence_re",
    }

    assert decoded[field] is not None
    assert all(decoded[name] is None for name in controlled_fields - {field})


@pytest.mark.parametrize(
    ("bit", "field"),
    [
        (0, "center_normalized"),
        (1, "distance_mm"),
        (2, "diameter_normalized_by_image_width"),
        (3, "diameter_mm"),
        (4, "minor_axis_normalized_by_image_width"),
        (5, "minor_axis_mm"),
    ],
)
def test_each_pupil_validity_bit_controls_only_its_documented_field(
    bit: int,
    field: str,
) -> None:
    sample = sdk._EyeDataEx()
    pupil = sample.left_pupil
    pupil.pupil_bit_mask = 1 << bit
    pupil.pupil_center.x = 0.1
    pupil.pupil_distance = 2.0
    pupil.pupil_diameter = 0.3
    pupil.pupil_diameter_mm = 4.0
    pupil.pupil_minor_axis = 0.5
    pupil.pupil_minor_axis_mm = 6.0
    payload = sdk.decode_sdk_sample(
        ctypes.string_at(ctypes.byref(sample), ctypes.sizeof(sample)),
        sequence=0,
        host_monotonic_ns=1,
        host_wall_time_ns=2,
    )
    decoded = payload["pupil"]["left"]
    controlled_fields = {
        "center_normalized",
        "distance_mm",
        "diameter_normalized_by_image_width",
        "diameter_mm",
        "minor_axis_normalized_by_image_width",
        "minor_axis_mm",
    }

    assert decoded[field] is not None
    assert all(decoded[name] is None for name in controlled_fields - {field})


@pytest.mark.parametrize(
    ("bit", "field"),
    [
        (0, "blink_state"),
        (1, "openness_percent"),
        (2, "eyelid_up_normalized"),
        (3, "eyelid_down_normalized"),
    ],
)
def test_each_eye_extra_validity_bit_controls_only_its_documented_field(
    bit: int,
    field: str,
) -> None:
    sample = sdk._EyeDataEx()
    extra = sample.left_ex_data
    extra.eye_data_ex_bit_mask = 1 << bit
    extra.blink = 3
    extra.openness = 88.0
    extra.eyelid_up = 0.2
    extra.eyelid_down = 0.7
    payload = sdk.decode_sdk_sample(
        ctypes.string_at(ctypes.byref(sample), ctypes.sizeof(sample)),
        sequence=0,
        host_monotonic_ns=1,
        host_wall_time_ns=2,
    )
    decoded = payload["eye_extra"]["left"]
    controlled_fields = {
        "blink_state",
        "openness_percent",
        "eyelid_up_normalized",
        "eyelid_down_normalized",
    }

    assert decoded[field] is not None
    assert all(decoded[name] is None for name in controlled_fields - {field})


def test_studio_pixel_formulas_use_eye_image_width_and_height() -> None:
    sample = sdk._EyeDataEx()
    sample.left_pupil.pupil_bit_mask = 1 << 2
    sample.left_pupil.pupil_diameter = 0.0445
    sample.left_ex_data.eye_data_ex_bit_mask = (1 << 2) | (1 << 3)
    sample.left_ex_data.eyelid_up = 0.1
    sample.left_ex_data.eyelid_down = 0.3775
    payload = sdk.decode_sdk_sample(
        ctypes.string_at(ctypes.byref(sample), ctypes.sizeof(sample)),
        sequence=0,
        host_monotonic_ns=1,
        host_wall_time_ns=2,
        image_metadata={"left_eye": {"width": 640, "height": 480}},
    )

    assert payload["pupil"]["left"]["diameter_px"] == pytest.approx(28.48)
    assert payload["eye_extra"]["left"]["eyelid_distance_px"] == pytest.approx(133.2)


def test_decode_rejects_callback_with_wrong_abi_size() -> None:
    with pytest.raises(sdk.SeveninvensunSdkError, match="callback bytes"):
        sdk.decode_sdk_sample(
            b"short",
            sequence=0,
            host_monotonic_ns=1,
            host_wall_time_ns=2,
        )


def test_runtime_validation_accepts_only_complete_allowlisted_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "aSeeGlassesPlusUserSDK"
    config = root / "bin" / "config"
    config.mkdir(parents=True)
    aseex = root / "bin" / "aSeeX.dll"
    usersdk = root / "bin" / "USERSDK.dll"
    aseex.write_bytes(b"public wrapper")
    usersdk.write_bytes(b"vendor runtime")
    (config / "config.ini").write_text("[config]\n", encoding="utf-8")

    hashes = {
        aseex: sdk.ASEEX_SHA256,
        usersdk: sdk.USERSDK_SHA256,
    }
    monkeypatch.setattr(
        sdk,
        "RUNTIME_FILE_SHA256",
        {
            "bin/aSeeX.dll": sdk.ASEEX_SHA256,
            "bin/USERSDK.dll": sdk.USERSDK_SHA256,
        },
    )
    monkeypatch.setattr(sdk, "_sha256", hashes.__getitem__)

    manifest = sdk.validate_sdk_runtime(tmp_path)

    assert manifest.sdk_root == root
    assert manifest.bin_directory == root / "bin"
    assert manifest.config_directory == config
    assert manifest.validated_file_count == 2


def test_runtime_validation_rejects_mixed_studio_or_a8_dlls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "bin" / "config"
    config.mkdir(parents=True)
    (tmp_path / "bin" / "aSeeX.dll").write_bytes(b"wrong")
    (tmp_path / "bin" / "USERSDK.dll").write_bytes(b"wrong")
    (config / "config.ini").write_text("[config]\n", encoding="utf-8")
    monkeypatch.setattr(
        sdk,
        "RUNTIME_FILE_SHA256",
        {
            "bin/aSeeX.dll": sdk.ASEEX_SHA256,
            "bin/USERSDK.dll": sdk.USERSDK_SHA256,
        },
    )

    with pytest.raises(sdk.SeveninvensunSdkError, match="Do not mix Studio, A8"):
        sdk.validate_sdk_runtime(tmp_path)


def test_runtime_validation_rejects_unknown_dll_in_sdk_bin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "bin" / "config"
    config.mkdir(parents=True)
    (tmp_path / "bin" / "aSeeX.dll").write_bytes(b"wrapper")
    (tmp_path / "bin" / "USERSDK.dll").write_bytes(b"runtime")
    (tmp_path / "bin" / "OpenCL.dll").write_bytes(b"unexpected")
    monkeypatch.setattr(
        sdk,
        "RUNTIME_FILE_SHA256",
        {
            "bin/aSeeX.dll": sdk.ASEEX_SHA256,
            "bin/USERSDK.dll": sdk.USERSDK_SHA256,
        },
    )

    with pytest.raises(sdk.SeveninvensunSdkError, match="Unknown DLLs"):
        sdk.validate_sdk_runtime(tmp_path)


def test_runtime_validation_reports_missing_allowlisted_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "aSeeX.dll").write_bytes(b"wrapper")
    monkeypatch.setattr(
        sdk,
        "RUNTIME_FILE_SHA256",
        {
            "bin/aSeeX.dll": sdk.ASEEX_SHA256,
            "bin/AlgInterface.dll": "expected",
        },
    )

    with pytest.raises(sdk.SeveninvensunSdkError, match="bin/AlgInterface.dll"):
        sdk.validate_sdk_runtime(tmp_path)


def test_callback_copies_structure_before_vendor_buffer_can_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = sdk.SeveninvensunRuntimeManifest(
        sdk_root=tmp_path,
        bin_directory=tmp_path,
        config_directory=tmp_path,
        aseex_sha256=sdk.ASEEX_SHA256,
        usersdk_sha256=sdk.USERSDK_SHA256,
        validated_file_count=len(sdk.RUNTIME_FILE_SHA256),
        runtime_bundle_sha256="f" * 64,
    )
    monkeypatch.setattr(sdk, "validate_sdk_runtime", lambda _path: manifest)
    capture = sdk.SeveninvensunGlassesSdkCapture(tmp_path, queue_capacity=1)
    vendor_sample = sdk._EyeDataEx.from_buffer_copy(_raw_sample())

    capture._on_gaze(ctypes.pointer(vendor_sample), None)
    vendor_sample.timestamp = 999_999
    decoded = capture.read_sample(timeout_seconds=0.01)

    assert decoded is not None
    assert decoded["device_timestamp_raw"] == 123_456


def test_bounded_callback_queue_counts_dropped_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = sdk.SeveninvensunRuntimeManifest(
        sdk_root=tmp_path,
        bin_directory=tmp_path,
        config_directory=tmp_path,
        aseex_sha256=sdk.ASEEX_SHA256,
        usersdk_sha256=sdk.USERSDK_SHA256,
        validated_file_count=len(sdk.RUNTIME_FILE_SHA256),
        runtime_bundle_sha256="f" * 64,
    )
    monkeypatch.setattr(sdk, "validate_sdk_runtime", lambda _path: manifest)
    capture = sdk.SeveninvensunGlassesSdkCapture(tmp_path, queue_capacity=1)
    vendor_sample = sdk._EyeDataEx.from_buffer_copy(_raw_sample())

    capture._on_gaze(ctypes.pointer(vendor_sample), None)
    capture._on_gaze(ctypes.pointer(vendor_sample), None)

    assert capture.dropped_callback_samples == 1
    assert capture.received_callback_samples == 2
    assert capture.pending_callback_samples == 1


def test_stop_waits_for_inflight_gaze_copy_and_rejects_late_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = sdk.SeveninvensunRuntimeManifest(
        sdk_root=tmp_path,
        bin_directory=tmp_path,
        config_directory=tmp_path,
        aseex_sha256=sdk.ASEEX_SHA256,
        usersdk_sha256=sdk.USERSDK_SHA256,
        validated_file_count=len(sdk.RUNTIME_FILE_SHA256),
        runtime_bundle_sha256="f" * 64,
    )
    monkeypatch.setattr(sdk, "validate_sdk_runtime", lambda _path: manifest)
    capture = sdk.SeveninvensunGlassesSdkCapture(tmp_path)
    callback_copy_entered = threading.Event()
    release_callback_copy = threading.Event()
    native_stop_called = threading.Event()
    stop_returned = threading.Event()
    original_string_at = ctypes.string_at

    class FakeDll:
        @staticmethod
        def _7i_stop() -> int:
            native_stop_called.set()
            return 0

    def blocking_string_at(address: object, size: int) -> bytes:
        callback_copy_entered.set()
        assert release_callback_copy.wait(timeout=1.0)
        return original_string_at(address, size)

    capture._dll = FakeDll()
    capture._started = True
    capture._start_attempted = True
    capture._start_was_attempted = True
    vendor_sample = sdk._EyeDataEx.from_buffer_copy(_raw_sample())
    monkeypatch.setattr(sdk.ctypes, "string_at", blocking_string_at)

    callback_thread = threading.Thread(
        target=capture._on_gaze,
        args=(ctypes.pointer(vendor_sample), None),
        daemon=True,
    )
    callback_thread.start()
    assert callback_copy_entered.wait(timeout=1.0)

    stop_thread = threading.Thread(
        target=lambda: (capture.stop(), stop_returned.set()),
        daemon=True,
    )
    stop_thread.start()
    assert native_stop_called.wait(timeout=1.0)
    assert not stop_returned.wait(timeout=0.05)

    release_callback_copy.set()
    callback_thread.join(timeout=1.0)
    stop_thread.join(timeout=1.0)
    assert not callback_thread.is_alive()
    assert not stop_thread.is_alive()
    assert stop_returned.is_set()
    assert capture.received_callback_samples == 1
    assert capture.pending_callback_samples == 1
    assert capture.post_stop_callback_samples == 0

    capture._on_gaze(ctypes.pointer(vendor_sample), None)

    assert capture.received_callback_samples == 1
    assert capture.pending_callback_samples == 1
    assert capture.post_stop_callback_samples == 1


@pytest.mark.parametrize(
    (
        "authorization_established",
        "sdk_stop_result",
        "device_disconnect_result",
        "expected",
    ),
    [
        (True, 0, 0, sdk.DEVICE_DISCONNECT_TERMINAL_DISCONNECTED),
        (True, 0, -22, sdk.DEVICE_DISCONNECT_TERMINAL_ALREADY_ABSENT),
        (True, -1, -22, None),
        (False, 0, -22, None),
        (True, 0, -21, None),
    ],
)
def test_disconnect_terminal_state_requires_exact_supported_lifecycle(
    authorization_established: bool,
    sdk_stop_result: int,
    device_disconnect_result: int,
    expected: str | None,
) -> None:
    assert (
        sdk.classify_device_disconnect_terminal_state(
            authorization_established=authorization_established,
            sdk_stop_result=sdk_stop_result,
            device_disconnect_result=device_disconnect_result,
        )
        == expected
    )


def test_closed_capture_is_terminal_and_retains_callback_lifetime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = sdk.SeveninvensunRuntimeManifest(
        sdk_root=tmp_path,
        bin_directory=tmp_path,
        config_directory=tmp_path,
        aseex_sha256=sdk.ASEEX_SHA256,
        usersdk_sha256=sdk.USERSDK_SHA256,
        validated_file_count=len(sdk.RUNTIME_FILE_SHA256),
        runtime_bundle_sha256="f" * 64,
    )
    monkeypatch.setattr(sdk, "validate_sdk_runtime", lambda _path: manifest)
    capture = sdk.SeveninvensunGlassesSdkCapture(tmp_path)

    capture.close()

    assert sdk._CAPTURE_LIFETIME_GUARD[-1] is capture
    with pytest.raises(sdk.SeveninvensunSdkError, match="cannot be reused"):
        capture.load()


@pytest.mark.parametrize("stop_result", [0, -1])
def test_started_capture_is_one_shot_even_after_stop(
    stop_result: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = sdk.SeveninvensunRuntimeManifest(
        sdk_root=tmp_path,
        bin_directory=tmp_path,
        config_directory=tmp_path,
        aseex_sha256=sdk.ASEEX_SHA256,
        usersdk_sha256=sdk.USERSDK_SHA256,
        validated_file_count=len(sdk.RUNTIME_FILE_SHA256),
        runtime_bundle_sha256="f" * 64,
    )
    monkeypatch.setattr(sdk, "validate_sdk_runtime", lambda _path: manifest)
    capture = sdk.SeveninvensunGlassesSdkCapture(tmp_path)

    class FakeDll:
        @staticmethod
        def _7i_stop() -> int:
            return stop_result

    capture._dll = FakeDll()
    capture._start_was_attempted = True
    capture._start_attempted = True
    capture.stop()

    assert capture.stop_result == stop_result
    assert sdk._CAPTURE_LIFETIME_GUARD[-1] is capture
    with pytest.raises(sdk.SeveninvensunSdkError, match="one-shot"):
        capture.start()


def test_partial_start_failure_still_calls_native_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = sdk.SeveninvensunRuntimeManifest(
        sdk_root=tmp_path,
        bin_directory=tmp_path,
        config_directory=tmp_path,
        aseex_sha256=sdk.ASEEX_SHA256,
        usersdk_sha256=sdk.USERSDK_SHA256,
        validated_file_count=len(sdk.RUNTIME_FILE_SHA256),
        runtime_bundle_sha256="f" * 64,
    )
    monkeypatch.setattr(sdk, "validate_sdk_runtime", lambda _path: manifest)
    capture = sdk.SeveninvensunGlassesSdkCapture(tmp_path)

    class FakeDll:
        stop_calls = 0

        @staticmethod
        def _7i_set_camera_state_callback(_callback: object, _context: object) -> int:
            return 0

        @staticmethod
        def _7i_set_image_callback(_callback: object, _context: object) -> int:
            return -7

        @staticmethod
        def _7i_set_gaze_callback(_callback: object, _context: object) -> int:
            return 0

        @classmethod
        def _7i_stop(cls) -> int:
            cls.stop_calls += 1
            return 0

    capture._dll = FakeDll()
    capture._callbacks = (object(), object(), object())

    with pytest.raises(sdk.SeveninvensunSdkCallError):
        capture.start()
    capture.close()

    assert FakeDll.stop_calls == 1
    assert capture.stop_result == 0
    with pytest.raises(sdk.SeveninvensunSdkError, match="cannot be reused"):
        capture.load()


def test_helper_import_does_not_eager_load_qt_numpy_or_opencv() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import oculidoc.vendor_helpers.check_seveninvensun_glasses_live; "
                "print(','.join(name for name in ('PySide6','numpy','cv2') if name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.stdout.strip() == ""


def test_live_acceptance_requires_transport_fields_images_and_imu() -> None:
    required_coverage = {
        name: {"valid_samples": 1, "valid_ratio_percent": 1.0}
        for name in (
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
    }
    details = {
        "sample_count": 240,
        "host_sample_rate_hz": 240.0,
        "nonpositive_device_timestamp_deltas": 0,
        "dropped_callback_samples": 0,
        "received_callback_samples": 240,
        "discarded_callback_samples": 0,
        "pending_callback_samples": 0,
        "post_stop_callback_samples": 0,
        "sdk_stop_result": 0,
        "sdk_disconnect_result": 0,
        "authorization_prompt_used": True,
        "authorization_established": True,
        "sdk_disconnect_terminal_state": sdk.DEVICE_DISCONNECT_TERMINAL_DISCONNECTED,
        "coverage": required_coverage,
        "last_image_metadata": {
            "scene": {"width": 1280, "height": 960},
            "left_eye": {"width": 640, "height": 480},
            "right_eye": {"width": 640, "height": 480},
        },
        "gyroscope_requested": True,
        "distinct_nonzero_imu_timestamps": 10,
    }

    assert _live_acceptance_issues(details, expected_resolution="1280x960") == []

    details["sdk_disconnect_result"] = -22
    details["sdk_disconnect_terminal_state"] = sdk.DEVICE_DISCONNECT_TERMINAL_ALREADY_ABSENT
    assert _live_acceptance_issues(details, expected_resolution="1280x960") == []
    details["sdk_disconnect_result"] = 0
    details["sdk_disconnect_terminal_state"] = sdk.DEVICE_DISCONNECT_TERMINAL_DISCONNECTED

    details["distinct_nonzero_imu_timestamps"] = 0
    required_coverage["recommended_gaze"]["valid_samples"] = 0
    issues = _live_acceptance_issues(details, expected_resolution="1280x960")

    assert "imu_stream_not_observed" in issues
    assert any(issue.startswith("missing_core_fields:recommended_gaze") for issue in issues)

    details["last_image_metadata"]["left_eye"] = {}
    details["sdk_stop_result"] = None
    details["pending_callback_samples"] = 1
    issues = _live_acceptance_issues(details, expected_resolution="1280x960")

    assert "invalid_image_dimensions:left_eye" in issues
    assert "sdk_stop_not_confirmed_successful" in issues
    assert "callback_queue_not_fully_consumed" in issues
    assert "callback_accounting_mismatch" in issues


def test_coverage_does_not_count_all_null_vector_as_valid() -> None:
    summary = _CaptureSummary()
    summary.add(
        {
            "host_monotonic_ns": 1,
            "device_timestamp_raw": 1,
            "gaze": {
                "recommended": {"gaze_point_scene_centered_px": [None, None, None]},
            },
            "imu": {"timestamp_raw": 0},
        }
    )

    assert summary.as_dict()["coverage"]["recommended_gaze"]["valid_samples"] == 0


def test_report_writer_never_overwrites_existing_result(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    _write_json(path, {"result": "first"})

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        _write_json(path, {"result": "second"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"result": "first"}
    assert not path.with_name("report.json.tmp").exists()
