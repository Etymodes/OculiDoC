"""Algorithm/quality tests use artificial eyes, not evidence of real-world accuracy."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from time import monotonic, monotonic_ns

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from oculidoc.config import Settings
from oculidoc.devices.acies import (
    ACIES_VERSION,
    FEATURE_COUNT,
    GazeSmoother,
    extract_observation,
    fit_mapping,
    validation_passes,
)
from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.webcam_gaze import (
    WebcamEyeTrackerDevice,
    WebcamFeatureProcess,
    predict_calibration,
)
from oculidoc.ui import webcam_calibration as ui
from test_webcam_gaze import SCREEN, config_file, packet, save_model


def artificial_eyes(*, aperture=10.0, eye_width=40.0, dx=0.0, dy=0.0):
    gray = np.full((240, 320), 180, dtype=np.uint8)
    points = np.zeros((478, 3))
    yy, xx = np.mgrid[:240, :320]
    for center, corners, lids, iris in (
        (100, (33, 133), (159, 145), 468),
        (210, (362, 263), (386, 374), 473),
    ):
        points[corners[0], :2] = (center - eye_width / 2, 90)
        points[corners[1], :2] = (center + eye_width / 2, 90)
        points[lids[0], :2] = (center, 90 - aperture / 2)
        points[lids[1], :2] = (center, 90 + aperture / 2)
        points[iris, :2] = (center + dx, 90 + dy)
        mask = (xx - center - dx) ** 2 + (yy - 90 - dy) ** 2 <= (eye_width * 0.16) ** 2
        gray[mask] = 35
    points[1, :2] = (155, 120)
    return gray, points / [320, 240, 320]


def test_binocular_features_respond_to_real_iris_motion_and_ignore_target():
    centered = extract_observation(*artificial_eyes())
    moved = extract_observation(*artificial_eyes(dx=4, dy=1))
    assert centered["mode"] == moved["mode"] == "binocular"
    assert len(moved["features"]) == FEATURE_COUNT
    assert moved["features"][0] > centered["features"][0] + 0.06
    assert moved["features"][1] > centered["features"][1]
    assert moved["algorithm_version"] == ACIES_VERSION


def test_narrow_eyelids_are_usable_with_lower_quality():
    normal = extract_observation(*artificial_eyes())
    narrow = extract_observation(*artificial_eyes(aperture=2.0))
    assert narrow["features"] is not None
    assert 0.18 <= narrow["confidence"] < normal["confidence"]


def test_one_closed_eye_uses_only_the_visible_eye():
    gray, points = artificial_eyes()
    points[159, 1] = points[145, 1] = 90 / 240
    observation = extract_observation(gray, points)
    assert observation["features"] is not None
    assert observation["mode"] == "left_eye"
    assert observation["left_eye_valid"] and not observation["right_eye_valid"]
    assert observation["confidence"] < extract_observation(*artificial_eyes())["confidence"]


@pytest.mark.parametrize("cause", ["closed", "uniform", "glare", "missing", "nan", "tiny"])
def test_unobservable_eyes_never_become_a_center_point(cause):
    gray, points = artificial_eyes(
        aperture=0.8 if cause == "closed" else 10, eye_width=4 if cause == "tiny" else 40
    )
    if cause == "uniform":
        gray[:] = 100
    elif cause == "glare":
        gray[:] = 255
    elif cause == "missing":
        points = []
    elif cause == "nan":
        points[468, 0] = float("nan")
    observation = extract_observation(gray, points)
    assert observation["features"] is None
    assert observation["preview"] is None
    assert observation["mode"] == "unavailable"


def test_partial_glasses_glint_does_not_discard_both_eyes():
    gray, points = artificial_eyes()
    gray[87:90, 98:101] = 255
    result = extract_observation(gray, points)
    assert result["features"] is not None
    assert np.isfinite(result["features"]).all()


def test_lower_resolution_reduces_quality_but_preserves_observed_motion():
    regular = extract_observation(*artificial_eyes())
    small = extract_observation(*artificial_eyes(eye_width=10, aperture=3, dx=1))
    assert small["features"] is not None
    assert small["confidence"] < regular["confidence"]
    assert small["features"][0] > regular["features"][0]


@pytest.mark.parametrize(
    "profile,mean,maximum,passed",
    [
        ("strict", 0.171, 0.238, False),
        ("balanced", 0.171, 0.238, True),
        ("balanced", 0.22, 0.40, False),
        ("tolerant", 0.22, 0.40, True),
        ("tolerant", 0.251, 0.40, False),
        ("tolerant", 0.22, 0.451, False),
        ("balanced", float("nan"), 0.1, False),
        ("balanced", -0.1, 0.1, False),
    ],
)
def test_tolerance_is_explicit_and_bounded(profile, mean, maximum, passed):
    assert validation_passes(mean, maximum, profile) is passed


def test_robust_calibration_balances_locations_and_rejects_static_features():
    rows, targets = [], []
    for i, (x, y) in enumerate(ui.TRAIN_POINTS):
        count = 10 if i else 200
        for j in range(count):
            rows.append([2 * x - 1, 3 * y + 1] if j > 1 else [90, -90])
            targets.append((x, y))
    model = fit_mapping(rows, targets)
    assert predict_calibration(model, [-0.5, 3.25]) == pytest.approx((0.25, 0.75), abs=0.015)
    with pytest.raises(ValueError, match="可区分"):
        fit_mapping([[0, 0]] * (len(ui.TRAIN_POINTS) * 10), list(ui.TRAIN_POINTS) * 10)


def test_smoother_resets_after_missing_or_stale_data():
    smoother = GazeSmoother()
    assert smoother.update((0.2, 0.2), 1_000_000_000, 0.8) == (0.2, 0.2)
    smoothed = smoother.update((0.21, 0.21), 1_030_000_000, 0.2)
    assert 0.2 < smoothed[0] < 0.21
    assert smoother.update((0.8, 0.8), 2_000_000_000, 0.8) == (0.8, 0.8)
    smoother.reset()
    assert smoother.point is None
    assert smoother.update((0.1, 0.1), 2_100_000_000, 0.8) == (0.1, 0.1)


def test_accuracy_profile_grades_saved_calibration_without_discarding_it(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    save_model(
        path, validation_error=0.22, validation_max_error=0.40, calibration_profile="tolerant"
    )
    monkeypatch.setattr("oculidoc.devices.webcam_gaze.WebcamFeatureProcess.start", lambda _: None)
    strict = WebcamEyeTrackerDevice(path, "acies", tmp_path, "participant-1", SCREEN, "balanced")
    strict.connect()
    assert strict._model["validated"] is True
    assert strict._model["meets_current_profile"] is False
    strict.disconnect()
    tolerant = WebcamEyeTrackerDevice(path, "acies", tmp_path, "participant-1", SCREEN, "tolerant")
    tolerant.connect()
    assert tolerant._model["validated"] is True
    assert tolerant._model["meets_current_profile"] is True
    tolerant.disconnect()


def test_monocular_source_and_quality_are_preserved_in_samples(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    save_model(path)
    monkeypatch.setattr("oculidoc.devices.webcam_gaze.WebcamFeatureProcess.start", lambda _: None)
    device = WebcamEyeTrackerDevice(path, "acies", tmp_path, "participant-1", SCREEN)
    device.connect()
    device.start_stream()
    data = packet(0, [0.3, 0.4])
    data.update(mode="left_eye", right_eye_valid=False, confidence=0.3)
    device._process._queue.put_nowait(data)
    sample = device.read_sample()
    assert sample.gaze_valid and sample.left_eye_valid and not sample.right_eye_valid
    assert sample.gaze_estimation_mode == "left_eye" and sample.gaze_confidence == 0.3
    device.disconnect()


def test_reticle_never_uses_current_training_target_or_survives_signal_loss(qtbot, tmp_path):
    dialog = ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    data = packet(0, [0.1, 0.2])
    data["preview"] = [0.23, 0.64]
    dialog._collect(data)
    assert dialog.visible_reticle() == (0.23, 0.64)
    dialog._index = 4
    assert dialog.visible_reticle() == (0.23, 0.64)
    dialog._index = len(ui.TRAIN_POINTS)
    assert dialog.visible_reticle() is None  # No visual feedback contaminates holdout.
    dialog._index = 0
    dialog._collect(packet(1))
    assert dialog.visible_reticle() is None
    dialog._collect(data)
    dialog._last_packet = monotonic() - 0.5
    assert dialog.visible_reticle() is None


def test_calibration_does_not_collect_until_space_and_ignores_queued_old_frames(qtbot, tmp_path):
    dialog = ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    dialog._identity = "test"
    dialog._collect(packet(0, [0.1, 0.2]))
    assert dialog._point_samples == []
    dialog._begin()
    assert dialog._armed
    dialog._started = monotonic() - 2
    stale = packet(1, [0.1, 0.2])
    stale["timestamp_ns"] -= 3_000_000_000
    dialog._collect(stale)
    assert dialog._point_samples == []
    dialog._collect(packet(2, [0.1, 0.2]))
    assert len(dialog._point_samples) == 1


def test_independent_validation_cannot_refit_mapping(qtbot, tmp_path, monkeypatch):
    dialog = ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    path = config_file(tmp_path)
    dialog._model = save_model(path)
    dialog._identity = "holdout"
    original = json.dumps(dialog._model)
    dialog._index = len(ui.TRAIN_POINTS)
    dialog._armed = True
    dialog._started = monotonic() - 3
    dialog._point_samples = [packet(features=[0.25, 0.25])["features"]] * 6
    dialog._point_qualities = [0.8] * 6
    monkeypatch.setattr(ui, "fit_calibration", lambda *a, **kw: pytest.fail("holdout refitted"))
    dialog._advance()
    assert json.dumps(dialog._model) == original
    assert len(dialog._validation_points) == 1
    assert dialog._features == []


@pytest.mark.parametrize("mean,maximum,accepted", [(0.171, 0.238, True), (0.22, 0.40, False)])
def test_ui_saves_actual_metrics_for_pass_and_failure(
    qtbot, tmp_path, monkeypatch, mean, maximum, accepted
):
    dialog = ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    screen = QApplication.primaryScreen()
    dialog._screen = (screen.name(), screen.geometry().width(), screen.geometry().height())
    dialog._identity = "test-identity"
    dialog._model = save_model(config_file(tmp_path))
    dialog._validation_points = [
        {
            "sample_count": 5,
            "mean_error": mean,
            "horizontal_mae": mean / 2,
            "vertical_mae": mean / 2,
            "mean_prediction": list(target),
        }
        for target in ui.VALIDATION_POINTS
    ]
    dialog._errors = [mean, maximum]
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: None)
    dialog._finish_calibration()
    root = tmp_path / "runtime" / "webcam"
    attempt = json.loads(
        next((root / "calibration_attempts").glob("*.json")).read_text(encoding="utf-8")
    )
    assert attempt["validation_error"] == pytest.approx(mean)
    assert attempt["validated"] is accepted
    assert attempt["validation_cursor_visible"] is False
    saved = json.loads((root / "calibration" / "test-identity.json").read_text(encoding="utf-8"))
    assert saved["reusable"] is True
    assert saved["validated"] is accepted
    assert attempt["status"] == "completed"
    assert dialog._reviewing
    assert not dialog._closed
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert "features" not in attempt  # No images or biometric feature rows in attempts.
    candidate = json.loads(
        (root / "calibration_candidates" / attempt["candidate_file"]).read_text(encoding="utf-8")
    )
    assert candidate["task_eligible"] is False
    assert candidate["validation_metrics"]["validated"] is accepted


def test_vertical_iris_motion_survives_lids_moving_with_half_open_eyes():
    results = []
    for dy in (-2, 0, 2):
        gray, points = artificial_eyes(aperture=2, dy=dy)
        points[[159, 145, 386, 374], 1] += dy / 240
        results.append(extract_observation(gray, points))
    assert all(result["mode"] == "binocular" for result in results)
    vertical = [result["features"][1] for result in results]
    assert vertical == pytest.approx([-0.05, 0, 0.05], abs=1e-5)
    assert [result["features"][11] for result in results] == pytest.approx(vertical)


def test_small_vertical_motion_maps_to_full_range_without_following_head_drift():
    rng = np.random.default_rng(71)
    rows, targets = [], []
    for x, y in ui.TRAIN_POINTS:
        for _ in range(20):
            u, v = (0.5 - x) * 0.15, (y - 0.5) * 0.009
            row = [u, v, u * u, v * v, u * v, *rng.normal(0, 0.01, 6), v, 0.06] + [0] * 6
            rows.append(row)
            targets.append((x, y))
    model = fit_mapping(rows, targets)
    predictions = []
    for x, y in ui.VALIDATION_POINTS:
        u, v = (0.5 - x) * 0.15, (y - 0.5) * 0.009
        row = [u, v, u * u, v * v, u * v, *rng.normal(0, 0.03, 6), v, 0.06] + [0] * 6
        predictions.append(predict_calibration(model, row))
    assert np.max(np.abs(np.array(predictions) - ui.VALIDATION_POINTS)) < 0.015
    assert model["axis_models"]["vertical"]["basis"] == "iris"
    assert np.ptp(np.array(predictions)[:, 1]) > 0.65


def test_preparation_reuses_valid_mapping_but_not_old_version(qtbot, tmp_path):
    path = config_file(tmp_path)
    model = save_model(path)
    # Match the data directory layout used by the dialog.
    runtime = tmp_path / "runtime" / "webcam" / "calibration"
    runtime.mkdir(parents=True)
    saved = runtime / f"{model['identity'][:24]}.json"
    saved.write_text(json.dumps(model))
    dialog = ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    dialog._identity = model["identity"]
    dialog._load_preview()
    data = packet(features=[0.2, 0.8])
    data["preview"] = [0.5, 0.5]
    dialog._collect(data)
    assert dialog.visible_reticle() == pytest.approx((0.2, 0.8))
    model["algorithm_version"] = "0.1.0"
    saved.write_text(json.dumps(model))
    dialog._load_preview()
    assert not dialog._preview_model and not dialog._saved_preview


def test_cancel_and_capture_failure_leave_single_attempt_record(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: None)
    for failed in (False, True):
        dialog = ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
        qtbot.addWidget(dialog)
        dialog._identity = "failed" if failed else "cancelled"
        dialog._collect(packet(features=[0.2, 0.3]))
        dialog._begin()
        path = dialog._attempt_path
        assert json.loads(path.read_text())["status"] == "in_progress"
        if failed:
            dialog._fail("camera disconnected")
        else:
            dialog.reject()
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["status"] == ("failed" if failed else "cancelled")
        assert record["validated"] is False
        assert "features" not in record
    assert len(list(path.parent.glob("*.json"))) == 2


def test_outside_saved_mapping_does_not_prevent_recalibration(qtbot, tmp_path):
    dialog = ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    dialog._identity = "outside"
    dialog._preview_model = save_model(config_file(tmp_path))
    dialog._collect(packet(features=[1.2, 0.5]))
    assert dialog.visible_reticle() is None
    dialog._begin()
    assert dialog._armed
    dialog.reject()


def test_validation_records_vertical_error_and_mean_position(qtbot, tmp_path):
    dialog = ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    dialog._identity = "axis-error"
    dialog._model = save_model(config_file(tmp_path))
    x, y = ui.VALIDATION_POINTS[0]
    dialog._index, dialog._armed = len(ui.TRAIN_POINTS), True
    dialog._started = monotonic() - 3
    dialog._point_samples = [packet(features=[x + 0.01, y + 0.2])["features"]] * 6
    dialog._point_qualities = [0.8] * 6
    dialog._advance()
    result = dialog._validation_points[0]
    assert result["horizontal_mae"] == pytest.approx(0.01)
    assert result["vertical_mae"] == pytest.approx(0.2)
    assert result["mean_prediction"] == pytest.approx([x + 0.01, y + 0.2])
    dialog.reject()


def test_quality_is_optional_for_hardware_but_validated_if_present():
    timestamp = DeviceTimestamp(0, monotonic_ns(), datetime.now(UTC))
    sample = EyeTrackerSample(timestamp, 0.5, 0.5, True, True)
    assert sample.gaze_confidence is None
    with pytest.raises(ValueError, match="gaze_confidence"):
        EyeTrackerSample(timestamp, 0.5, 0.5, True, True, gaze_confidence=float("nan"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"confidence": None},
        {"confidence": float("nan")},
        {"confidence": True},
        {"mode": "left_eye"},
        {"algorithm_version": "old"},
        {"preview": [float("nan"), 0.5]},
        {"features": [0.0] * 10},
        {"screen_focus": "focused"},
        {"eye_diagnostics": {"left": {"glare_ratio": float("nan")}}},
    ],
)
def test_invalid_quality_or_provenance_packets_are_rejected(tmp_path, overrides):
    process = WebcamFeatureProcess(config_file(tmp_path), "acies", tmp_path)
    data = packet(0, [0.3, 0.4])
    data.update(overrides)
    process._queue.put_nowait(data)
    with pytest.raises(DeviceConnectionError, match="ACIES"):
        process.read()


def test_recording_preserves_monocular_quality_in_parquet(tmp_path):
    import pyarrow.parquet as pq

    from oculidoc.experiments.recording import ScreenContext, TaskRunRecorder

    recorder = TaskRunRecorder(
        session_directory=tmp_path,
        patient_id="Beta00",
        session_id="quality-test",
        task_kind="binary_horizontal",
        task_config={},
        screen_context=ScreenContext(1920, 1080),
        task_started_monotonic_ns=1_000_000_000,
    )
    sample = EyeTrackerSample(
        DeviceTimestamp(0, 1_100_000_000, datetime.now(UTC)),
        0.3,
        0.4,
        True,
        False,
        gaze_confidence=0.31,
        gaze_estimation_mode="left_eye",
        gaze_observation_status="monocular",
        screen_focus_status="unknown",
    )
    recorder.record_sample(sample)
    recorder.finish(reason="completed")
    row = pq.read_table(recorder.gaze_events_path).to_pylist()[0]
    assert row["gaze_confidence"] == 0.31
    assert row["gaze_estimation_mode"] == "left_eye"
    assert row["gaze_observation_status"] == "monocular"
    assert row["screen_focus_status"] == "unknown"
    assert row["left_eye_valid"] and not row["right_eye_valid"]
