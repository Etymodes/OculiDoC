"""Behavioral temporal tests with independent synthetic motion, not human accuracy claims."""

import hashlib
import json

import numpy as np
import pytest

from oculidoc.devices.acies import FEATURE_COUNT, GazeSmoother, fit_mapping
from oculidoc.devices.webcam_gaze import calibration_identity, load_calibration, predict_calibration
from oculidoc.ui import webcam_calibration as ui
from test_webcam_gaze import SCREEN, config_file, packet, save_model


def run_filter(points, fps=30, quality=0.8):
    smoother = GazeSmoother()
    return np.array(
        [
            smoother.update(tuple(p), 1_000_000_000 + round(i * 1e9 / fps), quality)
            for i, p in enumerate(points)
        ]
    )


@pytest.mark.parametrize("fps", [20, 30, 60])
def test_static_noise_is_suppressed_without_freezing_small_real_motion(fps):
    rng = np.random.default_rng(3108)
    points = np.array([0.45, 0.55]) + rng.normal(0, 0.025, (fps * 8, 2))
    output = run_filter(points, fps)
    raw_rms = np.sqrt(np.mean((points[fps:] - [0.45, 0.55]) ** 2))
    filtered_rms = np.sqrt(np.mean((output[fps:] - [0.45, 0.55]) ** 2))
    assert filtered_rms < 0.45 * raw_rms
    small_step = np.vstack([np.tile([0.4, 0.4], (fps, 1)), np.tile([0.46, 0.46], (fps, 1))])
    step_output = run_filter(small_step, fps)
    assert np.all(step_output[fps + round(0.35 * fps)] > 0.454)
    assert step_output[-1] == pytest.approx([0.46, 0.46], abs=0.001)


@pytest.mark.parametrize("fps", [20, 30, 60])
def test_sustained_large_shift_arrives_quickly_without_overshoot(fps):
    points = np.vstack([np.tile([0.2, 0.7], (fps, 1)), np.tile([0.8, 0.2], (fps, 1))])
    output = run_filter(points, fps)
    after = output[fps + round(0.15 * fps)]
    assert after[0] >= 0.74 and after[1] <= 0.25
    assert np.all((output[:, 0] >= 0.2) & (output[:, 0] <= 0.8))
    assert np.all((output[:, 1] >= 0.2) & (output[:, 1] <= 0.7))


def test_single_frame_spike_does_not_pull_reticle_away_from_fixation():
    points = np.tile([0.5, 0.5], (80, 1))
    points[35] = [0.95, 0.05]
    assert np.max(np.abs(run_filter(points) - 0.5)) < 1e-10


def test_fast_horizontal_motion_does_not_loosen_vertical_filter():
    t = np.arange(240) / 30
    vertical = 0.5 + 0.04 * np.sin(2 * np.pi * 9 * t)
    static_x = run_filter(np.column_stack([np.full(240, 0.5), vertical]))
    moving_x = run_filter(np.column_stack([0.5 + 0.3 * np.sin(2 * np.pi * t), vertical]))
    assert moving_x[:, 1] == pytest.approx(static_x[:, 1])


def test_continuous_ramp_is_not_stuck_and_invalid_samples_clear_memory():
    points = np.column_stack([np.linspace(0.2, 0.8, 150)] * 2)
    output = run_filter(points)
    assert np.max(np.abs(output[30:] - points[30:])) < 0.035
    smoother = GazeSmoother()
    smoother.update((0.2, 0.2), 1_000_000_000, 0.8)
    with pytest.raises(ValueError):
        smoother.update((float("nan"), 0.2), 1_030_000_000, 0.8)
    assert smoother.point is None


def test_compatible_valid_map_keeps_its_identity_without_recalibration(tmp_path):
    path = config_file(tmp_path)
    config = json.loads(path.read_text())
    expected = hashlib.sha256(
        json.dumps([config, "acies", "participant-1", SCREEN, "0.3.0"], sort_keys=True).encode()
    ).hexdigest()
    assert calibration_identity(config, "acies", "participant-1", SCREEN) == expected
    original = save_model(path, algorithm_version="0.3.0")
    assert load_calibration(tmp_path, expected, "balanced") == original


def test_noisy_shortcut_is_rejected_on_unseen_locations_and_frames():
    rng = np.random.default_rng(144)
    rows, targets = [], []
    for x, y in ui.BASE_TRAIN_POINTS:
        for _ in range(60):
            row = np.zeros(FEATURE_COUNT)
            row[:2] = [0.16 * (x - 0.5), 0.08 * (y - 0.5)] + rng.normal(0, 0.001, 2)
            row[2:5] = row[0] ** 2, row[1] ** 2, row[0] * row[1]
            # A nuisance proxy seems related to labels in average but jitters badly.
            row[5] = 0.5 * (x - 0.5) + rng.normal(0, 0.25)
            row[6] = 0.5 * (y - 0.5) + rng.normal(0, 0.25)
            row[15] = row[18] = 1
            rows.append(row.tolist())
            targets.append((x, y))
    model = fit_mapping(rows, targets)
    errors = []
    for x, y in ui.VALIDATION_POINTS:
        for _ in range(20):
            row = np.zeros(FEATURE_COUNT)
            row[:2] = [0.16 * (x - 0.5), 0.08 * (y - 0.5)] + rng.normal(0, 0.001, 2)
            row[5:7] = rng.normal(0, 0.5, 2)  # Nuisance association gone in a new session.
            row[15] = row[18] = 1
            errors.append(
                np.linalg.norm(np.array(predict_calibration(model, row.tolist())) - [x, y])
            )
    assert np.mean(errors) < 0.04
    assert max(errors) < 0.07


def test_calibration_trace_preserves_numeric_frames_without_images(qtbot, tmp_path):
    dialog = ui.WebcamCalibrationDialog(ui.Settings(data_dir=tmp_path), "acies")
    qtbot.addWidget(dialog)
    dialog._identity = "trace-test"
    dialog._collect(packet(0, [0.1, 0.2]))
    dialog._begin()
    dialog._collect(packet(1, [0.12, 0.21]))
    dialog.reject()
    trace = next((tmp_path / "runtime/webcam/calibration_traces").glob("*.jsonl"))
    frames = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(frames) == 1 and frames[0]["features"][:2] == [0.12, 0.21]
    assert frames[0]["raw_prediction"] == [0.5, 0.5]
    assert "image" not in frames[0] and "target" not in frames[0]
    record = json.loads(dialog._attempt_path.read_text(encoding="utf-8"))
    assert record["status"] == "cancelled" and record["trace_frames"] == 1
