import json
from copy import deepcopy
from time import monotonic, monotonic_ns

import numpy as np
import pytest

from oculidoc.config import Settings
from oculidoc.devices.acies import GazeSmoother
from oculidoc.devices.acies_experiments import (
    experiment_plan,
    filter_sweep,
    filter_variants,
    load_experiment_model,
    summarize_experiment,
)
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.webcam_gaze import load_calibration
from oculidoc.ui.acies_experiment import AciesExperimentDialog
from test_webcam_gaze import config_file, packet, save_model


def test_schedule_repeats_by_seed_and_balances_condition_order_across_rounds():
    assert experiment_plan(717) == experiment_plan(717)
    assert experiment_plan(717) != experiment_plan(718)
    orders = []
    for i in range(3):
        plan = experiment_plan(717, i)
        assert len(plan) == 27 and sum(p["duration_s"] for p in plan) == 108
        orders.append([plan[j]["condition"] for j in (0, 9, 18)])
        for condition in orders[-1]:
            assert len({p["target"] for p in plan if p["condition"] == condition}) == 9
    assert all(len({order[i] for order in orders}) == 3 for i in range(3))
    assert len(experiment_plan(717, short=True)) == 9


def test_failed_candidate_is_diagnostic_only_and_never_unlocks_tasks(tmp_path):
    model = save_model(config_file(tmp_path), validated=False, validation_max_error=0.8)
    directory = tmp_path / "calibration_candidates"
    directory.mkdir()
    path = directory / f"{model['identity'][:24]}-20261009.json"
    path.write_text(json.dumps({"model": model, "validation_metrics": model}), encoding="utf-8")
    before = path.read_bytes()
    loaded = load_experiment_model(tmp_path, model["identity"], "balanced")
    assert loaded["experiment_source"] == "candidate" and loaded["validated"] is False
    assert loaded["task_eligible"] is False and path.read_bytes() == before
    with pytest.raises(DeviceConnectionError):
        load_calibration(tmp_path, model["identity"], "balanced")
    with pytest.raises(DeviceConnectionError):
        load_experiment_model(tmp_path, "another-subject", "balanced")
    model["scale"][0] = 0
    path.write_text(json.dumps({"model": model, "validation_metrics": model}), encoding="utf-8")
    with pytest.raises(DeviceConnectionError):
        load_experiment_model(tmp_path, model["identity"], "balanced")


def test_variant_sweep_is_paired_and_leaves_defaults_and_data_unchanged():
    plan = experiment_plan(1, short=True)[:1]
    rng = np.random.default_rng(321)
    target = np.array(plan[0]["target"])
    frames = []
    for i in range(120):
        raw = (target + rng.normal(0, 0.015, 2)).tolist() if i not in range(45, 49) else None
        frames.append(
            {
                "trial": 0,
                "elapsed_s": i / 30,
                "timestamp_ns": (i + 1) * 33_333_333,
                "raw_prediction": raw,
                "filtered_prediction": raw,
                "quality": 0.8,
                "closure_candidate": raw is None,
            }
        )
    before = deepcopy(frames)
    result = filter_sweep(plan, frames)
    assert frames == before
    assert result["auto_applied"] is False
    assert len(result["variants"]) == 13
    baseline = result["variants"]["baseline"]["summary"]["points"][0]
    assert baseline["filtered"]["coverage"] < 1
    assert baseline["filtered"]["scatter_rms"] < baseline["raw"]["scatter_rms"]
    assert len(baseline["closure_recoveries"]) == 1
    for name, value in result["variants"].items():
        if name != "baseline" and not name.startswith("interaction:"):
            assert (
                sum(v != filter_variants()["baseline"][k] for k, v in value["parameters"].items())
                == 1
            )


def test_error_and_missing_frames_are_not_hidden_by_smoothing_or_group_averages():
    plan = [{"target": (0.5, 0.5), "condition": "baseline", "settle_s": 1}]
    frames = [
        {
            "trial": 0,
            "elapsed_s": i / 10,
            "raw_prediction": [1.5, 0.5] if i % 2 else None,
            "filtered_prediction": None,
        }
        for i in range(30)
    ]
    result = summarize_experiment(plan, frames)["points"][0]
    assert result["raw"]["mean_error"] == 1.0
    assert result["raw"]["coverage"] == 0.5
    assert result["filtered"]["coverage"] == 0
    assert "mean_error" not in result["filtered"]
    assert result["arrival_ms"] is None


@pytest.mark.parametrize(
    "parameters",
    [
        {"min_cutoff": 0},
        {"speed_gain": -1},
        {"derivative_cutoff": float("nan")},
        {"median_window": 2},
    ],
)
def test_bad_filter_parameters_are_rejected(parameters):
    with pytest.raises(ValueError):
        GazeSmoother(**parameters)


def test_cancelled_experiment_saves_numeric_data_and_never_changes_mapping(qtbot, tmp_path):
    dialog = AciesExperimentDialog(Settings(data_dir=tmp_path))
    qtbot.addWidget(dialog)
    dialog._ready = True
    dialog._last_packet = monotonic()
    dialog._screen = ("test", 1920, 1080)
    dialog._model = {
        "mean": [0] * 19,
        "scale": [1] * 19,
        "weights": [[1, 0], [0, 1]] + [[0, 0]] * 18,
    }
    model = deepcopy(dialog._model)
    dialog._begin(short=True)
    dialog._started = monotonic() - 1.2
    data = packet(1, [0.4, 0.6])
    data["timestamp_ns"] = monotonic_ns()
    dialog._collect(data)
    dialog.reject()
    directory = next((tmp_path / "runtime/webcam/experiments").iterdir())
    manifest = json.loads((directory / "manifest.json").read_text())
    frames = [json.loads(line) for line in (directory / "frames.jsonl").read_text().splitlines()]
    assert manifest["status"] == "cancelled" and manifest["frames"] == 1
    assert manifest["reticle_visible"] is False and manifest["calibration_changed"] is False
    assert frames[0]["raw_prediction"] == [0.4, 0.6]
    assert "image" not in frames[0]
    assert dialog._model == model
    assert not (tmp_path / "runtime/webcam/calibration").exists()
    assert (directory / "filter_sweep.json").exists()
