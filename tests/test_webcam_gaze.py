from __future__ import annotations

import hashlib
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic_ns

import pytest

from oculidoc.config import GazeDeviceConfig, GazeDeviceConfigStore, Settings
from oculidoc.devices import webcam_gaze as webcam
from oculidoc.devices.acies_experiments import load_experiment_model, load_paired_candidate
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


def test_runtime_repair_keeps_existing_calibration_and_uses_new_provider(tmp_path: Path):
    path = config_file(tmp_path)
    original = webcam.read_webcam_config(path)
    identity = webcam.calibration_identity(original, "acies", "participant-1", SCREEN)
    save_model(path)
    repaired = dict(original)
    repaired[webcam.CALIBRATION_IDENTITY_PROVIDERS] = original["providers"]
    repaired["providers"] = {"acies": {"python": "C:/new/python.exe", "face_model": "new.task"}}
    path.write_text(json.dumps(repaired), encoding="utf-8")

    loaded = webcam.read_webcam_config(path)
    assert loaded["providers"]["acies"]["python"] == "C:/new/python.exe"
    assert webcam.calibration_identity(loaded, "acies", "participant-1", SCREEN) == identity
    assert webcam.load_calibration(path.parent, identity, "balanced")["identity"] == identity
    for altered in (
        {**loaded, "camera_index": 2},
        {**loaded, "camera_width": 640},
    ):
        assert webcam.calibration_identity(altered, "acies", "participant-1", SCREEN) != identity
    assert webcam.calibration_identity(loaded, "acies", "participant-2", SCREEN) != identity
    assert (
        webcam.calibration_identity(loaded, "acies", "participant-1", ("other", 1920, 1080))
        != identity
    )


@pytest.mark.parametrize("basis", [None, [], {}, {"acies": None}, {"acies": "old"}])
def test_invalid_identity_provider_snapshot_fails_explicitly(tmp_path: Path, basis):
    path = config_file(tmp_path)
    config = webcam.read_webcam_config(path)
    config[webcam.CALIBRATION_IDENTITY_PROVIDERS] = basis
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(DeviceConnectionError, match="calibration_identity_providers"):
        webcam.read_webcam_config(path)


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
        {"validated": False, "reusable": True, "validation_error": None},
        {"validated": False, "reusable": True, "validation_max_error": float("inf")},
        {"validated": False, "reusable": True, "validation_error": -0.1},
        {"validated": 1, "reusable": True},
        {"validated": False, "reusable": 1},
        {"validated": False, "reusable": True, "calibration_profile": "unknown"},
        {"scale": [0, 1]},
        {"created_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        {"created_at": "2026-01-01T12:00:00"},
        {"feature_version": "incompatible"},
        {"identity": "another-person"},
    ],
)
def test_incomplete_or_incompatible_calibration_cannot_open_camera(
    tmp_path, monkeypatch, overrides
):
    path = config_file(tmp_path)
    save_model(path, **overrides)
    monkeypatch.setattr(
        webcam.WebcamFeatureProcess, "start", lambda _: pytest.fail("opened camera")
    )
    device = webcam.WebcamEyeTrackerDevice(path, "acies", tmp_path, "participant-1", SCREEN)
    with pytest.raises(DeviceConnectionError, match="重新校准"):
        device.connect()


def test_saved_calibration_survives_restart_days_later_without_rewriting_file(
    tmp_path, monkeypatch
):
    path = config_file(tmp_path)
    model = save_model(path, created_at=(datetime.now(UTC) - timedelta(days=9)).isoformat())
    saved = tmp_path / "calibration" / f"{model['identity'][:24]}.json"
    before = saved.read_bytes()
    monkeypatch.setattr(webcam.WebcamFeatureProcess, "start", lambda _: None)
    device = webcam.WebcamEyeTrackerDevice(path, "acies", tmp_path, "participant-1", SCREEN)
    device.connect()
    assert device._model["validated"] is True
    assert device._model["meets_current_profile"] is True
    assert device._model["calibration_age_hours"] == pytest.approx(216, abs=0.01)
    assert webcam.calibration_snapshot(device._model) == model
    assert any("跨天" in note for note in device.capability_diagnostics())
    assert saved.read_bytes() == before
    device.disconnect()


def test_completed_low_accuracy_mapping_is_reusable_without_fabricating_pass(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    model = save_model(
        path, validated=False, reusable=True, validation_error=0.32, validation_max_error=0.9
    )
    monkeypatch.setattr(webcam.WebcamFeatureProcess, "start", lambda _: None)
    device = webcam.WebcamEyeTrackerDevice(path, "acies", tmp_path, "participant-1", SCREEN)
    device.connect()
    assert device._model["validated"] is False
    assert device._model["meets_current_profile"] is False
    assert device._model["validation_error"] == model["validation_error"]
    assert any("精度待改善" in note for note in device.capability_diagnostics())
    device.start_stream()
    device._process._queue.put_nowait(packet(0, [0.3, 0.4]))
    assert device.read_sample().gaze_valid
    device.disconnect()
    experiment = load_experiment_model(tmp_path, model["identity"], "balanced")
    assert experiment["experiment_source"] == "saved_reusable"
    assert experiment["validated"] is False


def test_interrupted_legacy_calibration_recovers_previous_only_when_active_is_missing(tmp_path):
    model = save_model(
        config_file(tmp_path), created_at=(datetime.now(UTC) - timedelta(days=2)).isoformat()
    )
    saved = tmp_path / "calibration" / f"{model['identity'][:24]}.json"
    previous = saved.with_name(f"{model['identity'][:24]}.previous.json")
    saved.rename(previous)
    before = previous.read_bytes()
    assert load_experiment_model(tmp_path, model["identity"], "balanced")["validated"] is True
    assert previous.read_bytes() == before and not saved.exists()
    saved.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(DeviceConnectionError):
        webcam.load_calibration(tmp_path, model["identity"], "balanced")


def test_reusable_eye_can_estimate_monocular_without_changing_validation_truth(tmp_path):
    path = config_file(tmp_path)
    child = save_model(
        path, validated=False, reusable=True, validation_error=0.32, validation_max_error=0.9
    )
    model = save_model(path, **child, eye_models={"left": child.copy()})
    loaded = webcam.load_calibration(tmp_path, model["identity"], "balanced")
    data = packet(0, [0.3, 0.4])
    data.update(right_eye_valid=False, eye_features={"left": data["features"], "right": None})
    assert webcam.predict_packet(loaded, data) == pytest.approx((0.3, 0.4))
    assert loaded["eye_models"]["left"]["validated"] is False
    assert loaded["eye_models"]["left"]["meets_current_profile"] is False
    loaded["eye_models"]["left"]["reusable"] = False
    assert webcam.predict_packet(loaded, data) is None
    data["eye_features"]["left"] = None
    assert webcam.predict_packet(loaded, data) is None


def test_stricter_accuracy_target_preserves_original_per_eye_validation(tmp_path):
    path = config_file(tmp_path)
    child = save_model(
        path, calibration_profile="tolerant", validation_error=0.22, validation_max_error=0.4
    )
    model = save_model(path, **child, eye_models={"left": child.copy()})
    loaded = webcam.load_calibration(tmp_path, model["identity"], "strict")
    assert loaded["validated"] is True and loaded["meets_current_profile"] is False
    assert loaded["eye_models"]["left"]["validated"] is True
    assert loaded["eye_models"]["left"]["meets_current_profile"] is False
    assert webcam.calibration_snapshot(loaded) == model


@pytest.mark.parametrize(
    "overrides",
    [
        {"validation_error": None, "validation_max_error": None},
        {"validation_error": float("nan")},
        {"validation_error": -0.1},
        {"validation_error": 0.9},
    ],
)
def test_reusable_eye_still_requires_sound_validation_metrics(tmp_path, overrides):
    path = config_file(tmp_path)
    child = save_model(path, validated=False, reusable=True, **overrides)
    model = save_model(path, eye_models={"left": child})
    with pytest.raises(DeviceConnectionError):
        webcam.load_calibration(tmp_path, model["identity"], "balanced")


def test_incomplete_eye_holdout_is_retained_without_authorizing_monocular_reuse(tmp_path):
    path = config_file(tmp_path)
    child = save_model(
        path, validated=False, reusable=False, validation_error=None, validation_max_error=None
    )
    model = save_model(path, eye_models={"left": child})
    loaded = webcam.load_calibration(tmp_path, model["identity"], "balanced")
    assert loaded["eye_models"]["left"]["meets_current_profile"] is False
    data = packet(0, [0.3, 0.4])
    data.update(right_eye_valid=False, eye_features={"left": data["features"], "right": None})
    assert webcam.predict_packet(loaded, data) is None


def test_loading_diagnostics_do_not_change_experiment_or_paired_baseline_hash(tmp_path):
    path = config_file(tmp_path)
    original = save_model(path)
    baseline = {**original, "experiment_source": "validated"}
    directory = tmp_path / "experiment_candidates"
    directory.mkdir()
    staged = {
        "name": "control",
        "baseline_sha256": hashlib.sha256(
            json.dumps(baseline, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
        "model": {
            **original,
            "feature_version": webcam.CALIBRATION_FEATURE_VERSION,
            "validated": False,
            "task_eligible": False,
        },
    }
    (directory / f"{original['identity'][:24]}.json").write_text(
        json.dumps(staged), encoding="utf-8"
    )
    first = load_experiment_model(tmp_path, original["identity"], "balanced")
    second = load_experiment_model(tmp_path, original["identity"], "strict")
    assert first == second == baseline
    assert load_paired_candidate(tmp_path, second)["name"] == "control"
    dynamic = {**first, "calibration_age_hours": 100.0, "meets_current_profile": False}
    assert load_paired_candidate(tmp_path, dynamic)["name"] == "control"


def test_older_failed_candidate_remains_diagnostic_and_does_not_replace_saved_calibration(tmp_path):
    model = save_model(
        config_file(tmp_path),
        validated=False,
        created_at=(datetime.now(UTC) - timedelta(days=9)).isoformat(),
    )
    directory = tmp_path / "calibration_candidates"
    directory.mkdir()
    candidate = directory / f"{model['identity'][:24]}-old.json"
    candidate.write_text(
        json.dumps({"model": model, "validation_metrics": model}), encoding="utf-8"
    )
    loaded = load_experiment_model(tmp_path, model["identity"], "balanced")
    assert loaded["experiment_source"] == "candidate" and loaded["task_eligible"] is False
    with pytest.raises(DeviceConnectionError):
        webcam.load_calibration(tmp_path, model["identity"], "balanced")


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
