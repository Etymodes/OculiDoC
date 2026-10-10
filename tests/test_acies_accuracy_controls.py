"""Accuracy evidence uses intended targets only for scoring, never online correction."""

import csv
import hashlib
import json
from copy import deepcopy

import numpy as np
import pytest

from oculidoc.config import Settings
from oculidoc.devices.acies import MappingParameters, fit_mapping
from oculidoc.devices.acies_experiments import (
    load_paired_candidate,
    prediction_error,
    summarize_experiment,
    write_error_table,
)
from oculidoc.devices.webcam_gaze import predict_calibration
from oculidoc.ui.acies_experiment import AciesExperimentDialog
from test_acies_eye_mapping import eye_row
from test_webcam_gaze import config_file, save_model


def test_error_direction_distance_and_screen_units_are_not_camera_units():
    error = prediction_error((0.4, 0.7), (0.5, 0.5), (2000, 1000))
    assert error == pytest.approx(
        {
            "dx": -0.1,
            "dy": 0.2,
            "distance": np.hypot(0.1, 0.2),
            "dx_px": -200,
            "dy_px": 200,
            "distance_px": np.hypot(200, 200),
        }
    )
    assert prediction_error(None, (0.5, 0.5), (2000, 1000)) is None
    # Opposite errors cancel bias but not accuracy error.
    plan = [{"target": (0.5, 0.5), "condition": "baseline", "settle_s": 1}]
    rows = [
        {
            "trial": 0,
            "elapsed_s": 1 + i,
            "raw_prediction": (0.4 + 0.2 * i, 0.5),
            "timestamp_ns": i + 1,
        }
        for i in range(2)
    ]
    result = summarize_experiment(plan, rows, (2000, 1000))["points"][0]["raw"]
    assert result["horizontal_bias_px"] == pytest.approx(0, abs=1e-10)
    assert result["mean_error_px"] == pytest.approx(200)
    assert result["horizontal_mae"] == pytest.approx(0.1)


def test_csv_keeps_outside_errors_and_missing_values_distinct(tmp_path):
    plan = [{"target": (0.9, 0.5), "condition": "baseline"}]
    frames = [
        {
            "trial": 0,
            "elapsed_s": 2,
            "timestamp_ns": 1,
            "raw_prediction": (1.2, 0.3),
            "filtered_prediction": None,
        }
    ]
    before = deepcopy(frames)
    path = tmp_path / "errors.csv"
    write_error_table(path, plan, frames, (1000, 500))
    with path.open(encoding="utf-8-sig") as stream:
        row = next(csv.DictReader(stream))
    assert float(row["raw_dx_px"]) == pytest.approx(300)
    assert float(row["raw_dy_px"]) == pytest.approx(-100)
    assert row["filtered_distance"] == "" and row["candidate_x"] == ""
    assert frames == before


def test_paired_candidate_is_bound_to_calibration_and_cannot_approve_itself(tmp_path):
    original = save_model(config_file(tmp_path))
    assert load_paired_candidate(tmp_path, original) is None
    directory = tmp_path / "experiment_candidates"
    directory.mkdir()
    value = {
        "name": "control",
        "model": {
            **original,
            "feature_version": "0.3.0",
            "validated": False,
            "task_eligible": False,
        },
        "baseline_sha256": hashlib.sha256(
            json.dumps(original, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
    }
    path = directory / f"{original['identity'][:24]}.json"
    path.write_text(json.dumps(value))
    assert load_paired_candidate(tmp_path, original)["name"] == "control"
    assert load_paired_candidate(tmp_path, {**original, "created_at": "different"}) is None
    value["model"]["validated"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="provenance"):
        load_paired_candidate(tmp_path, original)


def test_no_lid_candidate_responds_to_iris_but_not_lid_motion():
    targets = [(x, y) for x in (0.05, 0.5, 0.95) for y in (0.05, 0.5, 0.95) for _ in range(12)]
    rows = [eye_row(t, "left") for t in targets]
    for row, target in zip(rows, targets, strict=True):
        row[11] = target[1] * 0.04  # Perfectly correlated in calibration, unstable later.
    model = fit_mapping(rows, targets, parameters=MappingParameters(use_lids=False))
    row = eye_row((0.15, 0.85), "left")
    before = predict_calibration(model, row)
    row[11:13] = [0.2, 0.4]
    assert predict_calibration(model, row) == before
    assert before == pytest.approx((0.15, 0.85), abs=0.02)
    assert predict_calibration(model, eye_row((0.85, 0.15), "left")) == pytest.approx(
        (0.85, 0.15), abs=0.02
    )


@pytest.mark.parametrize(
    "options",
    [
        {"ridge_scale": 0},
        {"lid_penalty": -1},
        {"stationary_penalty": float("nan")},
        {"use_lids": 1},
    ],
)
def test_bad_mapping_controls_rejected(options):
    with pytest.raises(ValueError):
        MappingParameters(**options)


def test_new_target_cannot_pull_either_online_prediction(qtbot, tmp_path):
    from time import monotonic, monotonic_ns

    from test_webcam_gaze import packet

    dialog = AciesExperimentDialog(Settings(data_dir=tmp_path))
    qtbot.addWidget(dialog)
    model = {"mean": [0] * 19, "scale": [1] * 19, "weights": [[1, 0], [0, 1]] + [[0, 0]] * 18}
    dialog._model = model
    dialog._candidate = {"name": "paired", "model": deepcopy(model)}
    dialog._screen = ("test", 1000, 500)
    dialog._active = True
    for target in ((0.1, 0.1), (0.9, 0.9)):
        dialog._plan = [{"target": target, "duration_s": 4}]
        dialog._started = monotonic() - 2
        row = packet(1, [0.4, 0.6])
        row["timestamp_ns"] = monotonic_ns()
        dialog._collect(row)
    first, last = dialog._frames
    assert first["raw_prediction"] == last["raw_prediction"] == (0.4, 0.6)
    assert first["candidate_raw_prediction"] == last["candidate_raw_prediction"] == (0.4, 0.6)
    assert first["raw_error"]["dx"] > 0 and last["raw_error"]["dx"] < 0
    dialog._active = False
    dialog.reject()
