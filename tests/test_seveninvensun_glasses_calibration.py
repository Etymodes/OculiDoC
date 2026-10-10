from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from oculidoc.vendor_helpers import calibrate_seveninvensun_glasses as calibration
from oculidoc.vendor_helpers import check_seveninvensun_glasses_live as live_probe
from oculidoc.vendor_helpers import seveninvensun_glasses_sdk as sdk


class _FakeFunction:
    def __init__(self, result: int = 0, callback: Any = None) -> None:
        self.result = result
        self.callback = callback
        self.calls: list[tuple[Any, ...]] = []
        self.argtypes: list[Any] = []
        self.restype: Any = None

    def __call__(self, *args: Any) -> int:
        self.calls.append(args)
        if self.callback is not None:
            return int(self.callback(*args))
        return self.result


class _FakeDll:
    def __init__(self) -> None:
        self.functions: dict[str, _FakeFunction] = {}

    def __getattr__(self, name: str) -> _FakeFunction:
        return self.functions.setdefault(name, _FakeFunction())


def _manifest(path: Path) -> sdk.SeveninvensunRuntimeManifest:
    return sdk.SeveninvensunRuntimeManifest(
        sdk_root=path,
        bin_directory=path,
        config_directory=path,
        aseex_sha256=sdk.ASEEX_SHA256,
        usersdk_sha256=sdk.USERSDK_SHA256,
        validated_file_count=len(sdk.RUNTIME_FILE_SHA256),
        runtime_bundle_sha256="f" * 64,
    )


def _capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[sdk.SeveninvensunGlassesSdkCapture, _FakeDll]:
    manifest = _manifest(tmp_path)
    monkeypatch.setattr(sdk, "validate_sdk_runtime", lambda _path: manifest)
    capture = sdk.SeveninvensunGlassesSdkCapture(tmp_path)
    fake = _FakeDll()
    capture._dll = fake
    capture._started = True
    capture._scene_dimensions = (1280, 960)
    capture._calibration_callbacks = (object(), object(), object(), object())
    return capture, fake


def test_configure_functions_binds_every_calibration_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, fake = _capture(tmp_path, monkeypatch)

    capture._configure_functions()

    assert len(capture._callbacks) == 3
    assert len(capture._calibration_callbacks) == 4
    assert fake._7i_start_calibration_point.restype is ctypes.c_int
    assert fake._7i_get_mpu_id.argtypes == [ctypes.POINTER(ctypes.c_char), ctypes.c_int]


def test_mpu_id_is_read_after_start_hashed_with_domain_and_never_returned_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, fake = _capture(tmp_path, monkeypatch)
    raw_id = b"opaque-device-id"

    def get_mpu_id(target: Any, size: int) -> int:
        assert size == 256
        ctypes.memmove(target, raw_id + b"\x00", len(raw_id) + 1)
        return 0

    fake.functions["_7i_get_mpu_id"] = _FakeFunction(callback=get_mpu_id)

    result = capture.get_mpu_id_hash()

    assert result == hashlib.sha256(b"oculidoc-asee-mpu-v1\x00" + raw_id).hexdigest()
    assert raw_id.decode() not in result


@pytest.mark.parametrize("mode", ["error", "empty", "unterminated"])
def test_mpu_id_failure_modes_fail_closed(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, fake = _capture(tmp_path, monkeypatch)

    def get_mpu_id(target: Any, size: int) -> int:
        if mode == "error":
            return -9
        if mode == "unterminated":
            ctypes.memset(target, ord("X"), size)
        return 0

    fake.functions["_7i_get_mpu_id"] = _FakeFunction(callback=get_mpu_id)

    with pytest.raises(sdk.SeveninvensunSdkError):
        capture.get_mpu_id_hash()


def test_runtime_bundle_hash_is_order_stable_and_changes_with_any_file() -> None:
    first = sdk._runtime_bundle_hash({"b": "2", "a": "1"})

    assert first == sdk._runtime_bundle_hash({"a": "1", "b": "2"})
    assert first != sdk._runtime_bundle_hash({"a": "1", "b": "3"})


def test_calibration_advances_only_after_both_eyes_finish_and_retries_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, _fake = _capture(tmp_path, monkeypatch)
    capture.begin_calibration(3)
    capture.start_calibration_point(1, -100.0, -50.0)
    token = capture._active_calibration_token
    assert token is not None

    capture._on_left_calibration_finish(1, 0, token)
    with pytest.raises(sdk.SeveninvensunSdkError, match="both eyes"):
        capture.start_calibration_point(2, 100.0, -50.0)
    capture._on_right_calibration_finish(1, 0, token)
    capture.start_calibration_point(2, 100.0, -50.0)

    retry_token = capture._active_calibration_token
    assert retry_token is not None
    capture._on_left_calibration_finish(2, -1, retry_token)
    capture._on_right_calibration_finish(2, 0, retry_token)
    capture.start_calibration_point(2, 100.0, -50.0)

    assert capture._successful_calibration_indices == {1}


def test_late_callback_from_cancelled_point_cannot_complete_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, fake = _capture(tmp_path, monkeypatch)
    capture.begin_calibration(3)
    capture.start_calibration_point(1, 0.0, 0.0)
    old_token = capture._active_calibration_token
    assert old_token is not None

    def cancel(eye: int) -> int:
        if eye == sdk.LEFT_EYE:
            capture._on_left_calibration_finish(1, -2, old_token)
        else:
            capture._on_right_calibration_finish(1, -2, old_token)
        return 0

    fake.functions["_7i_cancel_calibration"] = _FakeFunction(callback=cancel)
    capture.cancel_calibration_point()
    capture.start_calibration_point(1, 10.0, 10.0)
    new_token = capture._active_calibration_token
    assert new_token is not None and new_token != old_token

    capture._on_left_calibration_finish(1, 0, old_token)
    capture._on_right_calibration_finish(1, 0, old_token)

    assert capture._active_calibration_token == new_token
    assert capture._successful_calibration_indices == set()


def test_cancel_timeout_blocks_retry_and_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, _fake = _capture(tmp_path, monkeypatch)
    capture.begin_calibration(3)
    capture.start_calibration_point(1, 0.0, 0.0)

    with pytest.raises(sdk.SeveninvensunSdkError, match="terminal callbacks"):
        capture.cancel_calibration_point(timeout_seconds=0.01)
    with pytest.raises(sdk.SeveninvensunSdkError, match="both eyes"):
        capture.start_calibration_point(1, 10.0, 10.0)
    with pytest.raises(sdk.SeveninvensunSdkError, match="active point"):
        capture._complete_calibration()


def test_partial_point_start_waits_for_started_eye_cancel_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, fake = _capture(tmp_path, monkeypatch)
    capture.begin_calibration(3)

    def start_point(eye: int, *_arguments: Any) -> int:
        return 0 if eye == sdk.LEFT_EYE else -77

    def cancel(eye: int) -> int:
        if eye == sdk.LEFT_EYE:
            token = capture._active_calibration_token
            assert token is not None
            capture._on_left_calibration_finish(1, -2, token)
        return 0

    fake.functions["_7i_start_calibration_point"] = _FakeFunction(callback=start_point)
    fake.functions["_7i_cancel_calibration"] = _FakeFunction(callback=cancel)

    with pytest.raises(sdk.SeveninvensunSdkCallError, match="-77"):
        capture.start_calibration_point(1, 0.0, 0.0)

    assert capture._active_calibration_token is None
    assert capture._successful_calibration_indices == set()


def test_compute_get_complete_and_apply_exact_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, fake = _capture(tmp_path, monkeypatch)
    capture._calibration_point_count = 3
    capture._successful_calibration_indices = {1, 2, 3}

    def get_data(key: bytes, _key_length: int, target: Any, size_pointer: Any) -> int:
        size = ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_int)).contents
        if key == b"get_score":
            score = b"8.5"
            ctypes.memmove(target, score, len(score))
            size.value = len(score)
            return 0
        value = 0x11 if key == b"biLeft" else 0x22
        ctypes.memset(target, value, sdk.CALIBRATION_COEFFICIENT_SIZE)
        size.value = sdk.CALIBRATION_COEFFICIENT_SIZE
        return 0

    fake.functions["_7i_get_data"] = _FakeFunction(callback=get_data)
    device_checks = 0

    def get_device_hash() -> str:
        nonlocal device_checks
        device_checks += 1
        return "d" * 64

    monkeypatch.setattr(capture, "get_mpu_id_hash", get_device_hash)
    left, right, score = capture.compute_calibration_coefficients()
    capture.apply_calibration_coefficients(left, right, expected_device_hash="d" * 64)

    assert left == bytes([0x11]) * 1024
    assert right == bytes([0x22]) * 1024
    assert score == "8.5"
    assert len(fake._7i_complete_calibration.calls) == 1
    assert len(fake._7i_set_data.calls) == 2
    assert device_checks == 3
    assert capture.calibration_active is False


def test_compute_failure_still_completes_session_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, fake = _capture(tmp_path, monkeypatch)
    capture._calibration_point_count = 1
    capture._successful_calibration_indices = {1}
    fake.functions["_7i_compute_calibration"] = _FakeFunction(result=-251252)

    with pytest.raises(sdk.SeveninvensunSdkCallError, match="-251252"):
        capture.compute_calibration_coefficients()

    assert len(fake._7i_complete_calibration.calls) == 1
    assert capture.calibration_active is False


def test_coefficient_apply_stops_before_second_eye_if_device_binding_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, fake = _capture(tmp_path, monkeypatch)
    device_hashes = iter(("d" * 64, "e" * 64))
    monkeypatch.setattr(capture, "get_mpu_id_hash", lambda: next(device_hashes))

    with pytest.raises(sdk.SeveninvensunSdkError, match="identity changed"):
        capture.apply_calibration_coefficients(
            b"L" * 1024,
            b"R" * 1024,
            expected_device_hash="d" * 64,
        )

    assert len(fake._7i_set_data.calls) == 1


def test_scene_callback_copies_encoded_payload_before_vendor_buffer_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, _fake = _capture(tmp_path, monkeypatch)
    capture._scene_decoder_thread = object()  # type: ignore[assignment]
    vendor_buffer = (ctypes.c_ubyte * 4)(1, 2, 3, 4)

    capture._on_image(13, ctypes.addressof(vendor_buffer), 4, 1280, 960, 7, None)
    vendor_buffer[:] = (9, 9, 9, 9)
    queued = capture._scene_encoded_queue.get_nowait()

    assert queued.encoded_bytes == b"\x01\x02\x03\x04"
    assert queued.device_timestamp_raw == 7


def test_fresh_sample_window_discards_callback_that_entered_in_cutoff_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, _fake = _capture(tmp_path, monkeypatch)
    vendor_sample = sdk._EyeDataEx()
    entered_copy = threading.Event()
    release_copy = threading.Event()
    original_string_at = ctypes.string_at
    thread_errors: list[Exception] = []

    def blocking_string_at(pointer: Any, size: int) -> bytes:
        if size == ctypes.sizeof(sdk._EyeDataEx):
            entered_copy.set()
            assert release_copy.wait(timeout=1.0)
        return original_string_at(pointer, size)

    def invoke_callback() -> None:
        try:
            capture._on_gaze(ctypes.pointer(vendor_sample), None)
        except Exception as error:  # noqa: BLE001 - relay thread failure to the test
            thread_errors.append(error)

    monkeypatch.setattr(sdk.time, "perf_counter_ns", lambda: 123_456_789)
    monkeypatch.setattr(sdk.ctypes, "string_at", blocking_string_at)
    callback_thread = threading.Thread(target=invoke_callback)
    callback_thread.start()
    assert entered_copy.wait(timeout=1.0)
    (
        _cutoff_ns,
        received_baseline,
        _dropped_baseline,
        discarded_baseline,
    ) = capture.begin_fresh_sample_window()
    release_copy.set()
    callback_thread.join(timeout=1.0)

    assert not callback_thread.is_alive()
    assert not thread_errors
    assert capture.received_callback_samples == received_baseline + 1
    assert capture.discarded_callback_samples == discarded_baseline + 1
    assert capture.pending_callback_samples == 0


def test_scene_callback_rejects_manual_code_10_and_oversized_payload_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, _fake = _capture(tmp_path, monkeypatch)
    capture._scene_decoder_thread = object()  # type: ignore[assignment]
    vendor_buffer = (ctypes.c_ubyte * 1)(1)

    capture._on_image(10, ctypes.addressof(vendor_buffer), 1, 1280, 960, 7, None)
    capture._on_image(
        13,
        ctypes.addressof(vendor_buffer),
        1280 * 960 * 3 + 1,
        1280,
        960,
        8,
        None,
    )

    assert capture._scene_encoded_queue.empty()
    assert capture.rejected_scene_images == 1


def _wait_for_decode_result(
    capture: sdk.SeveninvensunGlassesSdkCapture,
    result: int,
) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if capture.scene_decode_return_codes.get(result, 0):
            return
        time.sleep(0.005)
    raise AssertionError("decoder result was not observed")


@pytest.mark.parametrize("decoder_result", [0, -1, 10])
def test_decoder_does_not_publish_incomplete_or_failed_output(
    decoder_result: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, _fake = _capture(tmp_path, monkeypatch)
    capture._started = False
    decoder = _FakeDll()
    decoder.functions["_7i_h265_decode"] = _FakeFunction(result=decoder_result)
    monkeypatch.setattr(ctypes, "WinDLL", lambda _path: decoder, raising=False)
    capture.start_scene_decoder("800x600")
    vendor_buffer = (ctypes.c_ubyte * 4)(1, 2, 3, 4)
    capture._on_image(13, ctypes.addressof(vendor_buffer), 4, 800, 600, 7, None)
    _wait_for_decode_result(capture, decoder_result)

    assert capture.latest_scene_frame() is None
    capture.stop_scene_decoder()


def test_decoder_publishes_only_exact_complete_bgr_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, _fake = _capture(tmp_path, monkeypatch)
    capture._started = False
    decoder = _FakeDll()

    def decode(_size: int, _encoded: Any, output: Any, capacity: int) -> int:
        ctypes.memset(output, 0x5A, capacity)
        return capacity

    expected_size = 800 * 600 * 3
    decoder.functions["_7i_h265_decode"] = _FakeFunction(callback=decode)
    monkeypatch.setattr(ctypes, "WinDLL", lambda _path: decoder, raising=False)
    capture.start_scene_decoder("800x600")
    callback_arrival_ns = 123_000_000_000
    monkeypatch.setattr(sdk.time, "perf_counter_ns", lambda: callback_arrival_ns)
    vendor_buffer = (ctypes.c_ubyte * 4)(1, 2, 3, 4)
    capture._on_image(13, ctypes.addressof(vendor_buffer), 4, 800, 600, 7, None)
    _wait_for_decode_result(capture, expected_size)
    frame = capture.latest_scene_frame()

    assert frame is not None
    assert frame.decoder_result_raw == expected_size
    assert frame.host_monotonic_ns == callback_arrival_ns
    assert len(frame.bgr_bytes) == expected_size
    assert frame.bgr_bytes[:3] == b"ZZZ"
    assert not calibration.scene_frame_is_fresh(
        frame,
        now_monotonic_ns=callback_arrival_ns + 2_000_000_000,
    )
    capture.stop_scene_decoder()


def test_scene_frame_freshness_requires_recent_complete_output() -> None:
    now = time.perf_counter_ns()
    fresh = sdk.SeveninvensunSceneFrame(b"\x00\x00\x00", 1, 1, 2, now, 3, 3)
    stale = sdk.SeveninvensunSceneFrame(b"\x00\x00\x00", 1, 1, 2, now - 2_000_000_000, 3, 3)
    failed = sdk.SeveninvensunSceneFrame(b"\x00\x00\x00", 1, 1, 2, now, 3, -1)

    assert calibration.scene_frame_is_fresh(fresh, now_monotonic_ns=now)
    assert not calibration.scene_frame_is_fresh(stale, now_monotonic_ns=now)
    assert not calibration.scene_frame_is_fresh(failed, now_monotonic_ns=now)


def test_aspect_fit_click_mapping_rejects_letterbox_and_preserves_native_pixels() -> None:
    assert calibration.map_aspect_fit_click(
        widget_width=1000,
        widget_height=1000,
        image_width=1280,
        image_height=720,
        click_x=500,
        click_y=500,
    ) == pytest.approx((640.0, 360.0))
    assert (
        calibration.map_aspect_fit_click(
            widget_width=1000,
            widget_height=1000,
            image_width=1280,
            image_height=720,
            click_x=500,
            click_y=100,
        )
        is None
    )


def _verification_sample(timestamp: int, *, valid: bool) -> dict[str, Any]:
    gaze_payload = {
        "gaze_point_scene_top_left_px": [640.0, 480.0, 0.0],
        "calibration_score": 1.0,
    }
    return {
        "device_timestamp_raw": timestamp,
        "validity": {"eye": valid, "left": valid, "right": valid},
        "gaze": {
            "recommended": dict(gaze_payload),
            "left": dict(gaze_payload),
            "right": dict(gaze_payload),
        },
    }


def test_post_calibration_gate_detects_current_all_invalid_failure() -> None:
    summary = calibration.evaluate_post_calibration(
        [_verification_sample(index, valid=False) for index in range(60)],
        scene_width=1280,
        scene_height=960,
        received_callback_samples=60,
        dropped_callback_samples=0,
        discarded_callback_samples=0,
        pending_callback_samples=0,
        post_stop_callback_samples=0,
    )

    assert summary["result"] == "REVIEW_REQUIRED"
    assert summary["valid_samples"] == {"recommended": 0, "left": 0, "right": 0}


def test_post_calibration_gate_passes_fresh_monotonic_valid_callbacks() -> None:
    summary = calibration.evaluate_post_calibration(
        [_verification_sample(index, valid=index < 12) for index in range(60)],
        scene_width=1280,
        scene_height=960,
        received_callback_samples=60,
        dropped_callback_samples=0,
        discarded_callback_samples=0,
        pending_callback_samples=0,
        post_stop_callback_samples=0,
    )

    assert summary["result"] == "PASS"
    assert summary["valid_samples"] == {"recommended": 12, "left": 12, "right": 12}


def test_post_calibration_gate_rejects_accounting_loss_and_out_of_frame_gaze() -> None:
    samples = [_verification_sample(index, valid=True) for index in range(30)]
    samples[0]["gaze"]["recommended"]["gaze_point_scene_top_left_px"] = [5000.0, 1.0]

    summary = calibration.evaluate_post_calibration(
        samples,
        scene_width=1280,
        scene_height=960,
        received_callback_samples=31,
        dropped_callback_samples=0,
        discarded_callback_samples=0,
        pending_callback_samples=0,
        post_stop_callback_samples=0,
    )

    assert summary["result"] == "REVIEW_REQUIRED"
    assert "callback_accounting_mismatch" in summary["issues"]
    assert summary["valid_samples"]["recommended"] == 29


def test_post_calibration_gate_rejects_callback_observed_after_stop() -> None:
    summary = calibration.evaluate_post_calibration(
        [_verification_sample(index, valid=True) for index in range(30)],
        scene_width=1280,
        scene_height=960,
        received_callback_samples=30,
        dropped_callback_samples=0,
        discarded_callback_samples=0,
        pending_callback_samples=0,
        post_stop_callback_samples=1,
    )

    assert summary["result"] == "REVIEW_REQUIRED"
    assert "callback_observed_after_stop" in summary["issues"]


def _bundle_metadata() -> dict[str, Any]:
    verification = calibration.evaluate_post_calibration(
        [_verification_sample(index, valid=True) for index in range(30)],
        scene_width=1280,
        scene_height=960,
        received_callback_samples=30,
        dropped_callback_samples=0,
        discarded_callback_samples=0,
        pending_callback_samples=0,
        post_stop_callback_samples=0,
    )
    return {
        "sdk_version": "2.2.22.2",
        "runtime_integrity": {
            "aSeeX_sha256": sdk.ASEEX_SHA256,
            "USERSDK_sha256": sdk.USERSDK_SHA256,
            "validated_file_count": len(sdk.RUNTIME_FILE_SHA256),
            "runtime_bundle_sha256": "f" * 64,
        },
        "environment": "indoor",
        "resolution": "1280x960",
        "verification": verification,
        "native_cleanup_results": {
            "calibration_complete": 0,
            "sdk_stop": 0,
            "device_disconnect": 0,
            "scene_decoder_release": 0,
            "scene_decoder_error": None,
        },
        "authorization_established": True,
        "device_disconnect_terminal_state": sdk.DEVICE_DISCONNECT_TERMINAL_DISCONNECTED,
        "device_binding_rechecked_before_stop": True,
    }


def test_verified_bundle_is_atomic_bound_and_contains_no_raw_profile_id(tmp_path: Path) -> None:
    profile_id = "12345678-1234-4234-9234-123456789abc"
    profile_hash = calibration.subject_hash(profile_id)
    bundle = calibration.save_verified_calibration_bundle(
        tmp_path,
        profile_hash=profile_hash,
        device_hash="d" * 64,
        left=b"L" * 1024,
        right=b"R" * 1024,
        metadata={
            **_bundle_metadata(),
            "protocol": "caller-cannot-override",
            "profile_id_sha256": "0" * 64,
        },
    )

    manifest = json.loads(bundle.read_text(encoding="utf-8"))
    assert manifest["profile_id_sha256"] == profile_hash
    assert manifest["device_mpu_id_sha256"] == "d" * 64
    assert manifest["protocol"] == calibration.CALIBRATION_PROTOCOL
    assert profile_id not in json.dumps(manifest)
    assert not list(tmp_path.glob(".*.tmp"))
    assert calibration.load_calibration_bundle(
        bundle,
        expected_profile_hash=profile_hash,
        expected_device_hash="d" * 64,
        expected_sdk_version="2.2.22.2",
        expected_runtime_bundle_hash="f" * 64,
        expected_environment="indoor",
        expected_resolution="1280x960",
    ) == (b"L" * 1024, b"R" * 1024)


def test_bundle_publisher_rejects_pass_label_without_complete_verification(tmp_path: Path) -> None:
    metadata = _bundle_metadata()
    metadata["verification"] = {"result": "PASS"}

    with pytest.raises(ValueError, match="technical output-validity PASS"):
        calibration.save_verified_calibration_bundle(
            tmp_path,
            profile_hash="a" * 64,
            device_hash="d" * 64,
            left=b"L" * 1024,
            right=b"R" * 1024,
            metadata=metadata,
        )


def test_bundle_publisher_rejects_unconfirmed_native_cleanup(tmp_path: Path) -> None:
    metadata = _bundle_metadata()
    metadata["native_cleanup_results"]["device_disconnect"] = -21
    metadata["device_disconnect_terminal_state"] = None

    with pytest.raises(ValueError, match="native cleanup"):
        calibration.save_verified_calibration_bundle(
            tmp_path,
            profile_hash="a" * 64,
            device_hash="d" * 64,
            left=b"L" * 1024,
            right=b"R" * 1024,
            metadata=metadata,
        )


def test_bundle_publisher_accepts_field_observed_already_disconnected_state(
    tmp_path: Path,
) -> None:
    metadata = _bundle_metadata()
    metadata["native_cleanup_results"]["device_disconnect"] = -22
    metadata["device_disconnect_terminal_state"] = sdk.DEVICE_DISCONNECT_TERMINAL_ALREADY_ABSENT

    bundle = calibration.save_verified_calibration_bundle(
        tmp_path,
        profile_hash="a" * 64,
        device_hash="d" * 64,
        left=b"L" * 1024,
        right=b"R" * 1024,
        metadata=metadata,
    )

    manifest = json.loads(bundle.read_text(encoding="utf-8"))
    assert manifest["native_cleanup_results"]["device_disconnect"] == -22
    assert (
        manifest["device_disconnect_terminal_state"]
        == sdk.DEVICE_DISCONNECT_TERMINAL_ALREADY_ABSENT
    )


@pytest.mark.parametrize(
    ("authorization_established", "sdk_stop_result"),
    [(False, 0), (True, -1)],
)
def test_already_disconnected_state_requires_authorization_and_successful_stop(
    authorization_established: bool,
    sdk_stop_result: int,
) -> None:
    metadata = _bundle_metadata()
    metadata["authorization_established"] = authorization_established
    metadata["native_cleanup_results"]["sdk_stop"] = sdk_stop_result
    metadata["native_cleanup_results"]["device_disconnect"] = -22
    metadata["device_disconnect_terminal_state"] = sdk.DEVICE_DISCONNECT_TERMINAL_ALREADY_ABSENT

    assert not calibration.native_cleanup_is_publishable(metadata)


def test_bundle_publish_failure_removes_temp_and_preserves_existing_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = tmp_path / f"asee_glasses_{'a' * 64}.aseecal"
    existing.write_bytes(b"old")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        calibration.save_verified_calibration_bundle(
            tmp_path,
            profile_hash="a" * 64,
            device_hash="d" * 64,
            left=b"L" * 1024,
            right=b"R" * 1024,
            metadata=_bundle_metadata(),
        )

    assert existing.read_bytes() == b"old"
    assert not list(tmp_path.glob(".*.tmp"))


def test_stale_temp_cleanup_never_removes_concurrent_recent_publish(tmp_path: Path) -> None:
    recent = tmp_path / f".asee_glasses_{'a' * 64}.aseecal.{'b' * 32}.tmp"
    stale = tmp_path / f".asee_glasses_{'c' * 64}.aseecal.{'d' * 32}.tmp"
    recent.write_bytes(b"recent")
    stale.write_bytes(b"stale")
    old = time.time() - 25 * 60 * 60
    os.utime(stale, (old, old))

    assert calibration.cleanup_stale_bundle_temps(tmp_path) == 1
    assert recent.read_bytes() == b"recent"
    assert not stale.exists()


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("expected_profile_hash", "b" * 64),
        ("expected_device_hash", "e" * 64),
        ("expected_sdk_version", "wrong"),
        ("expected_runtime_bundle_hash", "a" * 64),
        ("expected_environment", "outdoor"),
        ("expected_resolution", "800x600"),
    ],
)
def test_bundle_loader_rejects_every_external_binding_mismatch(
    tmp_path: Path,
    field: str,
    wrong_value: str,
) -> None:
    bundle = calibration.save_verified_calibration_bundle(
        tmp_path,
        profile_hash="a" * 64,
        device_hash="d" * 64,
        left=b"L" * 1024,
        right=b"R" * 1024,
        metadata=_bundle_metadata(),
    )
    expectations = {
        "expected_profile_hash": "a" * 64,
        "expected_device_hash": "d" * 64,
        "expected_sdk_version": "2.2.22.2",
        "expected_runtime_bundle_hash": "f" * 64,
        "expected_environment": "indoor",
        "expected_resolution": "1280x960",
    }
    expectations[field] = wrong_value

    with pytest.raises(sdk.SeveninvensunSdkError, match="mismatch"):
        calibration.load_calibration_bundle(bundle, **expectations)


def test_bundle_loader_rejects_tampered_coefficient_before_returning_pair(tmp_path: Path) -> None:
    bundle = calibration.save_verified_calibration_bundle(
        tmp_path,
        profile_hash="a" * 64,
        device_hash="d" * 64,
        left=b"L" * 1024,
        right=b"R" * 1024,
        metadata=_bundle_metadata(),
    )
    manifest = json.loads(bundle.read_text(encoding="utf-8"))
    manifest["coefficients"]["left"]["data"] = "AAAA"
    bundle.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(sdk.SeveninvensunSdkError, match="integrity"):
        calibration.load_calibration_bundle(
            bundle,
            expected_profile_hash="a" * 64,
            expected_device_hash="d" * 64,
            expected_sdk_version="2.2.22.2",
            expected_runtime_bundle_hash="f" * 64,
            expected_environment="indoor",
            expected_resolution="1280x960",
        )


def test_live_probe_binding_mismatch_calls_no_coefficient_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = calibration.save_verified_calibration_bundle(
        tmp_path,
        profile_hash="a" * 64,
        device_hash="d" * 64,
        left=b"L" * 1024,
        right=b"R" * 1024,
        metadata=_bundle_metadata(),
    )

    class FakeCapture:
        apply_calls = 0

        def __init__(self, _sdk_root: Path) -> None:
            self.manifest = _manifest(tmp_path)

        def load(self) -> str:
            return "2.2.22.2"

        def start(self, **_settings: Any) -> None:
            return None

        def get_mpu_id_hash(self) -> str:
            return "d" * 64

        def apply_calibration_coefficients(
            self,
            _left: bytes,
            _right: bytes,
            *,
            expected_device_hash: str,
        ) -> None:
            assert expected_device_hash == "d" * 64
            type(self).apply_calls += 1

        def close(self) -> None:
            return None

    monkeypatch.setattr(live_probe, "SeveninvensunGlassesSdkCapture", FakeCapture)
    arguments = SimpleNamespace(
        calibration_bundle=bundle,
        profile_hash="b" * 64,
        sdk_root=tmp_path,
        output_dir=tmp_path,
        run_id="run",
        prompt_authorization=False,
        environment="indoor",
        resolution="1280x960",
        no_gyroscope=True,
        duration=1.0,
    )

    with pytest.raises(sdk.SeveninvensunSdkError, match="profile_id_sha256"):
        live_probe._capture_live(arguments)

    assert FakeCapture.apply_calls == 0


def test_live_probe_discards_every_callback_before_bound_pair_is_fully_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = calibration.save_verified_calibration_bundle(
        tmp_path,
        profile_hash="a" * 64,
        device_hash="d" * 64,
        left=b"L" * 1024,
        right=b"R" * 1024,
        metadata=_bundle_metadata(),
    )

    class FakeCapture:
        def __init__(self, _sdk_root: Path) -> None:
            self.manifest = _manifest(tmp_path)
            self.received_callback_samples = 0
            self.dropped_callback_samples = 0
            self.discarded_callback_samples = 0
            self.pending_callback_samples = 0
            self.post_stop_callback_samples = 0
            self.camera_state = 1
            self.image_metadata: dict[str, Any] = {}
            self.stop_result = 0
            self.disconnect_result = 0
            self.authorization_was_established = False
            self.fresh_emitted = False

        def load(self) -> str:
            return "2.2.22.2"

        def start(self, **_settings: Any) -> None:
            self.received_callback_samples = 1

        def get_mpu_id_hash(self) -> str:
            return "d" * 64

        def apply_calibration_coefficients(
            self,
            left: bytes,
            right: bytes,
            *,
            expected_device_hash: str,
        ) -> None:
            assert (left, right) == (b"L" * 1024, b"R" * 1024)
            assert expected_device_hash == "d" * 64
            self.received_callback_samples = 2

        def begin_fresh_sample_window(self) -> tuple[int, int, int, int]:
            return (
                time.perf_counter_ns(),
                self.received_callback_samples,
                self.dropped_callback_samples,
                self.discarded_callback_samples,
            )

        def read_sample(self, timeout_seconds: float) -> dict[str, Any] | None:
            assert timeout_seconds > 0
            if self.fresh_emitted:
                return None
            self.fresh_emitted = True
            self.received_callback_samples += 1
            return {
                "sequence": 2,
                "host_monotonic_ns": time.perf_counter_ns(),
                "device_timestamp_raw": 3,
                "imu": {"timestamp_raw": 0},
            }

        def close(self) -> None:
            return None

    monkeypatch.setattr(live_probe, "SeveninvensunGlassesSdkCapture", FakeCapture)
    arguments = SimpleNamespace(
        calibration_bundle=bundle,
        profile_hash="a" * 64,
        sdk_root=tmp_path,
        output_dir=tmp_path,
        run_id="success",
        prompt_authorization=False,
        environment="indoor",
        resolution="1280x960",
        no_gyroscope=True,
        duration=0.02,
    )

    details, raw_path, _manifest_result = live_probe._capture_live(arguments)
    raw_rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]

    assert [row["sequence"] for row in raw_rows] == [2]
    assert details["received_callback_samples"] == 1
    assert details["sample_count"] == 1
    assert details["pre_capture_callbacks_discarded_or_dropped"] == 2


def test_subject_hash_requires_canonical_opaque_uuid() -> None:
    with pytest.raises(ValueError, match="UUID"):
        calibration.subject_hash("User1-test")


def test_geometry_rejects_close_and_collinear_targets() -> None:
    assert calibration.calibration_geometry_issues(
        [(100.0, 100.0), (110.0, 110.0)], image_width=1280, image_height=960
    ) == ["calibration_points_too_close"]
    assert "calibration_triangle_area_too_small" in calibration.calibration_geometry_issues(
        [(100.0, 100.0), (600.0, 100.0), (1100.0, 100.0)],
        image_width=1280,
        image_height=960,
    )
    assert not calibration.calibration_geometry_issues(
        [(100.0, 100.0), (1100.0, 100.0), (640.0, 850.0)],
        image_width=1280,
        image_height=960,
    )


def test_calibration_helper_import_does_not_eager_load_qt_numpy_or_opencv() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import oculidoc.vendor_helpers.calibrate_seveninvensun_glasses; "
                "print(','.join(name for name in ('PySide6','numpy','cv2') "
                "if name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.stdout.strip() == ""


def test_calibration_cli_has_no_password_argument() -> None:
    destinations = {action.dest for action in calibration._build_parser()._actions}

    assert "password" not in destinations


def test_live_probe_exposes_bound_bundle_not_naked_coefficient_paths() -> None:
    destinations = {action.dest for action in live_probe._build_parser()._actions}

    assert {"calibration_bundle", "profile_id"} <= destinations
    assert "left_coefficient" not in destinations
    assert "right_coefficient" not in destinations


def test_decoder_frame_dataclass_keeps_raw_decoder_result() -> None:
    frame = sdk.SeveninvensunSceneFrame(b"\x00\x00\x00", 1, 1, 2, time.perf_counter_ns(), 3, -1)

    assert frame.decoder_result_raw == -1
