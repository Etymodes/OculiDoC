"""Guarded rolling TRCA adaptation using labeled calibration trials only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from oculidoc.bci.ssvep.evaluation import DecoderResult, classification_metrics
from oculidoc.bci.ssvep.trca import TrcaDecoder, TrcaModel

ADAPTATION_VERSION = "guarded-trca-1.0"


@dataclass(frozen=True, slots=True)
class AdaptationDecision:
    accepted: bool
    reason: str
    candidate_accuracy: float | None
    candidate_coverage: float | None
    baseline_accuracy: float | None
    baseline_coverage: float | None
    trials_per_frequency: int
    training_trials_per_frequency: int
    holdout_trials_per_frequency: int

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": ADAPTATION_VERSION,
            "accepted": self.accepted,
            "reason": self.reason,
            "candidate_accuracy": self.candidate_accuracy,
            "candidate_coverage": self.candidate_coverage,
            "baseline_accuracy": self.baseline_accuracy,
            "baseline_coverage": self.baseline_coverage,
            "trials_per_frequency": self.trials_per_frequency,
            "training_trials_per_frequency": self.training_trials_per_frequency,
            "holdout_trials_per_frequency": self.holdout_trials_per_frequency,
            "minimum_accuracy": 0.75,
            "minimum_coverage": 0.75,
            "maximum_accuracy_drop": 0.02,
            "label_policy": "operator_cued_calibration_only",
            "unlabeled_self_training": False,
        }


def _score_model(
    model: TrcaModel,
    holdout_by_frequency: Mapping[float, NDArray[np.float64]],
) -> tuple[float, float]:
    decoder = TrcaDecoder(model)
    expected: list[float] = []
    results: list[DecoderResult] = []
    for frequency in sorted(holdout_by_frequency):
        for trial in holdout_by_frequency[frequency]:
            expected.append(float(frequency))
            results.append(decoder.decode(trial))
    metrics = classification_metrics(tuple(expected), tuple(results))
    accuracy_value = metrics["accuracy"]
    accepted_value = metrics["accepted_count"]
    trial_value = metrics["trial_count"]
    if not isinstance(accepted_value, int) or not isinstance(trial_value, int):
        raise TypeError("SSVEP evaluation returned invalid trial counts.")
    if accuracy_value is not None and not isinstance(accuracy_value, (int, float)):
        raise TypeError("SSVEP evaluation returned an invalid accuracy.")
    accepted_count = accepted_value
    trial_count = trial_value
    return (
        float(accuracy_value) if accuracy_value is not None else 0.0,
        accepted_count / trial_count if trial_count else 0.0,
    )


def guarded_trca_adaptation(
    trials_by_frequency: Mapping[float, NDArray[np.float64]],
    *,
    sample_rate_hz: float,
    channel_names: tuple[str, ...],
    prior_model: TrcaModel | None = None,
) -> tuple[TrcaModel | None, AdaptationDecision]:
    """Train on chronological history and gate promotion on untouched holdout trials.

    The final holdout trial for every target is never fitted. A candidate is
    promoted only when accuracy and accepted coverage are both adequate and it
    does not materially regress against the prior patient model. Unlabeled task
    predictions are intentionally not accepted by this API.
    """

    if not trials_by_frequency:
        raise ValueError("Guarded adaptation requires labeled calibration trials.")
    trial_counts = {int(np.asarray(trials).shape[0]) for trials in trials_by_frequency.values()}
    if len(trial_counts) != 1:
        raise ValueError("Guarded adaptation requires balanced target trial counts.")
    trials_per_frequency = trial_counts.pop()
    if trials_per_frequency < 4:
        return None, AdaptationDecision(
            accepted=False,
            reason="insufficient_labeled_trials",
            candidate_accuracy=None,
            candidate_coverage=None,
            baseline_accuracy=None,
            baseline_coverage=None,
            trials_per_frequency=trials_per_frequency,
            training_trials_per_frequency=max(0, trials_per_frequency - 1),
            holdout_trials_per_frequency=1,
        )
    training = {
        float(frequency): np.asarray(trials, dtype=np.float64)[:-1]
        for frequency, trials in trials_by_frequency.items()
    }
    holdout = {
        float(frequency): np.asarray(trials, dtype=np.float64)[-1:]
        for frequency, trials in trials_by_frequency.items()
    }
    candidate = TrcaModel.fit(
        training,
        sample_rate_hz=sample_rate_hz,
        channel_names=channel_names,
    )
    candidate_accuracy, candidate_coverage = _score_model(candidate, holdout)
    baseline_accuracy: float | None = None
    baseline_coverage: float | None = None
    if prior_model is not None:
        compatible = (
            prior_model.channel_names == channel_names
            and prior_model.frequencies_hz == tuple(sorted(float(item) for item in training))
            and np.isclose(prior_model.sample_rate_hz, sample_rate_hz)
        )
        if not compatible:
            return None, AdaptationDecision(
                accepted=False,
                reason="prior_model_incompatible",
                candidate_accuracy=candidate_accuracy,
                candidate_coverage=candidate_coverage,
                baseline_accuracy=None,
                baseline_coverage=None,
                trials_per_frequency=trials_per_frequency,
                training_trials_per_frequency=trials_per_frequency - 1,
                holdout_trials_per_frequency=1,
            )
        baseline_accuracy, baseline_coverage = _score_model(prior_model, holdout)
    accepted = (
        candidate_accuracy >= 0.75
        and candidate_coverage >= 0.75
        and (baseline_accuracy is None or candidate_accuracy >= baseline_accuracy - 0.02)
        and (baseline_coverage is None or candidate_coverage >= baseline_coverage - 0.02)
    )
    if candidate_accuracy < 0.75:
        reason = "holdout_accuracy_below_threshold"
    elif candidate_coverage < 0.75:
        reason = "holdout_coverage_below_threshold"
    elif (
        baseline_accuracy is not None
        and candidate_accuracy < baseline_accuracy - 0.02
        or baseline_coverage is not None
        and candidate_coverage < baseline_coverage - 0.02
    ):
        reason = "candidate_regressed_against_prior_model"
    else:
        reason = "holdout_gate_passed"
    return (
        candidate if accepted else None,
        AdaptationDecision(
            accepted=accepted,
            reason=reason,
            candidate_accuracy=candidate_accuracy,
            candidate_coverage=candidate_coverage,
            baseline_accuracy=baseline_accuracy,
            baseline_coverage=baseline_coverage,
            trials_per_frequency=trials_per_frequency,
            training_trials_per_frequency=trials_per_frequency - 1,
            holdout_trials_per_frequency=1,
        ),
    )
