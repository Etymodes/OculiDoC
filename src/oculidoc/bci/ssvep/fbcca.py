"""Filter-bank canonical-correlation SSVEP decoder."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from oculidoc.bci.ssvep.cca import canonical_correlation, reference_signals
from oculidoc.bci.ssvep.config import SsvepStimulusConfig
from oculidoc.bci.ssvep.evaluation import (
    DecoderResult,
    build_decoder_result,
    rejected_result,
)

ALGORITHM_VERSION = "fbcca-1.0"
DEFAULT_SUBBAND_LOW_HZ = (6.0, 14.0, 22.0, 30.0, 38.0)


def _fft_bandpass(
    values_uv: NDArray[np.float64],
    *,
    sample_rate_hz: float,
    low_hz: float,
    high_hz: float,
) -> NDArray[np.float64]:
    values = np.asarray(values_uv, dtype=np.float64)
    centered = values - values.mean(axis=1, keepdims=True)
    spectrum = np.fft.rfft(centered, axis=1)
    frequencies = np.fft.rfftfreq(centered.shape[1], 1.0 / sample_rate_hz)
    mask = (frequencies >= low_hz) & (frequencies <= high_hz)
    spectrum[:, ~mask] = 0.0
    return np.fft.irfft(spectrum, n=centered.shape[1], axis=1)


@dataclass(frozen=True, slots=True)
class FbccaDecoder:
    stimulus: SsvepStimulusConfig
    sample_rate_hz: float
    subband_low_hz: tuple[float, ...] = DEFAULT_SUBBAND_LOW_HZ
    weight_exponent: float = 1.25
    weight_offset: float = 0.25
    min_score: float = 0.025
    min_margin: float = 0.003
    regularization: float = 1e-6

    algorithm_name = "fbcca"
    algorithm_version = ALGORITHM_VERSION

    def decode(self, values_uv: NDArray[np.float64]) -> DecoderResult:
        values = np.asarray(values_uv, dtype=np.float64)
        if values.ndim != 2 or values.shape[1] < max(2, round(self.sample_rate_hz * 0.5)):
            return rejected_result(self.stimulus.frequencies_hz, "window_too_short")
        if not np.isfinite(values).all():
            return rejected_result(self.stimulus.frequencies_hz, "non_finite_samples")
        nyquist = self.sample_rate_hz / 2.0
        high_hz = min(90.0, nyquist - max(0.5, self.sample_rate_hz / values.shape[1]))
        subbands = tuple(low for low in self.subband_low_hz if 0 < low < high_hz)
        if not subbands:
            return rejected_result(self.stimulus.frequencies_hz, "sample_rate_too_low")
        scores: NDArray[np.float64] = np.zeros(len(self.stimulus.targets), dtype=np.float64)
        weights = np.asarray(
            [
                (index + 1) ** (-self.weight_exponent) + self.weight_offset
                for index in range(len(subbands))
            ],
            dtype=np.float64,
        )
        weights /= weights.sum()
        references = tuple(
            reference_signals(
                target.frequency_hz,
                sample_rate_hz=self.sample_rate_hz,
                sample_count=values.shape[1],
                harmonics=self.stimulus.harmonics,
                phase_rad=target.phase_rad,
                delay_seconds=self.stimulus.delay_seconds,
            )
            for target in self.stimulus.targets
        )
        for weight, low_hz in zip(weights, subbands, strict=True):
            filtered = _fft_bandpass(
                values,
                sample_rate_hz=self.sample_rate_hz,
                low_hz=low_hz,
                high_hz=high_hz,
            )
            for index, reference in enumerate(references):
                correlation = canonical_correlation(
                    filtered,
                    reference,
                    regularization=self.regularization,
                )
                scores[index] += weight * correlation**2
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
            "subband_low_hz": list(self.subband_low_hz),
            "subband_count": len(self.subband_low_hz),
            "weight_exponent": self.weight_exponent,
            "weight_offset": self.weight_offset,
            "harmonics": self.stimulus.harmonics,
            "window_seconds": self.stimulus.window_seconds,
            "delay_seconds": self.stimulus.delay_seconds,
            "min_score": self.min_score,
            "min_margin": self.min_margin,
        }
