"""Accumulated calibration must not mix people or train on holdout targets."""

from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from oculidoc.config import GazeDeviceConfigStore, Settings
from oculidoc.devices import acies_learning as learning
from oculidoc.ui import webcam_calibration as calibration_ui
from oculidoc.ui.device_settings import DeviceSettingsDialog


def _round(value: float):
    targets = [(x, y) for x in (0.05, 0.5, 0.95) for y in (0.05, 0.5, 0.95) for _ in range(5)]
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
    equal = {"eligible": True, "selection_score": 0.05}
    assert (
        learning.choose_model({"current": equal, "previous": equal, "accumulated": equal})
        == "previous"
    )


def test_eligible_validated_mapping_outranks_lower_mean_unvalidated_mapping():
    scores = {
        "current": {"eligible": True, "meets_profile": True, "selection_score": 0.10},
        "accumulated": {"eligible": True, "meets_profile": False, "selection_score": 0.05},
        "previous": {"eligible": False, "meets_profile": True, "selection_score": 0.01},
    }
    assert learning.choose_model(scores) == "current"

    scores["current"]["meets_profile"] = False
    assert learning.choose_model(scores) == "accumulated"


def test_failed_candidate_does_not_replace_existing_active_calibration(
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

    assert active.read_bytes() == b"previous validated calibration"
    assert next((active.parent.parent / "calibration_candidates").glob("*.json")).is_file()
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_preview_failure_after_failed_validation_does_not_claim_saved_calibration(
    qtbot, tmp_path, monkeypatch
):
    original_dialog = calibration_ui.WebcamCalibrationDialog

    class FailedPreviewDialog(original_dialog):
        last_result = None

        def start(self):
            self._reviewing = True
            self._validation_passed = False
            self._fail("摄像头已断开")
            FailedPreviewDialog.last_result = self.result()

    monkeypatch.setattr(calibration_ui, "WebcamCalibrationDialog", FailedPreviewDialog)
    monkeypatch.setattr(calibration_ui.QMessageBox, "warning", lambda *args: None)
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="webcam")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)
    original_label = dialog.preflight_label.text()

    dialog._calibrate_webcam()

    assert FailedPreviewDialog.last_result == QDialog.DialogCode.Rejected
    assert dialog.preflight_label.text() == original_label
