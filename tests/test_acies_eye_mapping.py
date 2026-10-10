"""Independent-eye and drift regressions; synthetic data is not human validation."""

from copy import deepcopy

import numpy as np
import pytest

from oculidoc.devices.acies import FEATURE_COUNT, extract_observation, fit_mapping
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.webcam_gaze import (
    WebcamFeatureProcess,
    fit_calibration,
    load_calibration,
    predict_calibration,
    predict_packet,
)
from oculidoc.ui import webcam_calibration as ui
from test_acies import artificial_eyes
from test_webcam_gaze import config_file, packet, save_model


def eye_row(target, eye):
    x, y = target
    row = np.zeros(FEATURE_COUNT)
    row[0] = (x - 0.5) * 0.2 + (0.07 if eye == "left" else -0.04)
    row[1] = (y - 0.5) * 0.1 + (0.025 if eye == "left" else -0.015)
    row[2:5] = row[0] ** 2, row[1] ** 2, row[0] * row[1]
    row[8:11] = 0.5, 0.5, -1.8
    row[12], row[15], row[18] = 0.15, 1, 1
    return row.tolist()


@pytest.fixture
def eyes_model():
    targets = [target for target in ui.BASE_TRAIN_POINTS for _ in range(10)]
    eyes = [{name: eye_row(target, name) for name in ("left", "right")} for target in targets]
    rows = [np.mean(list(item.values()), axis=0).tolist() for item in eyes]
    return fit_calibration(rows, targets, eye_features=eyes)


def test_eye_specific_origin_does_not_jump_on_glare_or_monocular_switch(eyes_model):
    for target in ((0.08, 0.08), (0.91, 0.93), (0.5, 0.5)):
        data = packet(features=eye_row(target, "left"))
        data["eye_features"] = {name: eye_row(target, name) for name in ("left", "right")}
        together = predict_packet(eyes_model, data)
        assert together == pytest.approx(target, abs=0.02)
        for quality in (0.2, 0.95):
            data["eye_diagnostics"] = {"left": {"quality": quality}, "right": {"quality": 1}}
            assert predict_packet(eyes_model, data) == together
        for eye in ("left", "right"):
            single = deepcopy(data)
            other = "right" if eye == "left" else "left"
            single[f"{other}_eye_valid"] = False
            single["eye_features"][other] = None
            assert predict_packet(eyes_model, single) == pytest.approx(together, abs=0.02)


def test_unmapped_eye_never_reuses_other_eyes_coordinates(eyes_model):
    model = deepcopy(eyes_model)
    del model["eye_models"]["left"]
    data = packet(features=eye_row((0.7, 0.3), "left"))
    data.update(right_eye_valid=False, eye_features={"left": data["features"], "right": None})
    assert predict_packet(model, data) is None


def test_binocular_pass_does_not_authorize_unvalidated_monocular_output(eyes_model):
    data = packet(features=eye_row((0.7, 0.3), "left"))
    data["eye_features"] = {name: eye_row((0.7, 0.3), name) for name in ("left", "right")}
    model = deepcopy(eyes_model)
    model["validated"] = True
    assert predict_packet(model, data) is not None  # The combined estimate was validated.
    data["eye_features"]["right"] = None
    assert predict_packet(model, data) is None
    model["eye_models"]["left"]["validated"] = True
    assert predict_packet(model, data) is not None
    model["eye_models"]["left"]["validated"] = False
    assert predict_packet(model, data) is None
    data["eye_features"] = {"left": None, "right": None}
    assert predict_packet(model, data) is None


def test_extractor_preserves_independent_eye_coordinates():
    gray, landmarks = artificial_eyes()
    # Move just one measured iris; the other eye's coordinates stay unchanged.
    before = extract_observation(gray, landmarks)
    landmarks[473, 0] += 3 / 320
    after = extract_observation(gray, landmarks)
    assert after["eye_features"]["left"][0] > before["eye_features"]["left"][0]
    assert after["eye_features"]["right"][:5] == before["eye_features"]["right"][:5]
    landmarks[159, 1] = landmarks[145, 1]
    one = extract_observation(gray, landmarks)
    assert one["eye_features"]["right"] is None
    assert one["eye_features"]["left"] is not None


def test_independent_models_use_all_locations_not_sparse_eye_fragments():
    targets = [target for target in ui.BASE_TRAIN_POINTS for _ in range(10)]
    rows = [eye_row(target, "left") for target in targets]
    eyes = [{"left": row, "right": row if i < 10 else None} for i, row in enumerate(rows)]
    model = fit_calibration(rows, targets, eye_features=eyes)
    assert set(model["eye_models"]) == {"left"}
    with pytest.raises(ValueError, match="不匹配"):
        fit_calibration(rows, targets, eye_features=eyes[:-1])


def test_same_target_drift_can_cancel_without_shrinking_edge_motion():
    rng = np.random.default_rng(519)
    rows, targets = [], []
    for target in ui.BASE_TRAIN_POINTS:
        for pitch in np.linspace(-0.12, 0.12, 35):
            row = np.array(eye_row(target, "left"))
            # Independent lid/head nuisance while the intended target stays fixed.
            lid = rng.uniform(-0.025, 0.025)
            row[1] += 0.08 * pitch + 0.5 * lid
            row[2:5] = row[0] ** 2, row[1] ** 2, row[0] * row[1]
            row[11], row[14] = lid, pitch
            rows.append(row.tolist())
            targets.append(target)
    model = fit_mapping(rows, targets)
    for target in ((0.08, 0.08), (0.92, 0.92), (0.4, 0.6)):
        for pitch, lid in ((-0.07, 0.015), (0.07, -0.015)):
            row = np.array(eye_row(target, "left"))
            row[1] += 0.08 * pitch + 0.5 * lid
            row[2:5] = row[0] ** 2, row[1] ** 2, row[0] * row[1]
            row[11], row[14] = lid, pitch
            assert predict_calibration(model, row.tolist()) == pytest.approx(target, abs=0.045)


def test_corrupt_per_eye_model_is_rejected_before_camera_open(tmp_path, eyes_model):
    import json

    config = config_file(tmp_path)
    model = save_model(config, **eyes_model)
    model["eye_models"]["left"]["scale"][0] = 0
    directory = tmp_path / "calibration"
    path = directory / f"{model['identity'][:24]}.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    with pytest.raises(DeviceConnectionError):
        load_calibration(tmp_path, model["identity"], "balanced")


def test_invalid_eye_packet_is_rejected(tmp_path):
    process = WebcamFeatureProcess(config_file(tmp_path), "acies", tmp_path / "runtime")
    data = packet(features=eye_row((0.5, 0.5), "left"))
    data["eye_features"] = {"left": [0.1], "right": data["features"]}
    process._queue.put(data)
    with pytest.raises(DeviceConnectionError, match="分眼特征"):
        process.read()


def test_validation_uses_unsmoothed_eye_predictions(qtbot, tmp_path):
    from time import monotonic

    dialog = ui.WebcamCalibrationDialog(ui.Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    dialog._identity = "eyes-validation"
    dialog._armed = True
    dialog._index = len(ui.TRAIN_POINTS)
    dialog._started = monotonic() - 4
    dialog._model = {"eye_models": {}}
    target = ui.VALIDATION_POINTS[0]
    dialog._point_samples = [eye_row(target, "left")] * 6
    dialog._point_qualities = [0.9] * 6
    dialog._point_predictions = [(1.4, 1.4)] * 6
    dialog._preview = target  # Display smoothing cannot turn this into a pass.
    dialog._advance()
    assert dialog._validation_points[0]["mean_error"] > 1
    dialog.reject()


def test_old_validated_mapping_keeps_original_predictions():
    rows = [eye_row(target, "left") for target in ui.BASE_TRAIN_POINTS for _ in range(10)]
    targets = [target for target in ui.BASE_TRAIN_POINTS for _ in range(10)]
    model = fit_mapping(rows, targets)
    data = packet(features=eye_row((0.2, 0.8), "left"))
    assert predict_packet(model, data) == predict_calibration(model, data["features"])
