from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic_ns

import pytest

from oculidoc.config import GazeDeviceConfig, GazeDeviceConfigStore, Settings
from oculidoc.devices import webcam_gaze as webcam
from oculidoc.devices.contracts import DeviceState
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.ui.device_settings import DeviceSettingsDialog

SCREEN = ("patient-screen", 1920, 1080)


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "providers.json"
    path.write_text(
        json.dumps(
            {
                "camera_index": 0,
                "providers": {name: {"python": sys.executable} for name in webcam.PROVIDERS},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_camera_selection_changes_runtime_config_and_calibration_identity(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    initial = webcam.calibration_identity(
        webcam.read_webcam_config(path), "acies", "participant-1", SCREEN
    )

    webcam.save_webcam_camera_index(path, 2)

    saved = webcam.read_webcam_config(path)
    assert saved["camera_index"] == 2
    assert saved["providers"]["acies"]["python"] == sys.executable
    assert webcam.calibration_identity(saved, "acies", "participant-1", SCREEN) != initial


def save_model(path: Path, provider: str = "acies", **overrides) -> dict:
    identity = webcam.calibration_identity(
        webcam.read_webcam_config(path), provider, "participant-1", SCREEN
    )
    model = {
        "mean": [0] * webcam.FEATURE_COUNT,
        "scale": [1] * webcam.FEATURE_COUNT,
        "weights": [[1, 0], [0, 1]] + [[0, 0]] * (webcam.FEATURE_COUNT - 1),
        "algorithm_version": webcam.ACIES_VERSION,
        "calibration_profile": "balanced",
        "identity": identity,
        "validated": True,
        "validation_error": 0.05,
        "validation_max_error": 0.1,
        "created_at": datetime.now(UTC).isoformat(),
        **overrides,
    }
    directory = path.parent / "calibration"
    directory.mkdir(exist_ok=True)
    (directory / f"{identity[:24]}.json").write_text(json.dumps(model), encoding="utf-8")
    return model


def packet(sequence=0, features=None):
    if isinstance(features, list) and len(features) == 2:
        features = features + [0.0] * (webcam.FEATURE_COUNT - 2)
    return {
        "sequence": sequence,
        "timestamp_ns": monotonic_ns(),
        "features": features,
        "confidence": 0.8 if features is not None else 0.0,
        "mode": "binocular" if features is not None else "unavailable",
        "left_eye_valid": features is not None,
        "right_eye_valid": features is not None,
        "algorithm_version": webcam.ACIES_VERSION,
        "preview": [0.5, 0.5],
        "screen_focus": "unknown",
        "eye_diagnostics": {},
    }


def test_calibration_generalizes_to_held_out_positions():
    targets = [(x, y) for y in (0.1, 0.5, 0.9) for x in (0.1, 0.5, 0.9) for _ in range(10)]
    model = webcam.fit_calibration([[x * 2 - 1, 3 * y + 1] for x, y in targets], targets)
    prediction = webcam.predict_calibration(model, [-0.5, 3.25])
    assert prediction == pytest.approx((0.25, 0.75), abs=0.01)
    with pytest.raises(ValueError, match="九个"):
        webcam.fit_calibration([[0, 0]] * 45, [(0.5, 0.5)] * 45)
    with pytest.raises(ValueError):
        webcam.predict_calibration(model, [float("nan"), 0])


@pytest.mark.parametrize(
    "overrides",
    [
        {"validated": False},
        {"validation_error": 0.9},
        {"validation_max_error": 0.8},
        {"validation_error": float("nan")},
        {"scale": [0, 1]},
        {"created_at": (datetime.now(UTC) - timedelta(hours=9)).isoformat()},
    ],
)
def test_invalid_or_expired_calibration_cannot_open_camera(tmp_path, monkeypatch, overrides):
    path = config_file(tmp_path)
    save_model(path, **overrides)
    monkeypatch.setattr(
        webcam.WebcamFeatureProcess, "start", lambda _: pytest.fail("opened camera")
    )
    device = webcam.WebcamEyeTrackerDevice(path, "acies", tmp_path, "participant-1", SCREEN)
    with pytest.raises(DeviceConnectionError, match="重新校准"):
        device.connect()


@pytest.mark.parametrize(
    "patient,screen",
    [
        ("participant-2", SCREEN),
        ("participant-1", ("other", 1920, 1080)),
        ("participant-1", ("patient-screen", 1280, 720)),
    ],
)
def test_calibration_is_bound_to_participant_and_screen(tmp_path, patient, screen):
    path = config_file(tmp_path)
    save_model(path)
    device = webcam.WebcamEyeTrackerDevice(path, "acies", tmp_path, patient, screen)
    with pytest.raises(DeviceConnectionError, match="重新校准"):
        device.connect()


def test_blinks_and_out_of_screen_predictions_are_invalid_not_clamped(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    save_model(path)
    monkeypatch.setattr(webcam.WebcamFeatureProcess, "start", lambda _: None)
    device = webcam.WebcamEyeTrackerDevice(path, "acies", tmp_path, "participant-1", SCREEN)
    device.connect()
    device.start_stream()
    for index, (features, valid) in enumerate(
        [(None, False), ([1.2, 0.5], False), ([0.25, 0.75], True)]
    ):
        device._process._queue.put_nowait(packet(index, features))
        sample = device.read_sample()
        assert sample.gaze_valid is valid
        assert sample.gaze_x_normalized == (0.25 if valid else None)
        assert sample.left_pupil_diameter_mm is None
        assert sample.eye_position_available is False
    device.disconnect()
    assert device.state is DeviceState.DISCONNECTED


def test_acies_is_the_only_camera_engine_and_requires_five_valid_frames(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    save_model(path)
    calls = []
    monkeypatch.setattr(
        webcam.WebcamFeatureProcess, "start", lambda self: calls.append(self.provider)
    )
    counter = iter(range(100))
    monkeypatch.setattr(
        webcam.WebcamFeatureProcess, "read", lambda self: packet(next(counter), [0.5, 0.5])
    )
    device = webcam.create_webcam_tracker(path, tmp_path, "participant-1", SCREEN)
    device.connect()
    assert calls == ["acies"]
    assert next(counter) == 5
    device.start_stream()
    assert device.read_sample().gaze_valid
    assert any("标准" in note for note in device.capability_diagnostics())
    device.stop_stream()
    device.start_stream()
    assert device.read_sample().gaze_valid
    device.disconnect()


def test_cancellation_during_probe_does_not_open_next_camera(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    save_model(path)
    started = threading.Event()
    monkeypatch.setattr(webcam.WebcamFeatureProcess, "start", lambda _: started.set())
    device = webcam.create_webcam_tracker(path, tmp_path, "participant-1", SCREEN)
    errors = []

    def connect():
        try:
            device.connect()
        except DeviceConnectionError as error:
            errors.append(str(error))

    thread = threading.Thread(target=connect)
    thread.start()
    assert started.wait(1)
    device.interrupt()
    thread.join(2)
    assert not thread.is_alive()
    assert errors and "取消" in errors[0]


@pytest.mark.parametrize("features", [[float("inf")], [[1, 2]], [], [0] * 2049])
def test_process_rejects_malformed_features(tmp_path, features):
    process = webcam.WebcamFeatureProcess(config_file(tmp_path), "acies", tmp_path)
    process._queue.put_nowait(packet(features=features))
    with pytest.raises(DeviceConnectionError, match="特征格式"):
        process.read()


def test_process_rejects_stale_and_repeated_packets(tmp_path):
    process = webcam.WebcamFeatureProcess(config_file(tmp_path), "acies", tmp_path)
    first = packet(1, [0.5, 0.5])
    process._queue.put_nowait(first)
    process.read()
    process._queue.put_nowait(first)
    with pytest.raises(DeviceConnectionError, match="重复"):
        process.read()
    stale = packet(2, [0.5, 0.5])
    stale["timestamp_ns"] -= 2_000_000_000
    process._queue.put_nowait(stale)
    with pytest.raises(DeviceConnectionError, match="过期"):
        process.read()


def test_actual_subprocess_and_camera_lease_are_released(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    real_popen = webcam.subprocess.Popen
    payload = packet(0, [0.3, 0.4])
    code = (
        "import time,json; p=json.loads(" + repr(json.dumps(payload)) + ");"
        "p['timestamp_ns']=time.monotonic_ns(); print(json.dumps(p),flush=True); time.sleep(20)"
    )
    monkeypatch.setattr(
        webcam.subprocess,
        "Popen",
        lambda args, **kw: real_popen([sys.executable, "-u", "-c", code], **kw),
    )
    first = webcam.WebcamFeatureProcess(path, "acies", tmp_path)
    second = webcam.WebcamFeatureProcess(path, "acies", tmp_path)
    first.start()
    try:
        with pytest.raises(DeviceConnectionError, match="占用"):
            second.start()
        for _ in range(10):
            try:
                assert first.read()["features"][:2] == [0.3, 0.4]
                break
            except TimeoutError:
                pass
        else:
            pytest.fail("child produced no packet")
    finally:
        child = first.process
        first.close()
    assert child.poll() is not None
    second.start()
    second.close()


def test_new_install_defaults_to_real_sources_and_saved_mock_is_preserved(monkeypatch, tmp_path):
    monkeypatch.delenv("OCULIDOC_GAZE_SOURCE")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.gaze_source == "auto_webcam"
    config = GazeDeviceConfig.from_settings(settings).model_copy(update={"gaze_source": "mock"})
    assert config.apply(settings).gaze_source == "mock"


def test_webcam_ui_persists_configuration_without_enabling_tobii_calibration(qtbot, tmp_path):
    settings = Settings(data_dir=tmp_path, gaze_source="webcam")
    store = GazeDeviceConfigStore.for_settings(settings)
    dialog = DeviceSettingsDialog(settings, store)
    qtbot.addWidget(dialog)
    dialog.webcam_config_edit.setText(str(tmp_path / "providers.json"))
    assert dialog.webcam_calibrate_button.isEnabled()
    assert not dialog.open_tobii_button.isEnabled()
    dialog.webcam_profile_combo.setCurrentIndex(dialog.webcam_profile_combo.findData("tolerant"))
    dialog._save()
    saved = store.load(GazeDeviceConfig.from_settings(settings))
    assert saved.webcam_config_path == tmp_path / "providers.json"
    assert saved.gaze_source == "webcam"
    assert saved.webcam_calibration_profile == "tolerant"
    assert saved.apply(settings).webcam_calibration_profile == "tolerant"


def test_report_labels_camera_estimate_and_preserves_legacy_records():
    from oculidoc.application.gaze_report import _task_result_rows

    rows = _task_result_rows(
        {
            "result": {
                "gaze_source": {
                    "device_name": "ACIES",
                    "webcam_estimate": True,
                }
            }
        }
    )
    assert ("眼动数据来源", "ACIES") in rows
    assert any(label == "摄像头估计边界" for label, _ in rows)
    assert not any(label == "摄像头估计边界" for label, _ in _task_result_rows({}))


def test_missing_providers_fail_explicitly_without_simulation(tmp_path):
    path = config_file(tmp_path)
    tracker = webcam.create_webcam_tracker(path, tmp_path, "participant-1", SCREEN)
    with pytest.raises(DeviceConnectionError, match="未使用其他算法或模拟数据") as failure:
        tracker.connect()
    assert all(name in str(failure.value) for name in webcam.PROVIDER_LABELS.values())
    assert tracker.active_device is None


def test_failed_process_cleanup_prevents_next_candidate(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    save_model(path)
    calls = []
    monkeypatch.setattr(
        webcam.WebcamFeatureProcess, "start", lambda self: calls.append(self.provider)
    )

    def fail_read(self):
        raise DeviceConnectionError("camera unavailable")

    def fail_close(self):
        raise RuntimeError("camera process did not exit")

    monkeypatch.setattr(webcam.WebcamFeatureProcess, "read", fail_read)
    monkeypatch.setattr(webcam.WebcamFeatureProcess, "close", fail_close)
    tracker = webcam.create_webcam_tracker(path, tmp_path, "participant-1", SCREEN)
    with pytest.raises(RuntimeError, match="did not exit"):
        tracker.connect()
    assert calls == ["acies"]


def test_task_record_retains_camera_provenance(qtbot, tmp_path):
    from PySide6.QtWidgets import QWidget

    from oculidoc.experiments.task_runtime import RecordedTaskRuntime

    task = QWidget()
    qtbot.addWidget(task)
    runtime = RecordedTaskRuntime(
        task=task, sample_sink=lambda _: None, session_directory=tmp_path, patient_id="Beta00"
    )
    runtime.gaze_source_metadata = {"device_name": "ACIES", "webcam_estimate": True}
    runtime.finish("completed")
    result = json.loads((runtime.run_directory / "task_result.json").read_text(encoding="utf-8"))
    assert result["result"]["gaze_source"]["webcam_estimate"] is True
