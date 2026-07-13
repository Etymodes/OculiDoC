"""Patient domain and ORM mapping tests."""

from datetime import UTC, date, datetime
from uuid import UUID

from oculidoc.domain import ClinicalDiagnosis, Patient, Sex
from oculidoc.infrastructure.database.mappers import (
    patient_to_record,
    record_to_patient,
)
from oculidoc.infrastructure.database.models import PatientRecord


def test_patient_to_record_copies_domain_fields() -> None:
    patient_id = UUID("89fa89c5-0b18-430b-9338-19381d3141e5")
    created_at = datetime(2026, 7, 13, 8, 30, tzinfo=UTC)
    updated_at = datetime(2026, 7, 13, 9, 45, tzinfo=UTC)

    patient = Patient(
        patient_id=patient_id,
        patient_code="DOC-MAP-001",
        family_name="??",
        sex=Sex.FEMALE,
        date_of_birth=date(1980, 1, 2),
        etiology="TBI",
        clinical_diagnosis=ClinicalDiagnosis.MCS_PLUS,
        diagnosis_details="??????",
        enrollment_date=date(2026, 7, 13),
        notes="????",
        is_active=False,
        created_at=created_at,
        updated_at=updated_at,
    )

    record = patient_to_record(patient)

    assert record.patient_id == str(patient_id)
    assert record.patient_code == "DOC-MAP-001"
    assert record.family_name == "??"
    assert record.sex == Sex.FEMALE.value
    assert record.date_of_birth == date(1980, 1, 2)
    assert record.etiology == "TBI"
    assert record.clinical_diagnosis == ClinicalDiagnosis.MCS_PLUS.value
    assert record.diagnosis_details == "??????"
    assert record.enrollment_date == date(2026, 7, 13)
    assert record.notes == "????"
    assert record.is_active is False
    assert record.created_at == created_at
    assert record.updated_at == updated_at


def test_record_to_patient_restores_domain_types() -> None:
    patient_id = "3dc61ff8-fc6c-455b-a614-8b7d96899f4b"

    record = PatientRecord(
        patient_id=patient_id,
        patient_code="DOC-MAP-002",
        family_name="??",
        sex=Sex.MALE.value,
        date_of_birth=date(1975, 5, 6),
        etiology="Hypoxic-ischemic injury",
        clinical_diagnosis=ClinicalDiagnosis.UWS.value,
        diagnosis_details=None,
        enrollment_date=date(2026, 7, 13),
        notes="",
        is_active=True,
        created_at=datetime(2026, 7, 13, 10, 0),
        updated_at=datetime(2026, 7, 13, 10, 5),
    )

    patient = record_to_patient(record)

    assert patient.patient_id == UUID(patient_id)
    assert patient.sex is Sex.MALE
    assert patient.clinical_diagnosis is ClinicalDiagnosis.UWS
    assert patient.created_at.tzinfo is UTC
    assert patient.updated_at.tzinfo is UTC
    assert patient.is_active is True


def test_patient_mapping_round_trip_preserves_identity() -> None:
    patient = Patient(
        patient_code="DOC-MAP-003",
        family_name="??",
        clinical_diagnosis=ClinicalDiagnosis.EMCS,
        diagnosis_details="??????",
    )

    restored_patient = record_to_patient(patient_to_record(patient))

    assert restored_patient.patient_id == patient.patient_id
    assert restored_patient.patient_code == patient.patient_code
    assert restored_patient.family_name == patient.family_name
    assert restored_patient.clinical_diagnosis is ClinicalDiagnosis.EMCS
    assert restored_patient.diagnosis_details == "??????"
