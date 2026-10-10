"""Calibration participation preserves prior work and freezes before holdout."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from oculidoc.config import Settings
from oculidoc.ui import webcam_calibration as ui
from test_webcam_gaze import config_file, packet, save_model


def dialog_for(qtbot, tmp_path):
    dialog = ui.WebcamCalibrationDialog(Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    screen = QApplication.primaryScreen()
    dialog._screen = (screen.name(), screen.geometry().width(), screen.geometry().height())
    return dialog


@pytest.mark.parametrize("cancel", [True, False])
def test_start_and_cancel_or_capture_failure_keep_saved_model(qtbot, tmp_path, monkeypatch, cancel):
    dialog = dialog_for(qtbot, tmp_path)
    model = save_model(config_file(tmp_path))
    dialog._identity = model["identity"]
    runtime = tmp_path / "runtime" / "webcam" / "calibration"
    runtime.mkdir(parents=True)
    active = runtime / f"{model['identity'][:24]}.json"
    active.write_text(json.dumps(model), encoding="utf-8")
    original = active.read_bytes()
    dialog._load_preview()
    dialog._collect(packet(features=[0.4, 0.5]))
    dialog._begin()
    assert dialog._armed and active.read_bytes() == original
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: None)
    if cancel:
        dialog.reject()
    else:
        dialog._fail("capture interrupted")
    assert active.read_bytes() == original


def test_fresh_holdout_keeps_better_previous_model_and_original_current_trace_metrics(
    qtbot, tmp_path
):
    dialog = dialog_for(qtbot, tmp_path)
    previous = save_model(config_file(tmp_path))
    previous["axis_models"] = {"source": "previous"}
    dialog._identity = previous["identity"]
    current = deepcopy(previous)
    current["axis_models"] = {"source": "current"}
    current["weights"][-1][0] = 0.2
    dialog._model = current
    dialog._frozen_models = {"previous": deepcopy(previous), "current": deepcopy(current)}
    for target in ui.VALIDATION_POINTS:
        rows = [packet(features=list(target)) for _ in range(6)]
        dialog._validation_batches.append({"target": list(target), "packets": rows})
        dialog._validation_points.append(
            {
                "target": list(target),
                "sample_count": 6,
                "mean_error": 0.2,
                "horizontal_mae": 0.2,
                "vertical_mae": 0.0,
                "mean_prediction": [target[0] + 0.2, target[1]],
            }
        )
    dialog._errors = [0.2] * 54
    dialog._finish_calibration()
    runtime = tmp_path / "runtime" / "webcam"
    active = json.loads(
        (runtime / "calibration" / f"{previous['identity'][:24]}.json").read_text(encoding="utf-8")
    )
    attempt = json.loads(
        next((runtime / "calibration_attempts").glob("*.json")).read_text(encoding="utf-8")
    )
    assert active["model_selection"]["selected"] == "previous"
    assert active["weights"] == previous["weights"]
    assert active["validation_error"] == pytest.approx(0.0)
    assert attempt["validation_error"] == pytest.approx(0.2)
    assert attempt["active_model_metrics"]["validation_error"] == pytest.approx(0.0)
    assert attempt["axis_models"] == {"source": "current"}
    assert active["cross_session_confirmed"] is False
    dialog.reject()


@pytest.mark.parametrize("incumbent_has_validated_eyes", [False, True])
def test_incomplete_new_single_eye_evidence_cannot_overstate_saved_output_coverage(
    qtbot, tmp_path, incumbent_has_validated_eyes
):
    dialog = dialog_for(qtbot, tmp_path)
    previous = save_model(config_file(tmp_path))
    previous["weights"][-1][0] = 0.03
    previous["axis_models"] = {"source": "previous"}
    dialog._identity = previous["identity"]

    def eye_mapping(shift, *, validated=False):
        child = {key: deepcopy(previous[key]) for key in ("mean", "scale", "weights")}
        child["weights"][-1][0] = shift
        child["axis_models"] = {"source": "eye"}
        if validated:
            child.update(
                validated=True,
                calibration_profile="balanced",
                validation_error=abs(shift),
                validation_max_error=abs(shift),
            )
        return child

    if incumbent_has_validated_eyes:
        previous["eye_models"] = {
            "left": eye_mapping(0.03, validated=True),
            "right": eye_mapping(0.03, validated=True),
        }
    current = eye_mapping(0.0)
    current["eye_models"] = {"left": eye_mapping(0.0), "right": eye_mapping(0.0)}
    dialog._model = deepcopy(current)
    dialog._frozen_models = {"previous": deepcopy(previous), "current": deepcopy(current)}
    for index, target in enumerate(ui.VALIDATION_POINTS):
        # Both eyes are calibrated in training, but neither has complete fresh
        # single-eye evidence: each is absent at one entire holdout location.
        left_valid, right_valid = index != 1, index != 0
        rows = []
        for _ in range(6):
            row = packet(features=list(target))
            row.update(
                left_eye_valid=left_valid,
                right_eye_valid=right_valid,
                mode="binocular"
                if left_valid and right_valid
                else "left_eye"
                if left_valid
                else "right_eye",
                eye_features={
                    "left": row["features"] if left_valid else None,
                    "right": row["features"] if right_valid else None,
                },
            )
            rows.append(row)
        dialog._validation_batches.append({"target": list(target), "packets": rows})
        dialog._validation_points.append(
            {
                "target": list(target),
                "sample_count": 6,
                "mean_error": 0.0,
                "horizontal_mae": 0.0,
                "vertical_mae": 0.0,
                "mean_prediction": list(target),
                "eye_errors": {
                    name: {"sample_count": 6, "mean_error": 0.0, "max_error": 0.0}
                    for name, valid in (("left", left_valid), ("right", right_valid))
                    if valid
                },
            }
        )
    dialog._errors = [0.0] * 54
    dialog._finish_calibration()
    runtime = tmp_path / "runtime" / "webcam"
    active = ui.load_calibration(runtime, previous["identity"], "balanced")
    comparisons = active["model_selection"]["comparisons"]
    assert active["model_selection"]["selected"] == "previous"
    assert active["weights"] == previous["weights"]
    assert comparisons["previous"]["output_coverage"] == 1
    assert comparisons["current"]["output_coverage"] == pytest.approx(7 / 9)
    assert comparisons["current"]["eligible"] is False
    assert (
        ui.predict_packet(
            dialog._frozen_models["current"], dialog._validation_batches[0]["packets"][0]
        )
        is None
    )
    assert (
        current["eye_models"]["left"]["weights"]
        == dialog._frozen_models["current"]["eye_models"]["left"]["weights"]
    )
    if incumbent_has_validated_eyes:
        for child in active["eye_models"].values():
            assert child["reusable"] is True and child["validated"] is True
            assert child["validation_error"] == pytest.approx(0.03)
            assert child["latest_validation"]["complete"] is False
            assert child["latest_validation"]["retained_previous_evidence"] is True
        assert ui.predict_packet(active, dialog._validation_batches[0]["packets"][0]) is not None
    dialog.reject()


def _left_only_contender_with_saved_incumbent(qtbot, tmp_path):
    dialog = dialog_for(qtbot, tmp_path)
    previous = save_model(config_file(tmp_path))
    previous["axis_models"] = {"source": "previous"}
    dialog._identity = previous["identity"]
    child = {key: deepcopy(previous[key]) for key in ("mean", "scale", "weights", "axis_models")}
    dialog._model = {**deepcopy(child), "eye_models": {"left": deepcopy(child)}}
    dialog._incumbent_model = previous
    dialog._targets = [target for target in ui.BASE_TRAIN_POINTS for _ in range(6)]
    dialog._features = [list(target) + [0.0] * 17 for target in dialog._targets]
    dialog._qualities = [0.8] * len(dialog._features)
    dialog._eye_features = [{"left": row, "right": None} for row in dialog._features]
    runtime = tmp_path / "runtime" / "webcam"
    active = runtime / "calibration" / f"{previous['identity'][:24]}.json"
    active.parent.mkdir(parents=True)
    active.write_text(json.dumps(previous), encoding="utf-8")
    dialog._write_attempt("in_progress")
    dialog._freeze_learning_models()
    dialog._index = len(ui.TRAIN_POINTS)
    dialog._armed = True
    return dialog, runtime, active


def test_valid_right_eye_observations_finish_when_current_only_maps_left_eye(qtbot, tmp_path):
    dialog, runtime, active_path = _left_only_contender_with_saved_incumbent(qtbot, tmp_path)
    for index, target in enumerate(ui.VALIDATION_POINTS):
        dialog._started = ui.monotonic() - 3.0
        for _ in range(6):
            row = packet(features=list(target))
            row.update(
                left_eye_valid=False,
                right_eye_valid=True,
                mode="right_eye",
                eye_features={"left": None, "right": row["features"]},
            )
            dialog._collect(row)
        assert len(dialog._point_samples) == 6
        assert not dialog._point_predictions
        dialog._advance()
        assert dialog._index == len(ui.TRAIN_POINTS) + index + 1
    assert dialog._reviewing and not dialog._armed
    active = json.loads(active_path.read_text(encoding="utf-8"))
    assert active["model_selection"]["selected"] == "previous"
    assert active["validation_error"] == pytest.approx(0)
    assert active["model_selection"]["comparisons"]["current"]["output_coverage"] == 0
    attempt = json.loads(
        next((runtime / "calibration_attempts").glob("*.json")).read_text(encoding="utf-8")
    )
    assert attempt["status"] == "completed" and attempt["validation_complete"] is False
    assert attempt["validation_error"] is None
    assert attempt["active_model_metrics"]["validation_error"] == pytest.approx(0)
    assert all(point["observed_sample_count"] == 6 for point in attempt["validation_points"])
    assert all(point["sample_count"] == 0 for point in attempt["validation_points"])
    assert all(point["mean_error"] is None for point in attempt["validation_points"])
    assert len(list((runtime / "training_rounds").rglob("*.json"))) == 1
    candidate = json.loads(
        (runtime / "calibration_candidates" / attempt["candidate_file"]).read_text(encoding="utf-8")
    )
    assert candidate["model"]["validated"] is False
    assert candidate["model"].get("reusable") is not True
    assert "本轮采集已完成并保存" in dialog.summary_text
    dialog.reject()


def test_missing_raw_observations_preserve_training_and_report_unfinished_validation(
    qtbot, tmp_path, monkeypatch
):
    dialog, runtime, active_path = _left_only_contender_with_saved_incumbent(qtbot, tmp_path)
    original = active_path.read_bytes()
    dialog._started = ui.monotonic() - 16.0
    for _ in range(6):
        dialog._collect(packet(features=None))
    assert not dialog._point_samples
    messages = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    dialog._advance()
    assert not dialog._reviewing and active_path.read_bytes() == original
    attempt = json.loads(
        next((runtime / "calibration_attempts").glob("*.json")).read_text(encoding="utf-8")
    )
    assert attempt["status"] == "failed" and not attempt["validation_points"]
    assert len(list((runtime / "training_rounds").rglob("*.json"))) == 1
    assert "有效眼部样本不足" in messages[-1]


def test_summary_io_failure_does_not_discard_committed_model(qtbot, tmp_path, monkeypatch):
    dialog = dialog_for(qtbot, tmp_path)
    dialog._identity = "io-failure"
    dialog._model = save_model(config_file(tmp_path))
    dialog._validation_points = [
        {
            "target": list(target),
            "sample_count": 5,
            "mean_error": 0.3,
            "horizontal_mae": 0.2,
            "vertical_mae": 0.1,
            "mean_prediction": list(target),
        }
        for target in ui.VALIDATION_POINTS
    ]
    dialog._errors = [0.3, 0.4]

    def fail_summary(*args, **kwargs):
        raise OSError("disk summary unavailable")

    monkeypatch.setattr(dialog, "_write_attempt", fail_summary)
    dialog._finish_calibration()
    assert (tmp_path / "runtime" / "webcam" / "calibration" / "io-failure.json").is_file()
    assert dialog._reviewing and dialog._attempt_finished
    assert "模型已保存" in dialog.summary_text and "摘要写入失败" in dialog.summary_text
    dialog.reject()


def test_training_archive_and_contenders_are_frozen_before_validation(qtbot, tmp_path, monkeypatch):
    dialog = dialog_for(qtbot, tmp_path)
    dialog._identity = "private-training"
    dialog._write_attempt("in_progress")
    dialog._targets = [target for target in ui.BASE_TRAIN_POINTS for _ in range(6)]
    dialog._features = [list(target) + [0.0] * 17 for target in dialog._targets]
    dialog._qualities = [0.8] * len(dialog._features)
    dialog._eye_features = [{} for _ in dialog._features]
    dialog._model = {"weights": [[1.0, 0.0]]}
    dialog._incumbent_model = {"weights": [[0.5, 0.5]]}
    # Validation is deliberately different and must never appear in the archive.
    dialog._validation_batches = [{"target": [0.123, 0.456], "packets": []}]
    monkeypatch.setattr(ui, "fit_accumulated", lambda *a: ({"weights": [[0.0, 1.0]]}, ["a", "b"]))
    dialog._freeze_learning_models()
    path = next((tmp_path / "runtime" / "webcam" / "training_rounds").rglob("*.json"))
    archived = json.loads(path.read_text(encoding="utf-8"))
    assert [0.123, 0.456] not in archived["targets"]
    assert set(dialog._frozen_models) == {"current", "previous", "accumulated"}
    dialog._model["weights"][0][0] = 99
    assert dialog._frozen_models["current"]["weights"][0][0] == 1.0
    dialog.reject()


def test_trace_commit_failure_can_retry_without_duplicate_or_partial_frames(
    qtbot, tmp_path, monkeypatch
):
    dialog = dialog_for(qtbot, tmp_path)
    dialog._identity = "trace-transaction"
    dialog._trace_buffer = [{"sequence": 1}]
    dialog._write_attempt("in_progress")
    trace = next((tmp_path / "runtime" / "webcam" / "calibration_traces").glob("*.jsonl"))
    original = trace.read_bytes()
    real_replace = Path.replace

    def fail_commit(path, target):
        if Path(target) == trace:
            raise OSError("trace commit interrupted")
        return real_replace(path, target)

    dialog._trace_buffer = [{"sequence": 2}, {"sequence": 3}]
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_commit)
        with pytest.raises(OSError, match="interrupted"):
            dialog._write_attempt("in_progress")
    assert trace.read_bytes() == original
    assert len(dialog._trace_buffer) == 2
    dialog._write_attempt("in_progress")
    assert [
        json.loads(line)["sequence"] for line in trace.read_text(encoding="utf-8").splitlines()
    ] == [1, 2, 3]
    dialog.reject()
