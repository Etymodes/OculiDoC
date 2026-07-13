"""Patient registration and management use cases."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

from oculidoc.application.patient_repository import PatientRepository
from oculidoc.domain import ClinicalDiagnosis, Patient, Sex


class DuplicatePatientCodeError(ValueError):
    """Raised when an anonymous patient code already exists."""


class PatientNotFoundError(LookupError):
    """Raised when a requested patient cannot be found."""


@dataclass(frozen=True, slots=True)
class RegisterPatientRequest:
    """Input data for registering one patient."""

    patient_code: str
    family_name: str
    sex: Sex = Sex.UNKNOWN
    date_of_birth: date | None = None
    etiology: str | None = None
    clinical_diagnosis: ClinicalDiagnosis = ClinicalDiagnosis.UNKNOWN
    diagnosis_details: str | None = None
    enrollment_date: date = field(default_factory=date.today)
    notes: str = ""


@dataclass(frozen=True, slots=True)
class UpdatePatientRequest:
    """Editable data for one existing patient."""

    patient_id: UUID
    patient_code: str
    family_name: str
    sex: Sex = Sex.UNKNOWN
    date_of_birth: date | None = None
    etiology: str | None = None
    clinical_diagnosis: ClinicalDiagnosis = ClinicalDiagnosis.UNKNOWN
    diagnosis_details: str | None = None
    enrollment_date: date = field(default_factory=date.today)
    notes: str = ""


class PatientService:
    """Coordinate patient use cases independently of UI and storage."""

    def __init__(
        self,
        repository: PatientRepository,
    ) -> None:
        self._repository = repository

    def register_patient(
        self,
        request: RegisterPatientRequest,
    ) -> Patient:
        """Register a patient after checking the anonymous code."""
        normalized_code = request.patient_code.strip()

        existing_patient = self._repository.get_by_code(normalized_code)

        if existing_patient is not None:
            raise DuplicatePatientCodeError(f"Patient code already exists: {normalized_code}")

        patient = Patient(
            patient_code=normalized_code,
            family_name=request.family_name,
            sex=request.sex,
            date_of_birth=request.date_of_birth,
            etiology=request.etiology,
            clinical_diagnosis=request.clinical_diagnosis,
            diagnosis_details=request.diagnosis_details,
            enrollment_date=request.enrollment_date,
            notes=request.notes,
        )

        return self._repository.add(patient)

    def update_patient(
        self,
        request: UpdatePatientRequest,
    ) -> Patient:
        """Validate and persist editable patient information."""
        current_patient = self.get_patient(request.patient_id)
        normalized_code = request.patient_code.strip()

        existing_patient = self._repository.get_by_code(normalized_code)

        if (
            existing_patient is not None
            and existing_patient.patient_id != current_patient.patient_id
        ):
            raise DuplicatePatientCodeError(f"Patient code already exists: {normalized_code}")

        updated_patient = Patient(
            patient_id=current_patient.patient_id,
            patient_code=normalized_code,
            family_name=request.family_name,
            sex=request.sex,
            date_of_birth=request.date_of_birth,
            etiology=request.etiology,
            clinical_diagnosis=request.clinical_diagnosis,
            diagnosis_details=request.diagnosis_details,
            enrollment_date=request.enrollment_date,
            notes=request.notes,
            is_active=current_patient.is_active,
            created_at=current_patient.created_at,
            updated_at=datetime.now(UTC),
        )

        return self._repository.update(updated_patient)

    def get_patient(self, patient_id: UUID) -> Patient:
        """Return one patient or raise a domain-facing error."""
        patient = self._repository.get(patient_id)

        if patient is None:
            raise PatientNotFoundError(f"Patient not found: {patient_id}")

        return patient

    def list_patients(
        self,
        *,
        active_only: bool = False,
    ) -> list[Patient]:
        """Return patients for the administrator interface."""
        return self._repository.list_all(active_only=active_only)

    def deactivate_patient(
        self,
        patient_id: UUID,
    ) -> Patient:
        """Deactivate a patient while preserving historical data."""
        patient = self.get_patient(patient_id)
        patient.deactivate()

        return self._repository.update(patient)

    def activate_patient(
        self,
        patient_id: UUID,
    ) -> Patient:
        """Reactivate a previously deactivated patient."""
        patient = self.get_patient(patient_id)
        patient.activate()

        return self._repository.update(patient)
