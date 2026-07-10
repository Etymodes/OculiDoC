"""Patient domain model tests."""

from datetime import UTC, date

import pytest

from oculidoc.domain import ClinicalDiagnosis, Patient, Sex


def test_patient_normalizes_text_fields() -> None:
    patient = Patient(
        patient_code=" DOC-001 ",
        family_name=" 王 ",
        sex=Sex.FEMALE,
        date_of_birth=date(1980, 7, 11),
        enrollment_date=date(2026, 7, 10),
        etiology=" TBI ",
        clinical_diagnosis=ClinicalDiagnosis.MCS_PLUS,
        notes=" 首次测试 ",
    )

    assert patient.patient_code == "DOC-001"
    assert patient.family_name == "王"
    assert patient.etiology == "TBI"
    assert patient.clinical_diagnosis is ClinicalDiagnosis.MCS_PLUS
    assert patient.notes == "首次测试"
    assert patient.display_label == "王患者（DOC-001）"
    assert patient.created_at.tzinfo is UTC
    assert patient.updated_at.tzinfo is UTC


def test_patient_age_is_calculated_from_birth_date() -> None:
    patient = Patient(
        patient_code="DOC-002",
        family_name="李",
        date_of_birth=date(1980, 7, 11),
        enrollment_date=date(2026, 7, 10),
    )

    assert patient.age_on(date(2026, 7, 10)) == 45
    assert patient.age_on(date(2026, 7, 11)) == 46


def test_patient_age_is_none_when_birth_date_is_unknown() -> None:
    patient = Patient(
        patient_code="DOC-003",
        family_name="张",
    )

    assert patient.age_on(date(2026, 7, 10)) is None


@pytest.mark.parametrize(
    ("patient_code", "family_name"),
    [
        ("", "王"),
        ("   ", "王"),
        ("DOC-004", ""),
        ("DOC-004", "   "),
    ],
)
def test_patient_rejects_missing_required_identity(
    patient_code: str,
    family_name: str,
) -> None:
    with pytest.raises(ValueError):
        Patient(
            patient_code=patient_code,
            family_name=family_name,
        )


def test_patient_rejects_birth_after_enrollment() -> None:
    with pytest.raises(
        ValueError,
        match="later than enrollment",
    ):
        Patient(
            patient_code="DOC-005",
            family_name="赵",
            date_of_birth=date(2026, 7, 11),
            enrollment_date=date(2026, 7, 10),
        )


def test_patient_can_be_deactivated_and_reactivated() -> None:
    patient = Patient(
        patient_code="DOC-006",
        family_name="陈",
    )

    original_updated_at = patient.updated_at

    patient.deactivate()

    assert patient.is_active is False
    assert patient.updated_at >= original_updated_at

    patient.activate()

    assert patient.is_active is True


def test_clinical_diagnosis_enum_values() -> None:
    assert ClinicalDiagnosis.UNKNOWN.value == "unknown"
    assert ClinicalDiagnosis.UWS.value == "uws"
    assert ClinicalDiagnosis.MCS_MINUS.value == "mcs_minus"
    assert ClinicalDiagnosis.MCS_PLUS.value == "mcs_plus"
    assert ClinicalDiagnosis.EMCS.value == "emcs"


def test_patient_defaults_to_unknown_diagnosis() -> None:
    patient = Patient(
        patient_code="DOC-007",
        family_name="?",
    )

    assert patient.clinical_diagnosis is ClinicalDiagnosis.UNKNOWN


def test_patient_normalizes_stored_diagnosis_string() -> None:
    patient = Patient(
        patient_code="DOC-008",
        family_name="?",
        clinical_diagnosis="uws",  # type: ignore[arg-type]
    )

    assert patient.clinical_diagnosis is ClinicalDiagnosis.UWS


def test_patient_normalizes_diagnosis_details() -> None:
    patient = Patient(
        patient_code="DOC-009",
        family_name="?",
        diagnosis_details=" ????? MCS+ ",
    )

    assert patient.diagnosis_details == "????? MCS+"


def test_empty_diagnosis_details_become_none() -> None:
    patient = Patient(
        patient_code="DOC-010",
        family_name="?",
        diagnosis_details="   ",
    )

    assert patient.diagnosis_details is None
