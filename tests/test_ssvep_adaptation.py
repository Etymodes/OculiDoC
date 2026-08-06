"""Overfitting guards for rolling patient-specific SSVEP adaptation."""

from __future__ import annotations

import numpy as np

from oculidoc.bci.ssvep.adaptation import guarded_trca_adaptation


def _wave(frequency: float, *, phase: float = 0.0) -> np.ndarray:
    time_axis = np.arange(500, dtype=np.float64) / 250.0
    base = np.sin(2 * np.pi * frequency * time_axis + phase)
    return np.stack((base, 1.2 * base, 0.8 * base), axis=0)


def test_guarded_adaptation_promotes_only_after_untouched_holdout_passes() -> None:
    trials = {
        frequency: np.stack([_wave(frequency, phase=index * 0.01) for index in range(4)])
        for frequency in (6.0, 10.0)
    }
    model, decision = guarded_trca_adaptation(
        trials,
        sample_rate_hz=250.0,
        channel_names=("O1", "Oz", "O2"),
    )
    assert model is not None
    assert decision.accepted is True
    assert decision.training_trials_per_frequency == 3
    assert decision.holdout_trials_per_frequency == 1
    assert decision.to_dict()["unlabeled_self_training"] is False


def test_guarded_adaptation_rejects_a_candidate_that_fails_holdout() -> None:
    trials = {
        6.0: np.stack([_wave(6.0), _wave(6.0, phase=0.01), _wave(6.0, phase=0.02), _wave(10.0)]),
        10.0: np.stack([_wave(10.0), _wave(10.0, phase=0.01), _wave(10.0, phase=0.02), _wave(6.0)]),
    }
    model, decision = guarded_trca_adaptation(
        trials,
        sample_rate_hz=250.0,
        channel_names=("O1", "Oz", "O2"),
    )
    assert model is None
    assert decision.accepted is False
    assert decision.reason == "holdout_accuracy_below_threshold"


def test_guarded_adaptation_refuses_tiny_calibration_sets() -> None:
    trials = {
        frequency: np.stack([_wave(frequency, phase=index * 0.01) for index in range(3)])
        for frequency in (6.0, 10.0)
    }
    model, decision = guarded_trca_adaptation(
        trials,
        sample_rate_hz=250.0,
        channel_names=("O1", "Oz", "O2"),
    )
    assert model is None
    assert decision.reason == "insufficient_labeled_trials"
