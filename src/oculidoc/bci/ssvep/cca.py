"""Training-free canonical-correlation SSVEP baseline."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from oculidoc.bci.ssvep.config import SsvepStimulusConfig
from oculidoc.bci.ssvep.evaluation import (
    DecoderResult,
    build_decoder_result,
    rejected_result,
)

ALGORITHM_VERSION = "cca-1.0"


def reference_signals(
    frequency_hz: float,
    *,
    sample_rate_hz: float,
    sample_count: int,
    harmonics: int,
    phase_rad: float = 0.0,
    delay_seconds: float = 0.0,
) -> NDArray[np.float64]:
    if frequency_hz <= 0 or sample_rate_hz <= 0 or sample_count < 2 or harmonics < 1:
        raise ValueError("Invalid SSVEP reference parameters.")
    time_axis = np.arange(sample_count, dtype=np.float64) / sample_rate_hz + delay_seconds
    rows: list[NDArray[np.float64]] = []
    for harmonic in range(1, harmonics + 1):
        angle = 2.0 * np.pi * harmonic * frequency_hz * time_axis + harmonic * phase_rad
        rows.extend((np.sin(angle), np.cos(angle)))
    return np.asarray(rows, dtype=np.float64)


def _inverse_sqrt(matrix: NDArray[np.float64], regularization: float) -> NDArray[np.float64]:
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    floor = max(regularization, float(eigenvalues.max(initial=0.0)) * regularization)
    inverse = 1.0 / np.sqrt(np.maximum(eigenvalues, floor))
    return (eigenvectors * inverse) @ eigenvectors.T


def canonical_correlation(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    *,
    regularization: float = 1e-6,
) -> float:
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.ndim != 2 or y.ndim != 2 or x.shape[1] != y.shape[1] or x.shape[1] < 2:
        raise ValueError("CCA inputs must be features x matching samples.")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("CCA inputs must be finite.")
    x = x - x.mean(axis=1, keepdims=True)
    y = y - y.mean(axis=1, keepdims=True)
    denominator = x.shape[1] - 1
    covariance_x = x @ x.T / denominator
    covariance_y = y @ y.T / denominator
    covariance_xy = x @ y.T / denominator
    scale_x = max(float(np.trace(covariance_x)) / max(1, x.shape[0]), 1.0)
    scale_y = max(float(np.trace(covariance_y)) / max(1, y.shape[0]), 1.0)
    covariance_x += np.eye(x.shape[0]) * regularization * scale_x
    covariance_y += np.eye(y.shape[0]) * regularization * scale_y
    whitened = (
        _inverse_sqrt(covariance_x, regularization)
        @ covariance_xy
        @ _inverse_sqrt(covariance_y, regularization)
    )
    singular_values = np.linalg.svd(whitened, compute_uv=False)
    return float(np.clip(singular_values[0], 0.0, 1.0))


@dataclass(frozen=True, slots=True)
class CcaDecoder:
    stimulus: SsvepStimulusConfig
    sample_rate_hz: float
    min_score: float = 0.12
    min_margin: float = 0.015
    regularization: float = 1e-6

    algorithm_name = "cca"
    algorithm_version = ALGORITHM_VERSION

    def decode(self, values_uv: NDArray[np.float64]) -> DecoderResult:
        values = np.asarray(values_uv, dtype=np.float64)
        if values.ndim != 2 or values.shape[1] < max(2, round(self.sample_rate_hz * 0.5)):
            return rejected_result(self.stimulus.frequencies_hz, "window_too_short")
        if not np.isfinite(values).all():
            return rejected_result(self.stimulus.frequencies_hz, "non_finite_samples")
        scores = []
        for target in self.stimulus.targets:
            reference = reference_signals(
                target.frequency_hz,
                sample_rate_hz=self.sample_rate_hz,
                sample_count=values.shape[1],
                harmonics=self.stimulus.harmonics,
                phase_rad=target.phase_rad,
                delay_seconds=self.stimulus.delay_seconds,
            )
            scores.append(
                canonical_correlation(values, reference, regularization=self.regularization)
            )
        return build_decoder_result(
            self.stimulus.frequencies_hz,
            scores,
            min_score=self.min_score,
            min_margin=self.min_margin,
        )

    def parameters(self) -> dict[str, object]:
        return {
            "algorithm": self.algorithm_name,
            "algorithm_version": self.algorithm_version,
            "harmonics": self.stimulus.harmonics,
            "window_seconds": self.stimulus.window_seconds,
            "delay_seconds": self.stimulus.delay_seconds,
            "regularization": self.regularization,
            "min_score": self.min_score,
            "min_margin": self.min_margin,
        }
