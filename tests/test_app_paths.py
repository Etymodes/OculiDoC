"""Application filesystem path tests."""

import pytest

from oculidoc.app_paths import (
    UNASSIGNED_PATIENT_KEY,
    application_data_directory,
    application_root,
    eye_observation_dataset_directory,
    normalize_patient_key,
    patient_data_directory,
    patients_data_directory,
)


def test_application_root_contains_project_files() -> None:
    root = application_root()

    assert root.is_absolute()
    assert (root / "pyproject.toml").exists()
    assert (root / "src" / "oculidoc").exists()


def test_application_data_directory_is_under_root() -> None:
    path = application_data_directory()

    assert path == application_root() / "data"
    assert path.is_dir()


def test_default_eye_dataset_uses_unassigned_patient() -> None:
    path = eye_observation_dataset_directory()

    assert path == (
        application_root() / "data" / "patients" / UNASSIGNED_PATIENT_KEY / "eye_observations"
    )


def test_patient_dataset_is_separated_by_key() -> None:
    path = eye_observation_dataset_directory("patient-0007")

    assert path == (application_root() / "data" / "patients" / "patient-0007" / "eye_observations")
    assert path.is_dir()


def test_patient_data_paths_are_nested_correctly() -> None:
    patients_root = patients_data_directory()
    patient_root = patient_data_directory("subject_A12")

    assert patients_root == (application_data_directory() / "patients")
    assert patient_root == (patients_root / "subject_A12")


@pytest.mark.parametrize(
    "patient_key",
    [
        "",
        "   ",
        "../patient",
        "patient/name",
        r"patient\name",
        "患者姓名",
    ],
)
def test_invalid_patient_keys_are_rejected(
    patient_key: str,
) -> None:
    with pytest.raises(ValueError):
        normalize_patient_key(patient_key)
