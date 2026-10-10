"""Named decoder construction without task-side branching."""

from __future__ import annotations

from collections.abc import Callable

from oculidoc.bci.ssvep.cca import ALGORITHM_VERSION as CCA_VERSION
from oculidoc.bci.ssvep.cca import CcaDecoder
from oculidoc.bci.ssvep.config import SsvepStimulusConfig
from oculidoc.bci.ssvep.etrca import ALGORITHM_VERSION as ETRCA_VERSION
from oculidoc.bci.ssvep.etrca import EtrcaDecoder
from oculidoc.bci.ssvep.fbcca import ALGORITHM_VERSION as FBCCA_VERSION
from oculidoc.bci.ssvep.fbcca import FbccaDecoder
from oculidoc.bci.ssvep.trca import ALGORITHM_VERSION as TRCA_VERSION
from oculidoc.bci.ssvep.trca import TrcaDecoder, TrcaModel


class DecoderRegistry:
    """Small explicit registry for the four v0.1.3 decoder families."""

    _training_free: dict[str, Callable[[SsvepStimulusConfig, float], object]] = {
        "cca": lambda stimulus, sample_rate: CcaDecoder(stimulus, sample_rate),
        "fbcca": lambda stimulus, sample_rate: FbccaDecoder(stimulus, sample_rate),
    }

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return ("cca", "fbcca", "trca", "etrca")

    @classmethod
    def versions(cls) -> dict[str, str]:
        return {
            "cca": CCA_VERSION,
            "fbcca": FBCCA_VERSION,
            "trca": TRCA_VERSION,
            "etrca": ETRCA_VERSION,
        }

    @classmethod
    def create(
        cls,
        name: str,
        *,
        stimulus: SsvepStimulusConfig,
        sample_rate_hz: float,
        model: TrcaModel | None = None,
    ) -> CcaDecoder | FbccaDecoder | TrcaDecoder | EtrcaDecoder:
        normalized = name.strip().casefold()
        factory = cls._training_free.get(normalized)
        if factory is not None:
            return factory(stimulus, sample_rate_hz)  # type: ignore[return-value]
        if model is None:
            raise ValueError(f"Decoder {normalized} requires a patient calibration model.")
        if tuple(model.frequencies_hz) != stimulus.frequencies_hz:
            raise ValueError("Decoder model frequencies do not match the stimulus configuration.")
        if model.sample_rate_hz != sample_rate_hz:
            raise ValueError("Decoder model sample rate does not match the signal source.")
        if normalized == "trca":
            return TrcaDecoder(model)
        if normalized == "etrca":
            return EtrcaDecoder(model)
        raise ValueError(f"Unsupported SSVEP decoder: {name}")
