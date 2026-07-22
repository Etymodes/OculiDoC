"""Patient registration and management use cases."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

from oculidoc.application.patient_audit_repository import (
    PatientAuditRepository,
)
from oculidoc.application.patient_repository import (
    PatientRepository,
)
from oculidoc.domain import ClinicalDiagnosis, Patient, Sex
from oculidoc.domain.patient_audit import (
    PatientAuditAction,
    PatientAuditEvent,
)


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


_EDITABLE_FIELDS = (
    "patient_code",
    "family_name",
    "sex",
    "date_of_birth",
    "etiology",
    "clinical_diagnosis",
    "diagnosis_details",
    "enrollment_date",
    "notes",
)


class PatientService:
    """Coordinate patient use cases independently of UI and storage."""

    def __init__(
        self,
        repository: PatientRepository,
        audit_repository: PatientAuditRepository | None = None,
        *,
        actor: str = "local_admin",
    ) -> None:
        self._repository = repository
        self._audit_repository = audit_repository
        self._actor = actor

    def _record_audit(
        self,
        patient_id: UUID,
        action: PatientAuditAction,
        changed_fields: tuple[str, ...] = (),
    ) -> None:
        """Persist an audit event when audit storage is available."""
        if self._audit_repository is None:
            return

        self._audit_repository.add(
            PatientAuditEvent(
                patient_id=patient_id,
                action=action,
                changed_fields=changed_fields,
                actor=self._actor,
            )
        )

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

        saved_patient = self._repository.add(patient)

        self._record_audit(
            saved_patient.patient_id,
            PatientAuditAction.REGISTERED,
            _EDITABLE_FIELDS,
        )

        return saved_patient

    def restore_patient(self, patient: Patient) -> Patient:
        """Restore one validated transfer record while preserving its identity."""
        existing_code = self._repository.get_by_code(patient.patient_code)
        existing_id = self._repository.get(patient.patient_id)

        if existing_code is not None or existing_id is not None:
            raise DuplicatePatientCodeError(f"Patient already exists: {patient.patient_code}")

        saved_patient = self._repository.add(patient)
        self._record_audit(
            saved_patient.patient_id,
            PatientAuditAction.REGISTERED,
            _EDITABLE_FIELDS,
        )
        return saved_patient

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

        changed_fields = tuple(
            field_name
            for field_name in _EDITABLE_FIELDS
            if getattr(current_patient, field_name) != getattr(updated_patient, field_name)
        )

        saved_patient = self._repository.update(updated_patient)

        if changed_fields:
            self._record_audit(
                saved_patient.patient_id,
                PatientAuditAction.UPDATED,
                changed_fields,
            )

        return saved_patient

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

    def list_patient_audit(
        self,
        patient_id: UUID,
        *,
        limit: int = 20,
    ) -> list[PatientAuditEvent]:
        """Return recent audit events for one patient."""
        self.get_patient(patient_id)

        if self._audit_repository is None:
            return []

        return self._audit_repository.list_for_patient(
            patient_id,
            limit=limit,
        )

    def deactivate_patient(
        self,
        patient_id: UUID,
    ) -> Patient:
        """Deactivate a patient while preserving historical data."""
        patient = self.get_patient(patient_id)
        was_active = patient.is_active
        patient.deactivate()

        saved_patient = self._repository.update(patient)

        if was_active:
            self._record_audit(
                saved_patient.patient_id,
                PatientAuditAction.DEACTIVATED,
                ("is_active",),
            )

        return saved_patient

    def activate_patient(
        self,
        patient_id: UUID,
    ) -> Patient:
        """Reactivate a previously deactivated patient."""
        patient = self.get_patient(patient_id)
        was_active = patient.is_active
        patient.activate()

        saved_patient = self._repository.update(patient)

        if not was_active:
            self._record_audit(
                saved_patient.patient_id,
                PatientAuditAction.ACTIVATED,
                ("is_active",),
            )

        return saved_patient
