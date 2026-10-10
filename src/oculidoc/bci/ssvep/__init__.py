"""SSVEP stimulus definitions, decoders, calibration, and evaluation."""

from oculidoc.bci.ssvep.cca import CcaDecoder
from oculidoc.bci.ssvep.config import SsvepStimulusConfig, SsvepTarget
from oculidoc.bci.ssvep.etrca import EtrcaDecoder
from oculidoc.bci.ssvep.evaluation import DecoderResult
from oculidoc.bci.ssvep.fbcca import FbccaDecoder
from oculidoc.bci.ssvep.registry import DecoderRegistry
from oculidoc.bci.ssvep.trca import TrcaDecoder, TrcaModel

__all__ = [
    "CcaDecoder",
    "DecoderRegistry",
    "DecoderResult",
    "EtrcaDecoder",
    "FbccaDecoder",
    "SsvepStimulusConfig",
    "SsvepTarget",
    "TrcaDecoder",
    "TrcaModel",
]
