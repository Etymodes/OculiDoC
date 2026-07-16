"""Patient audit trail tests."""

from sqlalchemy import inspect

from oculidoc.application import (
    RegisterPatientRequest,
    UpdatePatientRequest,
)
from oculidoc.domain import ClinicalDiagnosis
from oculidoc.domain.patient_audit import PatientAuditAction
from oculidoc.infrastructure.database import initialize_database


def test_runtime_creates_patient_audit_table() -> None:
    runtime = initialize_database(":memory:")

    assert "patient_audit_events" in inspect(runtime.engine).get_table_names()

    runtime.dispose()


def test_patient_changes_create_audit_events() -> None:
    runtime = initialize_database(":memory:")

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-AUDIT-001",
            family_name="Initial",
        )
    )

    runtime.patient_service.update_patient(
        UpdatePatientRequest(
            patient_id=patient.patient_id,
            patient_code="DOC-AUDIT-002",
            family_name="Updated",
            clinical_diagnosis=(ClinicalDiagnosis.MCS_PLUS),
        )
    )
    runtime.patient_service.deactivate_patient(patient.patient_id)
    runtime.patient_service.activate_patient(patient.patient_id)

    events = runtime.patient_service.list_patient_audit(patient.patient_id)

    assert [event.action for event in events] == [
        PatientAuditAction.ACTIVATED,
        PatientAuditAction.DEACTIVATED,
        PatientAuditAction.UPDATED,
        PatientAuditAction.REGISTERED,
    ]

    assert "patient_code" in events[2].changed_fields
    assert "family_name" in events[2].changed_fields
    assert "clinical_diagnosis" in events[2].changed_fields

    runtime.dispose()


def test_no_change_update_adds_no_update_event() -> None:
    runtime = initialize_database(":memory:")

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-AUDIT-003",
            family_name="Stable",
        )
    )

    runtime.patient_service.update_patient(
        UpdatePatientRequest(
            patient_id=patient.patient_id,
            patient_code=patient.patient_code,
            family_name=patient.family_name,
            sex=patient.sex,
            date_of_birth=patient.date_of_birth,
            etiology=patient.etiology,
            clinical_diagnosis=(patient.clinical_diagnosis),
            diagnosis_details=(patient.diagnosis_details),
            enrollment_date=patient.enrollment_date,
            notes=patient.notes,
        )
    )

    events = runtime.patient_service.list_patient_audit(patient.patient_id)

    assert len(events) == 1
    assert events[0].action is PatientAuditAction.REGISTERED

    runtime.dispose()
