"""Synthetic mechanism checks; do not substitute these for participant validation."""

import numpy as np
import pytest

from oculidoc.devices.acies import (
    FEATURE_COUNT,
    ObservationMonitor,
    extract_observation,
    face_pose,
    fit_mapping,
    validate_observation_model,
)
from oculidoc.devices.webcam_gaze import predict_calibration
from oculidoc.ui import webcam_calibration as ui
from test_acies import artificial_eyes
from test_webcam_gaze import packet


def pose_row(x, y, yaw, pitch):
    row = np.zeros(FEATURE_COUNT)
    row[0] = (x - 0.5) * 0.16 - yaw * 0.10
    row[1] = (y - 0.5) * 0.08 - pitch * 0.09
    row[2:5] = row[0] ** 2, row[1] ** 2, row[0] * row[1]
    row[8:11] = 0.5, 0.5, -1.8
    row[12:16] = 0.12, yaw, pitch, 1
    row[16:19] = 0.08 + 0.015 * yaw, 0.025 + 0.02 * pitch, 1
    return row.tolist()


@pytest.fixture
def head_model():
    rows, targets = [], []
    for target in ui.BASE_TRAIN_POINTS:
        for yaw, pitch in ((-0.2, -0.15), (0.1, -0.2), (0.2, 0.15), (-0.1, 0.2)):
            rows.extend([pose_row(*target, yaw, pitch)] * 10)
            targets.extend([target] * 10)
    return fit_mapping(rows, targets)


def test_same_target_head_motion_is_retained_for_compensation(head_model):
    for target in ui.VALIDATION_POINTS:
        for yaw, pitch in ((-0.13, 0.10), (0.13, -0.10)):
            result = predict_calibration(head_model, pose_row(*target, yaw, pitch))
            assert result == pytest.approx(target, abs=0.025)
    assert head_model["pose_required"]
    # Diagnostic binocular offsets cannot steer the estimated screen position.
    row = pose_row(0.2, 0.8, 0.08, -0.08)
    point = predict_calibration(head_model, row)
    row[16:19] = [0.7, -0.3, 0]
    assert predict_calibration(head_model, row) == point


def test_face_transform_extracts_angles_without_assuming_translation_is_gaze():
    angle = 0.23
    transform = np.eye(4)
    transform[:3, :3] = 1.8 * np.array(
        [
            [np.cos(angle), 0, np.sin(angle)],
            [0, 1, 0],
            [-np.sin(angle), 0, np.cos(angle)],
        ]
    )
    transform[:3, 3] = [20, -20, 30]
    assert face_pose(transform) == pytest.approx((angle, 0))
    observation = extract_observation(*artificial_eyes(), transform)
    assert observation["features"][13:16] == pytest.approx([angle, 0, 1])
    assert observation["screen_focus"] == "unknown"
    assert face_pose(np.zeros((4, 4))) is None
    assert face_pose(np.full((4, 4), np.nan)) is None


def test_glare_is_an_independent_quality_cue_and_no_glasses_are_required():
    gray, landmarks = artificial_eyes()
    normal = extract_observation(gray, landmarks)
    gray[87:90, 96:102] = 255
    reflected = extract_observation(gray, landmarks)
    assert normal["mode"] == reflected["mode"] == "binocular"
    assert (
        reflected["eye_diagnostics"]["right"]["glare_ratio"]
        > normal["eye_diagnostics"]["right"]["glare_ratio"]
    )
    assert (
        reflected["eye_diagnostics"]["right"]["quality"]
        < normal["eye_diagnostics"]["right"]["quality"]
    )
    assert reflected["eye_diagnostics"]["left"] == normal["eye_diagnostics"]["left"]


def test_personal_binocular_baseline_is_not_a_zero_disparity_assumption(head_model):
    monitor = ObservationMonitor()
    data = packet(features=pose_row(0.4, 0.6, 0.1, -0.1))
    assert data["features"][16] > 0.07  # This person's persistent offset is normal for them.
    assert monitor.update(data, head_model) == "consistent"
    data["features"][16] += 0.15
    start = data["timestamp_ns"]
    for i in range(7):
        data["timestamp_ns"] = start + i * 100_000_000
        state = monitor.update(data, head_model)
    assert state == "binocular_changed"
    assert data["screen_focus"] == "unknown"  # Not a focus/attention diagnosis.
    monitor.update(packet(), head_model)
    assert monitor.update(data, head_model) == "observing_change"


def test_monocular_and_missing_pose_are_not_reported_as_defocus(head_model):
    data = packet(features=pose_row(0.4, 0.6, 0.1, -0.1))
    data.update(mode="left_eye", right_eye_valid=False)
    data["features"][18] = 0
    monitor = ObservationMonitor()
    assert monitor.update(data, head_model) == "monocular"
    data["features"][15] = 0
    assert monitor.update(data, head_model) == "pose_unavailable"


def test_image_detail_is_separate_from_binocular_change(head_model):
    model = {**head_model, "image_detail_reference": 0.3}
    data = packet(features=pose_row(0.5, 0.5, 0, 0))
    data["eye_diagnostics"] = {"left": {"valid": True, "image_detail": 0.02, "glare_ratio": 0}}
    monitor = ObservationMonitor()
    start = data["timestamp_ns"]
    for i in range(7):
        data["timestamp_ns"] = start + i * 100_000_000
        state = monitor.update(data, model)
    assert state == "image_detail_low"


def test_eye_only_mapping_does_not_treat_missing_angles_as_measured_zero(head_model):
    model = {**head_model, "pose_required": False}
    model["pose_support"] = {
        "columns": [13, 14],
        "low": [0.5, 0.5],
        "high": [0.6, 0.6],
        "margin": [0.1, 0.1],
    }
    data = packet(features=pose_row(0.4, 0.6, 0, 0))
    data["features"][15] = 0
    data["features"][16] += 0.5
    assert ObservationMonitor().update(data, model) == "comparison_unavailable"


def test_diagnostic_model_rejects_corrupt_coefficients(head_model):
    from copy import deepcopy

    validate_observation_model(head_model)
    damaged = deepcopy(head_model)
    damaged["binocular_baseline"]["scale"][0] = 0
    with pytest.raises(ValueError):
        validate_observation_model(damaged)
    damaged = deepcopy(head_model)
    damaged["pose_support"]["low"][0] = float("nan")
    with pytest.raises(ValueError):
        validate_observation_model(damaged)


def test_pose_supplement_repeats_labels_without_becoming_validation(qtbot, tmp_path):
    dialog = ui.WebcamCalibrationDialog(ui.Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    dialog._identity = "pose-supplement"
    dialog._armed = True
    dialog._index = len(ui.BASE_TRAIN_POINTS)
    from time import monotonic

    dialog._started = monotonic() - 5
    dialog._features = [pose_row(x, y, 0, 0) for x, y in ui.BASE_TRAIN_POINTS for _ in range(5)]
    dialog._targets = [target for target in ui.BASE_TRAIN_POINTS for _ in range(5)]
    dialog._qualities = [0.8] * len(dialog._targets)
    dialog._point_samples = [pose_row(0.5, 0.5, -0.2, 0.1)] * 6
    dialog._point_qualities = [0.8] * 6
    dialog._advance()
    assert len(dialog._training_points) == 1
    assert dialog._validation_points == []
    assert dialog._targets[-6:] == [(0.5, 0.5)] * 6
    dialog.reject()
