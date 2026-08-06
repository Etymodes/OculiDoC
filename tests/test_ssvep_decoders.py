"""Algorithm and calibration tests for the v0.1.3 SSVEP package."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from oculidoc.bci.ssvep.calibration import (
    CalibrationDataset,
    load_trca_model,
    save_trca_model,
)
from oculidoc.bci.ssvep.config import SsvepStimulusConfig
from oculidoc.bci.ssvep.registry import DecoderRegistry
from oculidoc.signals.sources import SimulatedEEGSource


def _simulated_trial(frequency_hz: float, seed: int) -> np.ndarray:
    return (
        SimulatedEEGSource(
            sample_rate_hz=250.0,
            channel_names=("O1", "Oz", "O2"),
            target_frequency_hz=frequency_hz,
            seed=seed,
        )
        .acquire(2.0)
        .values_uv
    )


@pytest.mark.parametrize("decoder_name", ["cca", "fbcca"])
def test_training_free_decoders_classify_configured_frequencies(decoder_name: str) -> None:
    frequencies = (8.0, 10.0, 12.0, 15.0)
    stimulus = SsvepStimulusConfig.for_frequencies(frequencies)
    decoder = DecoderRegistry.create(
        decoder_name,
        stimulus=stimulus,
        sample_rate_hz=250.0,
    )
    results = [
        decoder.decode(_simulated_trial(frequency, index))
        for index, frequency in enumerate(frequencies)
    ]
    assert [result.target_frequency_hz for result in results] == list(frequencies)
    assert not any(result.rejected for result in results)


def test_trca_and_ensemble_trca_use_patient_calibration(tmp_path: Path) -> None:
    frequencies = (10.0, 12.0)
    trials = {
        frequency: np.stack(
            [_simulated_trial(frequency, seed) for seed in range(3)],
            axis=0,
        )
        for frequency in frequencies
    }
    dataset = CalibrationDataset(
        patient_id="patient-a",
        sample_rate_hz=250.0,
        channel_names=("O1", "Oz", "O2"),
        trials_by_frequency=trials,
    )
    model = dataset.train_trca()
    path = save_trca_model(
        tmp_path / "model.npz",
        model,
        patient_id="patient-a",
        simulated=False,
    )
    loaded, metadata = load_trca_model(path, expected_patient_id="patient-a")
    assert metadata["algorithm_version"] == "trca-1.0"
    stimulus = SsvepStimulusConfig.for_frequencies(frequencies)
    for name in ("trca", "etrca"):
        decoder = DecoderRegistry.create(
            name,
            stimulus=stimulus,
            sample_rate_hz=250.0,
            model=loaded,
        )
        result = decoder.decode(_simulated_trial(12.0, 99))
        assert result.target_frequency_hz == 12.0


def test_simulated_calibration_is_blocked_for_patient_use(tmp_path: Path) -> None:
    trials = {10.0: np.stack([_simulated_trial(10.0, seed) for seed in range(2)])}
    model = CalibrationDataset(
        "patient-a",
        250.0,
        ("O1", "Oz", "O2"),
        trials,
        simulated=True,
    ).train_trca()
    path = save_trca_model(
        tmp_path / "simulated-model.npz",
        model,
        patient_id="patient-a",
        simulated=True,
    )
    with pytest.raises(ValueError, match="Simulated TRCA"):
        load_trca_model(path, expected_patient_id="patient-a")


def test_stimulus_frequencies_are_configuration_owned() -> None:
    config = SsvepStimulusConfig.for_frequencies((7.5, 9.25, 11.75))
    assert config.frequencies_hz == (7.5, 9.25, 11.75)
    assert len(config.targets) == 3
