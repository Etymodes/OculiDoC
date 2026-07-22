"""Versioned JSON transfer for patient demographics without session artifacts."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from oculidoc.application.patient_service import PatientService, RegisterPatientRequest
from oculidoc.domain import ClinicalDiagnosis, Patient, Sex

PATIENT_TRANSFER_SCHEMA = "1.0"


@dataclass(frozen=True, slots=True)
class PatientImportRecord:
    request: RegisterPatientRequest
    is_active: bool


@dataclass(frozen=True, slots=True)
class PatientImportSummary:
    imported_count: int
    skipped_duplicate_count: int


def patient_transfer_document(patients: list[Patient]) -> dict[str, object]:
    return {
        "schema_version": PATIENT_TRANSFER_SCHEMA,
        "exported_at_utc": datetime.now(UTC).isoformat(),
        "contains_session_data": False,
        "patients": [
            {
                "patient_code": patient.patient_code,
                "family_name": patient.family_name,
                "sex": patient.sex.value,
                "date_of_birth": (
                    patient.date_of_birth.isoformat() if patient.date_of_birth is not None else None
                ),
                "etiology": patient.etiology,
                "clinical_diagnosis": patient.clinical_diagnosis.value,
                "diagnosis_details": patient.diagnosis_details,
                "enrollment_date": patient.enrollment_date.isoformat(),
                "notes": patient.notes,
                "is_active": patient.is_active,
            }
            for patient in patients
        ],
    }


def write_patient_transfer(path: str | Path, patients: list[Patient]) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = patient_transfer_document(patients)

    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())

    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    return destination


def _optional_date(value: object, name: str) -> date | None:
    if value is None or value == "":
        return None

    if not isinstance(value, str):
        raise TypeError(f"{name} must be an ISO date or null.")

    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date.") from error


def read_patient_transfer(path: str | Path) -> tuple[PatientImportRecord, ...]:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))

    if not isinstance(payload, dict) or payload.get("schema_version") != PATIENT_TRANSFER_SCHEMA:
        raise ValueError("患者资料文件版本不受支持。")

    raw_patients = payload.get("patients")

    if not isinstance(raw_patients, list):
        raise TypeError("患者资料文件缺少 patients 列表。")

    if len(raw_patients) > 10_000:
        raise ValueError("患者资料文件记录过多。")

    records: list[PatientImportRecord] = []

    for index, value in enumerate(raw_patients, start=1):
        if not isinstance(value, dict):
            raise TypeError(f"第 {index} 条患者资料不是对象。")

        active = value.get("is_active", True)

        if not isinstance(active, bool):
            raise TypeError(f"第 {index} 条患者资料的 is_active 必须是布尔值。")

        enrollment_date = _optional_date(value.get("enrollment_date"), "enrollment_date")

        if enrollment_date is None:
            raise ValueError(f"第 {index} 条患者资料缺少 enrollment_date。")

        request = RegisterPatientRequest(
            patient_code=str(value.get("patient_code", "")),
            family_name=str(value.get("family_name", "")),
            sex=Sex(str(value.get("sex", Sex.UNKNOWN.value))),
            date_of_birth=_optional_date(value.get("date_of_birth"), "date_of_birth"),
            etiology=(str(value["etiology"]) if value.get("etiology") is not None else None),
            clinical_diagnosis=ClinicalDiagnosis(
                str(value.get("clinical_diagnosis", ClinicalDiagnosis.UNKNOWN.value))
            ),
            diagnosis_details=(
                str(value["diagnosis_details"])
                if value.get("diagnosis_details") is not None
                else None
            ),
            enrollment_date=enrollment_date,
            notes=str(value.get("notes", "")),
        )
        Patient(
            patient_code=request.patient_code,
            family_name=request.family_name,
            sex=request.sex,
            date_of_birth=request.date_of_birth,
            etiology=request.etiology,
            clinical_diagnosis=request.clinical_diagnosis,
            diagnosis_details=request.diagnosis_details,
            enrollment_date=request.enrollment_date,
            notes=request.notes,
        )
        records.append(PatientImportRecord(request=request, is_active=active))

    return tuple(records)


def import_patient_records(
    patient_service: PatientService,
    records: tuple[PatientImportRecord, ...],
) -> PatientImportSummary:
    existing_codes = {
        patient.patient_code.casefold() for patient in patient_service.list_patients()
    }
    imported_count = 0
    skipped_count = 0

    for record in records:
        normalized_code = record.request.patient_code.strip()

        if normalized_code.casefold() in existing_codes:
            skipped_count += 1
            continue

        patient = patient_service.register_patient(record.request)

        if not record.is_active:
            patient_service.deactivate_patient(patient.patient_id)

        existing_codes.add(normalized_code.casefold())
        imported_count += 1

    return PatientImportSummary(imported_count, skipped_count)
