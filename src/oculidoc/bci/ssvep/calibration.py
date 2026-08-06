"""TRCA calibration dataset and auditable model storage."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from oculidoc.bci.ssvep.trca import ALGORITHM_VERSION, TrcaModel
from oculidoc.lan_control import utc_now_text


@dataclass(frozen=True, slots=True)
class CalibrationDataset:
    patient_id: str
    sample_rate_hz: float
    channel_names: tuple[str, ...]
    trials_by_frequency: Mapping[float, NDArray[np.float64]]
    simulated: bool = False

    def train_trca(self) -> TrcaModel:
        return TrcaModel.fit(
            self.trials_by_frequency,
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self.channel_names,
        )


def save_trca_model(
    path: str | Path,
    model: TrcaModel,
    *,
    patient_id: str,
    simulated: bool,
) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": "1.0",
        "algorithm": "trca",
        "algorithm_version": ALGORITHM_VERSION,
        "patient_id": patient_id.strip(),
        "sample_rate_hz": model.sample_rate_hz,
        "channel_names": list(model.channel_names),
        "frequencies_hz": list(model.frequencies_hz),
        "simulated": bool(simulated),
        "created_at_utc": utc_now_text(),
    }
    with target.open("wb") as stream:
        np.savez_compressed(
            stream,
            templates_uv=model.templates_uv,
            spatial_filters=model.spatial_filters,
            metadata=np.array(json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
        )
    return target


def load_trca_model(
    path: str | Path,
    *,
    expected_patient_id: str | None = None,
    allow_simulated: bool = False,
) -> tuple[TrcaModel, dict[str, object]]:
    target = Path(path).expanduser().resolve()
    try:
        with np.load(target, allow_pickle=False) as archive:
            metadata = json.loads(str(archive["metadata"].item()))
            templates = np.asarray(archive["templates_uv"], dtype=np.float64)
            filters = np.asarray(archive["spatial_filters"], dtype=np.float64)
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"TRCA calibration model is invalid: {target}") from error
    if not isinstance(metadata, dict) or metadata.get("schema_version") != "1.0":
        raise ValueError("Unsupported TRCA calibration schema.")
    if expected_patient_id is not None and metadata.get("patient_id") != expected_patient_id:
        raise ValueError("TRCA calibration model belongs to a different patient.")
    if metadata.get("simulated") is True and not allow_simulated:
        raise ValueError("Simulated TRCA models cannot be used for patient sessions.")
    model = TrcaModel(
        frequencies_hz=tuple(float(item) for item in metadata["frequencies_hz"]),
        sample_rate_hz=float(metadata["sample_rate_hz"]),
        channel_names=tuple(str(item) for item in metadata["channel_names"]),
        templates_uv=templates,
        spatial_filters=filters,
    )
    return model, metadata
