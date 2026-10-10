"""Ensemble TRCA decoder using all calibrated spatial filters."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from oculidoc.bci.ssvep.evaluation import (
    DecoderResult,
    build_decoder_result,
    rejected_result,
)
from oculidoc.bci.ssvep.trca import TrcaModel, _correlation

ALGORITHM_VERSION = "etrca-1.0"


@dataclass(frozen=True, slots=True)
class EtrcaDecoder:
    model: TrcaModel
    min_score: float = 0.1
    min_margin: float = 0.02

    algorithm_name = "etrca"
    algorithm_version = ALGORITHM_VERSION

    def decode(self, values_uv: NDArray[np.float64]) -> DecoderResult:
        values = np.asarray(values_uv, dtype=np.float64)
        if values.ndim != 2 or values.shape[0] != len(self.model.channel_names):
            return rejected_result(self.model.frequencies_hz, "channel_mismatch")
        if not np.isfinite(values).all():
            return rejected_result(self.model.frequencies_hz, "non_finite_samples")
        sample_count = min(values.shape[1], self.model.templates_uv.shape[2])
        if sample_count < max(2, round(self.model.sample_rate_hz * 0.5)):
            return rejected_result(self.model.frequencies_hz, "window_too_short")
        ensemble_filters = self.model.spatial_filters
        projected = ensemble_filters @ values[:, :sample_count]
        scores = tuple(
            _correlation(projected, ensemble_filters @ template[:, :sample_count])
            for template in self.model.templates_uv
        )
        return build_decoder_result(
            self.model.frequencies_hz,
            scores,
            min_score=self.min_score,
            min_margin=self.min_margin,
        )

    def parameters(self) -> dict[str, object]:
        return {
            "algorithm": self.algorithm_name,
            "algorithm_version": self.algorithm_version,
            "calibrated_frequencies_hz": list(self.model.frequencies_hz),
            "channel_names": list(self.model.channel_names),
            "ensemble_filter_count": int(self.model.spatial_filters.shape[0]),
            "sample_count": int(self.model.templates_uv.shape[2]),
            "min_score": self.min_score,
            "min_margin": self.min_margin,
        }
