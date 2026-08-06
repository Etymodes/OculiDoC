"""Patient-calibrated task-related component analysis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from oculidoc.bci.ssvep.evaluation import (
    DecoderResult,
    build_decoder_result,
    rejected_result,
)

ALGORITHM_VERSION = "trca-1.0"


def _correlation(x: NDArray[np.float64], y: NDArray[np.float64]) -> float:
    x = np.asarray(x, dtype=np.float64).ravel()
    y = np.asarray(y, dtype=np.float64).ravel()
    x = x - x.mean()
    y = y - y.mean()
    denominator = np.linalg.norm(x) * np.linalg.norm(y)
    return float(x @ y / denominator) if denominator > 0 else 0.0


@dataclass(frozen=True, slots=True)
class TrcaModel:
    frequencies_hz: tuple[float, ...]
    sample_rate_hz: float
    channel_names: tuple[str, ...]
    templates_uv: NDArray[np.float64]
    spatial_filters: NDArray[np.float64]

    def __post_init__(self) -> None:
        templates = np.asarray(self.templates_uv, dtype=np.float64)
        filters = np.asarray(self.spatial_filters, dtype=np.float64)
        target_count = len(self.frequencies_hz)
        if templates.ndim != 3 or templates.shape[0] != target_count:
            raise ValueError("TRCA templates must be targets x channels x samples.")
        if filters.shape != (target_count, len(self.channel_names)):
            raise ValueError("TRCA filters must be targets x channels.")
        if templates.shape[1] != len(self.channel_names):
            raise ValueError("TRCA channel metadata does not match templates.")
        templates = templates.copy()
        filters = filters.copy()
        templates.setflags(write=False)
        filters.setflags(write=False)
        object.__setattr__(self, "templates_uv", templates)
        object.__setattr__(self, "spatial_filters", filters)

    @classmethod
    def fit(
        cls,
        trials_by_frequency: Mapping[float, NDArray[np.float64]],
        *,
        sample_rate_hz: float,
        channel_names: tuple[str, ...],
        regularization: float = 1e-6,
    ) -> TrcaModel:
        if not trials_by_frequency:
            raise ValueError("TRCA calibration requires labeled trials.")
        frequencies = tuple(sorted(float(value) for value in trials_by_frequency))
        templates = []
        filters = []
        expected_shape: tuple[int, int] | None = None
        for frequency in frequencies:
            trials = np.asarray(trials_by_frequency[frequency], dtype=np.float64)
            if trials.ndim != 3 or trials.shape[0] < 2:
                raise ValueError("TRCA requires at least two trials per frequency.")
            if not np.isfinite(trials).all():
                raise ValueError("TRCA calibration trials must be finite.")
            if expected_shape is None:
                expected_shape = trials.shape[1:]
            if trials.shape[1:] != expected_shape or trials.shape[1] != len(channel_names):
                raise ValueError("TRCA calibration trials must share channel and sample shape.")
            centered = trials - trials.mean(axis=2, keepdims=True)
            q = sum(
                (trial @ trial.T for trial in centered),
                start=np.zeros((trials.shape[1], trials.shape[1])),
            )
            summed = centered.sum(axis=0)
            s = summed @ summed.T - q
            scale = max(float(np.trace(q)) / max(1, q.shape[0]), 1.0)
            eigenvalues, eigenvectors = np.linalg.eig(
                np.linalg.pinv(q + np.eye(q.shape[0]) * regularization * scale) @ s
            )
            spatial_filter = np.real(eigenvectors[:, int(np.argmax(np.real(eigenvalues)))])
            norm = np.linalg.norm(spatial_filter)
            filters.append(spatial_filter / norm if norm else spatial_filter)
            templates.append(trials.mean(axis=0))
        return cls(
            frequencies_hz=frequencies,
            sample_rate_hz=sample_rate_hz,
            channel_names=channel_names,
            templates_uv=np.asarray(templates, dtype=np.float64),
            spatial_filters=np.asarray(filters, dtype=np.float64),
        )


@dataclass(frozen=True, slots=True)
class TrcaDecoder:
    model: TrcaModel
    min_score: float = 0.1
    min_margin: float = 0.02

    algorithm_name = "trca"
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
        scores = tuple(
            _correlation(
                spatial_filter @ values[:, :sample_count],
                spatial_filter @ template[:, :sample_count],
            )
            for spatial_filter, template in zip(
                self.model.spatial_filters,
                self.model.templates_uv,
                strict=True,
            )
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
            "sample_count": int(self.model.templates_uv.shape[2]),
            "min_score": self.min_score,
            "min_margin": self.min_margin,
        }
