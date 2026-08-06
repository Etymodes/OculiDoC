"""Conservative, scale-tolerant EEG quality gates used before decoding."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from oculidoc.signals.models import EEGSampleBlock


@dataclass(frozen=True, slots=True)
class SignalQualityAssessment:
    usable: bool
    reasons: tuple[str, ...]
    channel_metrics: dict[str, dict[str, float]]

    def to_dict(self) -> dict[str, object]:
        return {
            "usable": self.usable,
            "reasons": list(self.reasons),
            "channel_metrics": self.channel_metrics,
        }


def assess_eeg_quality(block: EEGSampleBlock) -> SignalQualityAssessment:
    """Reject incomplete, flat, or device-reported poor blocks.

    The gate avoids absolute-amplitude thresholds because the Tieying evidence
    does not establish the hardware's microvolt conversion. That conversion is
    tracked separately in the task configuration and report.
    """

    reasons: list[str] = []
    metrics: dict[str, dict[str, float]] = {}
    usable_channels = 0
    device_good_channels = 0
    for index, channel_name in enumerate(block.channel_names):
        values = block.values_uv[index]
        finite = values[np.isfinite(values)]
        finite_ratio = float(len(finite) / len(values))
        deviation = float(np.std(finite)) if len(finite) else 0.0
        dynamic_ratio = (
            float(len(np.unique(np.round(finite, decimals=9))) / len(finite))
            if len(finite)
            else 0.0
        )
        device_quality = float(block.quality.get(channel_name, 1.0))
        channel_usable = finite_ratio >= 0.98 and deviation > 1e-9 and dynamic_ratio >= 0.02
        usable_channels += int(channel_usable)
        device_good_channels += int(device_quality >= 1.0 / 3.0)
        metrics[channel_name] = {
            "valid_sample_ratio": finite_ratio,
            "standard_deviation_scaled_uv": deviation,
            "dynamic_sample_ratio": dynamic_ratio,
            "device_quality": device_quality,
        }
    minimum_channels = max(1, (len(block.channel_names) + 1) // 2)
    if usable_channels < minimum_channels:
        reasons.append("insufficient_non_flat_channels")
    if block.quality and device_good_channels < minimum_channels:
        reasons.append("device_contact_quality_too_low")
    if block.sample_count < max(2, round(block.sample_rate_hz * 0.5)):
        reasons.append("window_too_short")
    return SignalQualityAssessment(not reasons, tuple(reasons), metrics)
