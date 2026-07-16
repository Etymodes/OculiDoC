"""Patient update application-service tests."""

import pytest

from oculidoc.application import (
    DuplicatePatientCodeError,
    RegisterPatientRequest,
    UpdatePatientRequest,
)
from oculidoc.domain import ClinicalDiagnosis, Sex
from oculidoc.infrastructure.database import initialize_database


def test_update_patient_preserves_identity_and_status() -> None:
    runtime = initialize_database(":memory:")

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-UPDATE-001",
            family_name="\u65e7",
        )
    )
    runtime.patient_service.deactivate_patient(patient.patient_id)

    updated = runtime.patient_service.update_patient(
        UpdatePatientRequest(
            patient_id=patient.patient_id,
            patient_code=" DOC-UPDATE-002 ",
            family_name="\u65b0",
            sex=Sex.FEMALE,
            etiology="TBI",
            clinical_diagnosis=(ClinicalDiagnosis.MCS_PLUS),
            diagnosis_details="\u66f4\u65b0",
        )
    )

    assert updated.patient_id == patient.patient_id
    assert updated.created_at == patient.created_at
    assert updated.is_active is False
    assert updated.patient_code == "DOC-UPDATE-002"
    assert updated.family_name == "\u65b0"
    assert updated.sex is Sex.FEMALE
    assert updated.etiology == "TBI"
    assert updated.clinical_diagnosis is ClinicalDiagnosis.MCS_PLUS
    assert updated.diagnosis_details == "\u66f4\u65b0"

    runtime.dispose()


def test_update_patient_rejects_another_patient_code() -> None:
    runtime = initialize_database(":memory:")

    first = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-DUP-001",
            family_name="\u7532",
        )
    )
    runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-DUP-002",
            family_name="\u4e59",
        )
    )

    with pytest.raises(
        DuplicatePatientCodeError,
        match="DOC-DUP-002",
    ):
        runtime.patient_service.update_patient(
            UpdatePatientRequest(
                patient_id=first.patient_id,
                patient_code="DOC-DUP-002",
                family_name="\u7532",
            )
        )

    runtime.dispose()
