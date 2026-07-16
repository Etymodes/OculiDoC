"""Stable filesystem paths used by the application."""

import re
import sys
from pathlib import Path

UNASSIGNED_PATIENT_KEY = "unassigned"

_PATIENT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def application_root() -> Path:
    """Return the packaged-application or project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parents[2]


def application_data_directory(
    *,
    create: bool = True,
) -> Path:
    """Return the application-owned data directory."""
    path = application_root() / "data"

    if create:
        path.mkdir(
            parents=True,
            exist_ok=True,
        )

    return path


def normalize_patient_key(
    patient_key: str,
) -> str:
    """Validate an opaque internal patient identifier."""
    normalized = patient_key.strip()

    if not normalized:
        raise ValueError("patient_key cannot be empty.")

    if not _PATIENT_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(
            "patient_key may contain only letters, numbers, dots, underscores, and hyphens."
        )

    if normalized in {".", ".."}:
        raise ValueError("patient_key cannot be a relative path.")

    return normalized


def patients_data_directory(
    *,
    create: bool = True,
) -> Path:
    """Return the root directory for patient archives."""
    path = application_data_directory(create=create) / "patients"

    if create:
        path.mkdir(
            parents=True,
            exist_ok=True,
        )

    return path


def patient_data_directory(
    patient_key: str,
    *,
    create: bool = True,
) -> Path:
    """Return one patient's application-owned directory."""
    normalized_key = normalize_patient_key(patient_key)
    path = patients_data_directory(create=create) / normalized_key

    if create:
        path.mkdir(
            parents=True,
            exist_ok=True,
        )

    return path


def eye_observation_dataset_directory(
    patient_key: str = UNASSIGNED_PATIENT_KEY,
    *,
    create: bool = True,
) -> Path:
    """Return one patient's eye-observation directory."""
    path = (
        patient_data_directory(
            patient_key,
            create=create,
        )
        / "eye_observations"
    )

    if create:
        path.mkdir(
            parents=True,
            exist_ok=True,
        )

    return path
