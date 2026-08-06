"""Decoder outputs and neutral evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class DecoderResult:
    target_index: int | None
    target_frequency_hz: float | None
    frequencies_hz: tuple[float, ...]
    scores: tuple[float, ...]
    probabilities: tuple[float, ...]
    confidence: float
    margin: float
    rejected: bool
    reject_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "target_index": self.target_index,
            "target_frequency_hz": self.target_frequency_hz,
            "frequencies_hz": list(self.frequencies_hz),
            "scores": list(self.scores),
            "probabilities": list(self.probabilities),
            "confidence": self.confidence,
            "margin": self.margin,
            "rejected": self.rejected,
            "reject_reason": self.reject_reason,
        }


def build_decoder_result(
    frequencies_hz: tuple[float, ...],
    scores: tuple[float, ...] | list[float] | np.ndarray,
    *,
    min_score: float,
    min_margin: float,
) -> DecoderResult:
    values = np.asarray(scores, dtype=np.float64)
    if values.ndim != 1 or len(values) != len(frequencies_hz) or not len(values):
        raise ValueError("Decoder scores must match the configured frequencies.")
    if not np.isfinite(values).all():
        return rejected_result(frequencies_hz, "non_finite_scores")
    order = np.argsort(values)
    selected = int(order[-1])
    confidence = float(values[selected])
    margin = float(confidence - values[order[-2]]) if len(values) > 1 else confidence
    shifted = (values - values.max()) / 0.08
    probabilities = np.exp(np.clip(shifted, -60.0, 0.0))
    probabilities /= probabilities.sum()
    reject_reason = None
    if confidence < min_score:
        reject_reason = "score_below_threshold"
    elif margin < min_margin:
        reject_reason = "margin_below_threshold"
    return DecoderResult(
        target_index=(None if reject_reason else selected),
        target_frequency_hz=(None if reject_reason else frequencies_hz[selected]),
        frequencies_hz=frequencies_hz,
        scores=tuple(float(value) for value in values),
        probabilities=tuple(float(value) for value in probabilities),
        confidence=confidence,
        margin=margin,
        rejected=reject_reason is not None,
        reject_reason=reject_reason,
    )


def rejected_result(
    frequencies_hz: tuple[float, ...],
    reason: str,
) -> DecoderResult:
    count = len(frequencies_hz)
    probability = 1.0 / count if count else 0.0
    return DecoderResult(
        target_index=None,
        target_frequency_hz=None,
        frequencies_hz=frequencies_hz,
        scores=(0.0,) * count,
        probabilities=(probability,) * count,
        confidence=0.0,
        margin=0.0,
        rejected=True,
        reject_reason=reason,
    )


def classification_metrics(
    expected_frequencies_hz: tuple[float, ...],
    results: tuple[DecoderResult, ...],
) -> dict[str, object]:
    if len(expected_frequencies_hz) != len(results):
        raise ValueError("Expected labels must match decoder result count.")
    accepted = [result for result in results if not result.rejected]
    correct = sum(
        not result.rejected and result.target_frequency_hz == expected
        for expected, result in zip(expected_frequencies_hz, results, strict=True)
    )
    return {
        "trial_count": len(results),
        "accepted_count": len(accepted),
        "rejected_count": len(results) - len(accepted),
        "accuracy": correct / len(results) if results else None,
        "accepted_accuracy": (
            sum(
                result.target_frequency_hz == expected
                for expected, result in zip(expected_frequencies_hz, results, strict=True)
                if not result.rejected
            )
            / len(accepted)
            if accepted
            else None
        ),
        "mean_confidence": (
            sum(result.confidence for result in results) / len(results) if results else None
        ),
    }
