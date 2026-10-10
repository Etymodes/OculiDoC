"""Accumulated calibration must not mix people or train on holdout targets."""

import json
from collections import Counter
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from oculidoc.config import GazeDeviceConfigStore, Settings
from oculidoc.devices import acies_learning as learning
from oculidoc.ui import webcam_calibration as calibration_ui
from oculidoc.ui.device_settings import DeviceSettingsDialog


def _round(value: float, samples: int = 5):
    targets = [(x, y) for x in (0.05, 0.5, 0.95) for y in (0.05, 0.5, 0.95) for _ in range(samples)]
    features = [[value, x, y] + [0.0] * 16 for x, y in targets]
    qualities = [0.8] * len(targets)
    eyes = [{"left": row, "right": None} for row in features]
    return features, targets, qualities, eyes


def test_training_history_keeps_identity_and_damaged_old_round_out(tmp_path, monkeypatch):
    first = learning.save_training_round(tmp_path, "patient-a", "round1", *_round(1.0))
    learning.save_training_round(tmp_path, "patient-b", "round1", *_round(9.0))
    learning.save_training_round(tmp_path, "patient-a", "round2", *_round(2.0))
    with pytest.raises(ValueError, match="已存在"):
        learning.save_training_round(tmp_path, "patient-a", "round1", *_round(3.0))
    with pytest.raises(ValueError, match="标识"):
        learning.save_training_round(tmp_path, "patient-a", "../escape", *_round(3.0))

    calls = []

    def fit(features, targets, qualities, *, eye_features):
        calls.append((features, targets, qualities, eye_features))
        return {"candidate": "pooled"}

    monkeypatch.setattr(learning, "fit_calibration", fit)
    model, round_ids = learning.fit_accumulated(tmp_path, "patient-a", "round2")
    assert round_ids == ["round1", "round2"]
    assert model == {"candidate": "pooled", "training_round_ids": round_ids}
    assert len(calls[0][0]) == 90
    assert {row[0] for row in calls[0][0]} == {1.0, 2.0}
    assert all(quality == 0.8 for quality in calls[0][2])
    assert first.read_text(encoding="utf-8").find("patient-a") >= 0

    first.write_text("{damaged", encoding="utf-8")
    model, round_ids = learning.fit_accumulated(tmp_path, "patient-a", "round2")
    assert model is None and round_ids == ["round2"]


@pytest.mark.parametrize("field", ["feature_version", "algorithm_version", "identity"])
def test_incompatible_history_is_skipped_but_invalid_current_raises(tmp_path, monkeypatch, field):
    first = learning.save_training_round(tmp_path, "patient-a", "round1", *_round(1.0))
    learning.save_training_round(tmp_path, "patient-a", "round2", *_round(2.0))
    record = json.loads(first.read_text(encoding="utf-8"))
    record[field] = "different"
    first.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(learning, "fit_calibration", lambda *args, **kwargs: pytest.fail("fit"))
    assert learning.fit_accumulated(tmp_path, "patient-a", "round2") == (None, ["round2"])
    with pytest.raises(ValueError):
        learning.fit_accumulated(tmp_path, "patient-a", "round1")


@pytest.mark.parametrize("invalid", ["few_locations", "few_samples", "nan", "quality", "eye"])
def test_only_complete_numeric_training_rounds_are_retained(tmp_path, invalid):
    features, targets, qualities, eyes = _round(1.0)
    if invalid == "few_locations":
        features, targets, qualities, eyes = features[:-5], targets[:-5], qualities[:-5], eyes[:-5]
    elif invalid == "few_samples":
        features, targets, qualities, eyes = features[:-1], targets[:-1], qualities[:-1], eyes[:-1]
    elif invalid == "nan":
        features[0][0] = float("nan")
    elif invalid == "quality":
        qualities[0] = 0
    else:
        eyes[0]["left"] = [0.0] * 18
    with pytest.raises(ValueError):
        learning.save_training_round(
            tmp_path, "patient-a", "round1", features, targets, qualities, eyes
        )
    assert not list(tmp_path.rglob("*.json"))


def test_accumulated_fit_uses_latest_five_rounds_and_balances_frame_rates(tmp_path, monkeypatch):
    for index in range(7):
        learning.save_training_round(
            tmp_path, "patient-a", f"round{index}", *_round(float(index), 100 + index)
        )
    calls = []

    def fit(features, targets, qualities, *, eye_features):
        calls.append(deepcopy((features, targets, qualities, eye_features)))
        return {}

    monkeypatch.setattr(learning, "fit_calibration", fit)
    model, round_ids = learning.fit_accumulated(tmp_path, "patient-a", "round6")
    assert round_ids == ["round2", "round3", "round4", "round5", "round6"]
    assert model["training_round_ids"] == round_ids
    assert len(calls[0][0]) == 5 * 9 * 60
    assert all(
        count == 60
        for count in Counter(
            (row[0], target) for row, target in zip(calls[0][0], calls[0][1], strict=True)
        ).values()
    )
    learning.fit_accumulated(tmp_path, "patient-a", "round6")
    assert calls[0] == calls[1]
    assert all(isinstance(target, tuple) for target in calls[0][1])


def test_fewer_samples_in_one_round_do_not_give_other_round_more_weight(tmp_path, monkeypatch):
    learning.save_training_round(tmp_path, "patient-a", "round1", *_round(1.0, 7))
    learning.save_training_round(tmp_path, "patient-a", "round2", *_round(2.0, 30))
    calls = []
    monkeypatch.setattr(
        learning, "fit_calibration", lambda features, *args, **kwargs: calls.append(features) or {}
    )
    learning.fit_accumulated(tmp_path, "patient-a", "round2")
    assert Counter(row[0] for row in calls[0]) == {1.0: 63, 2.0: 63}


def test_training_round_is_immutable_even_if_another_writer_wins_publish_race(
    tmp_path, monkeypatch
):
    def another_writer_wins(source, destination):
        destination.write_bytes(b"another completed round")
        raise FileExistsError(destination)

    monkeypatch.setattr(learning.os, "link", another_writer_wins)
    with pytest.raises(ValueError, match="不会覆盖"):
        learning.save_training_round(tmp_path, "patient-a", "round1", *_round(1.0))
    paths = list(tmp_path.rglob("*.json"))
    assert len(paths) == 1 and paths[0].read_bytes() == b"another completed round"
    assert not list(tmp_path.rglob("*.tmp"))


def _model(horizontal_shift: float):
    return {
        "mean": [0.0] * 19,
        "scale": [1.0] * 19,
        "weights": [[1.0, 0.0], [0.0, 1.0]] + [[0.0, 0.0]] * 17 + [[horizontal_shift, 0.0]],
    }


def test_frozen_models_share_holdout_and_missing_output_cannot_win():
    batches = []
    for x in (0.08, 0.5, 0.92):
        for y in (0.08, 0.5, 0.92):
            valid = {
                "features": [x, y] + [0.0] * 17,
                "confidence": 0.8,
            }
            batches.append(
                {
                    "target": [x, y],
                    "packets": [deepcopy(valid) for _ in range(6)]
                    + [{"features": None, "confidence": 0.0} for _ in range(6)],
                }
            )
    original = deepcopy(batches)
    scores = learning.evaluate_models(
        {
            "current": _model(0),
            "previous": _model(0.1),
            "sparse": {"eye_models": {"left": _model(0)}},
        },
        batches,
        "balanced",
    )
    assert batches == original
    assert scores["current"]["eligible"] and scores["current"]["coverage"] == 0.5
    assert scores["current"]["mean_error"] == pytest.approx(0)
    assert scores["previous"]["mean_error"] == pytest.approx(0.1)
    assert not scores["sparse"]["eligible"]
    assert learning.choose_model(scores) == "current"


def test_equal_holdout_scores_preserve_previous_mapping():
    equal = _score(0.05)
    assert (
        learning.choose_model(
            {
                "current": deepcopy(equal),
                "previous": deepcopy(equal),
                "accumulated": deepcopy(equal),
            }
        )
        == "previous"
    )


def _score(mean: float = 0.10, **changes):
    return {
        "eligible": True,
        "meets_profile": True,
        "mean_error": mean,
        "selection_score": mean,
        "center_error": mean,
        "edge_error": mean,
        "coverage": 0.9,
        "output_coverage": 0.9,
        "p90_error": mean + 0.05,
        "max_error": mean + 0.10,
        **changes,
    }


def test_validated_reference_is_not_downgraded_to_unvalidated_candidate():
    scores = {
        "current": _score(0.10),
        "accumulated": _score(0.05, meets_profile=False),
        "previous": _score(0.01, eligible=False),
    }
    assert learning.choose_model(scores) == "current"

    scores["current"]["meets_profile"] = False
    assert learning.choose_model(scores) == "accumulated"


@pytest.mark.parametrize(
    "regression,reason",
    [
        ({"mean_error": 0.196}, "改善不足"),
        ({"center_error": 0.206}, "中心"),
        ({"edge_error": 0.206}, "边缘"),
        ({"coverage": 0.889}, "覆盖率"),
        ({"output_coverage": 0.889}, "实际输出覆盖率"),
        ({"p90_error": 0.261}, "P90"),
        ({"max_error": 0.311}, "最大"),
        ({"eligible": False}, "九个不同目标"),
    ],
)
def test_mean_improvement_cannot_hide_regional_tail_or_coverage_regression(regression, reason):
    evaluations = {
        "previous": _score(0.20),
        "current": _score(0.19, **regression),
    }
    assert learning.choose_model(evaluations) == "previous"
    assert reason in " ".join(evaluations["current"]["promotion_reasons"])


def test_lowest_mean_qualifying_candidate_wins_and_tolerated_changes_are_explicit():
    evaluations = {
        "previous": _score(0.20),
        "current": _score(0.18),
        "accumulated": _score(
            0.17,
            center_error=0.205,
            edge_error=0.205,
            coverage=0.89,
            output_coverage=0.89,
            p90_error=0.26,
            max_error=0.31,
        ),
    }
    assert learning.choose_model(evaluations) == "accumulated"
    assert "本轮选择此" in evaluations["accumulated"]["promotion_reasons"][-1]


def test_no_eligible_incumbent_uses_current_as_comparison_reference():
    evaluations = {
        "previous": _score(0.01, eligible=False),
        "current": _score(0.20),
        "accumulated": _score(0.19),
    }
    assert learning.choose_model(evaluations) == "accumulated"
    evaluations["accumulated"]["mean_error"] = 0.199
    assert learning.choose_model(evaluations) == "current"


def test_outside_screen_errors_axis_bias_and_models_are_preserved(monkeypatch):
    batches = [
        {
            "target": [x, y],
            "packets": [{"features": [x, y] + [0.0] * 17, "confidence": 0.8}] * 5,
        }
        for x in (0.08, 0.5, 0.92)
        for y in (0.08, 0.5, 0.92)
    ]
    models = {"current": _model(1.3)}
    original = deepcopy(models)
    monkeypatch.setattr(
        learning, "fit_calibration", lambda *args, **kwargs: pytest.fail("holdout entered fit")
    )
    score = learning.evaluate_models(models, batches, "balanced")["current"]
    assert score["eligible"] and not score["meets_profile"]
    assert score["coverage"] == 1
    assert score["finite_coverage"] == 1 and score["output_coverage"] == 0
    for key in ("mean_error", "max_error", "p90_error", "center_error", "edge_error"):
        assert score[key] == pytest.approx(1.3)
    assert score["horizontal_mae"] == pytest.approx(1.3)
    assert score["horizontal_bias"] == pytest.approx(1.3)
    assert score["vertical_bias"] == pytest.approx(0)
    assert models == original


def test_small_edge_extrapolation_cannot_win_by_losing_displayable_output():
    batches = [
        {
            "target": [x, y],
            "packets": [{"features": [x, y] + [0.0] * 17, "confidence": 0.8}] * 5,
        }
        for x in (0.08, 0.5, 0.99)
        for y in (0.08, 0.5, 0.92)
    ]
    scores = learning.evaluate_models(
        {"previous": _model(-0.03), "current": _model(0.02)}, batches, "balanced"
    )
    assert scores["current"]["mean_error"] < scores["previous"]["mean_error"] - 0.005
    assert scores["current"]["coverage"] == scores["previous"]["coverage"] == 1
    assert scores["previous"]["output_coverage"] == 1
    assert scores["current"]["output_coverage"] == pytest.approx(2 / 3)
    assert scores["current"]["per_point"][-1]["sample_count"] == 5
    assert scores["current"]["per_point"][-1]["in_screen_sample_count"] == 0
    assert scores["current"]["per_point"][-1]["mean_error"] == pytest.approx(0.02)
    assert learning.choose_model(scores) == "previous"
    assert "实际输出覆盖率" in " ".join(scores["current"]["promotion_reasons"])


def test_missing_or_nonfinite_quality_lowers_coverage_instead_of_creating_predictions():
    batches = [
        {
            "target": [x, y],
            "packets": [{"features": [x, y] + [0.0] * 17, "confidence": 0.8}] * 5
            + [
                {"features": None, "confidence": 0.0},
                {"features": [x, y] + [0.0] * 17, "confidence": float("nan")},
            ],
        }
        for x in (0.08, 0.5, 0.92)
        for y in (0.08, 0.5, 0.92)
    ]
    score = learning.evaluate_models({"current": _model(0)}, batches, "balanced")["current"]
    assert score["eligible"] and score["coverage"] == pytest.approx(5 / 7)
    assert score["mean_error"] == pytest.approx(0)


@pytest.mark.parametrize("problem", ["few_locations", "few_samples", "repeated_locations"])
def test_incomplete_or_repeated_validation_cannot_be_eligible(problem):
    batches = [
        {
            "target": [x, y],
            "packets": [{"features": [x, y] + [0.0] * 17, "confidence": 0.8}] * 5,
        }
        for x in (0.08, 0.5, 0.92)
        for y in (0.08, 0.5, 0.92)
    ]
    if problem == "few_locations":
        batches.pop()
    elif problem == "few_samples":
        batches[-1]["packets"].pop()
    else:
        batches = [deepcopy(batches[0]) for _ in range(9)]
    assert not learning.evaluate_models({"current": _model(0)}, batches, "balanced")["current"][
        "eligible"
    ]


def test_per_eye_validation_includes_each_location_and_does_not_mutate_children():
    batches = [
        {
            "target": [x, y],
            "packets": [
                {
                    "features": [x, y] + [0.0] * 17,
                    "eye_features": {"left": [x, y] + [0.0] * 17, "right": [x, y] + [0.0] * 17},
                    "left_eye_valid": True,
                    "right_eye_valid": True,
                    "confidence": 0.8,
                }
            ]
            * 6,
        }
        for x in (0.08, 0.5, 0.92)
        for y in (0.08, 0.5, 0.92)
    ]
    models = {"current": {"eye_models": {"left": _model(0.1), "right": _model(-0.1)}}}
    original = deepcopy(models)
    score = learning.evaluate_models(models, batches, "balanced")["current"]
    assert score["mean_error"] == pytest.approx(0)
    for name in ("left", "right"):
        assert score["eye_validation"][name]["complete"]
        assert score["eye_validation"][name]["validated"]
        assert score["eye_validation"][name]["mean_error"] == pytest.approx(0.1)
        assert score["eye_validation"][name]["sample_count"] == 54
    assert models == original


def test_complete_low_precision_round_is_saved_reusable_and_preserves_old_version(
    qtbot, tmp_path, monkeypatch
):
    dialog = calibration_ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    screen = QApplication.primaryScreen()
    dialog._screen = (screen.name(), screen.geometry().width(), screen.geometry().height())
    dialog._identity = "one-patient-one-screen"
    dialog._model = _model(0)
    dialog._validation_points = [
        {
            "target": list(target),
            "sample_count": 5,
            "mean_error": 0.3,
            "horizontal_mae": 0.15,
            "vertical_mae": 0.15,
            "mean_prediction": list(target),
        }
        for target in calibration_ui.VALIDATION_POINTS
    ]
    dialog._errors = [0.3, 0.4]
    active = tmp_path / "runtime" / "webcam" / "calibration" / f"{dialog._identity[:24]}.json"
    active.parent.mkdir(parents=True)
    active.write_bytes(b"previous validated calibration")
    monkeypatch.setattr(calibration_ui.QMessageBox, "warning", lambda *args: None)

    dialog._finish_calibration()

    saved = json.loads(active.read_text(encoding="utf-8"))
    assert saved["reusable"] is True and saved["validated"] is False
    assert saved["quality_status"] == "needs_improvement"
    versions = active.parent.parent / "calibration_versions" / dialog._identity[:24]
    assert any(
        path.read_bytes() == b"previous validated calibration" for path in versions.glob("*.json")
    )
    assert next((active.parent.parent / "calibration_candidates").glob("*.json")).is_file()
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_preview_failure_after_completed_low_precision_round_keeps_participation_complete(
    qtbot, tmp_path, monkeypatch
):
    original_dialog = calibration_ui.WebcamCalibrationDialog

    class FailedPreviewDialog(original_dialog):
        last_result = None

        def start(self):
            self._reviewing = True
            self._validation_passed = False
            self.summary_text = "采集已完成，模型精度待改进，数据已保存可复用。"
            self._fail("摄像头已断开")
            FailedPreviewDialog.last_result = self.result()

    monkeypatch.setattr(calibration_ui, "WebcamCalibrationDialog", FailedPreviewDialog)
    monkeypatch.setattr(calibration_ui.QMessageBox, "warning", lambda *args: None)
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="webcam")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)
    monkeypatch.setattr(dialog, "_ensure_webcam_ready", lambda: True)
    dialog._calibrate_webcam()

    assert FailedPreviewDialog.last_result == QDialog.DialogCode.Accepted
    assert "采集已完成" in dialog.preflight_label.text()
