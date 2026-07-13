"""Patient application service tests."""

from uuid import UUID

import pytest

from oculidoc.application import (
    DuplicatePatientCodeError,
    PatientNotFoundError,
    PatientRepository,
    PatientService,
    RegisterPatientRequest,
)
from oculidoc.domain import ClinicalDiagnosis, Patient, Sex


class FakePatientRepository:
    """In-memory repository used to isolate application tests."""

    def __init__(self) -> None:
        self.patients: dict[UUID, Patient] = {}

    def add(self, patient: Patient) -> Patient:
        self.patients[patient.patient_id] = patient
        return patient

    def get(self, patient_id: UUID) -> Patient | None:
        return self.patients.get(patient_id)

    def get_by_code(
        self,
        patient_code: str,
    ) -> Patient | None:
        normalized_code = patient_code.strip()

        return next(
            (
                patient
                for patient in self.patients.values()
                if patient.patient_code == normalized_code
            ),
            None,
        )

    def list_all(
        self,
        *,
        active_only: bool = False,
    ) -> list[Patient]:
        patients = sorted(
            self.patients.values(),
            key=lambda patient: patient.patient_code,
        )

        if active_only:
            return [patient for patient in patients if patient.is_active]

        return patients

    def update(self, patient: Patient) -> Patient:
        if patient.patient_id not in self.patients:
            raise KeyError(patient.patient_id)

        self.patients[patient.patient_id] = patient
        return patient


def create_service() -> tuple[
    PatientService,
    FakePatientRepository,
]:
    repository = FakePatientRepository()
    service = PatientService(repository)

    return service, repository


def test_fake_repository_satisfies_protocol() -> None:
    repository = FakePatientRepository()

    assert isinstance(repository, PatientRepository)


def test_service_registers_patient() -> None:
    service, repository = create_service()

    patient = service.register_patient(
        RegisterPatientRequest(
            patient_code=" DOC-SVC-001 ",
            family_name=" ?? ",
            sex=Sex.FEMALE,
            etiology=" TBI ",
            clinical_diagnosis=ClinicalDiagnosis.MCS_PLUS,
            diagnosis_details=" ???? ",
        )
    )

    assert patient.patient_code == "DOC-SVC-001"
    assert patient.family_name == "??"
    assert patient.sex is Sex.FEMALE
    assert patient.etiology == "TBI"
    assert patient.clinical_diagnosis is ClinicalDiagnosis.MCS_PLUS
    assert patient.diagnosis_details == "????"
    assert repository.get(patient.patient_id) is patient


def test_service_rejects_duplicate_patient_code() -> None:
    service, _ = create_service()

    service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-SVC-002",
            family_name="??",
        )
    )

    with pytest.raises(
        DuplicatePatientCodeError,
        match="DOC-SVC-002",
    ):
        service.register_patient(
            RegisterPatientRequest(
                patient_code=" DOC-SVC-002 ",
                family_name="??",
            )
        )


def test_service_rejects_duplicate_code_when_inactive() -> None:
    service, _ = create_service()

    patient = service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-SVC-003",
            family_name="??",
        )
    )
    service.deactivate_patient(patient.patient_id)

    with pytest.raises(DuplicatePatientCodeError):
        service.register_patient(
            RegisterPatientRequest(
                patient_code="DOC-SVC-003",
                family_name="????",
            )
        )


def test_service_lists_only_active_patients() -> None:
    service, _ = create_service()

    active_patient = service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-SVC-004",
            family_name="??",
        )
    )
    inactive_patient = service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-SVC-005",
            family_name="??",
        )
    )

    service.deactivate_patient(inactive_patient.patient_id)

    patients = service.list_patients(active_only=True)

    assert [patient.patient_id for patient in patients] == [active_patient.patient_id]


def test_service_deactivates_and_reactivates_patient() -> None:
    service, _ = create_service()

    patient = service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-SVC-006",
            family_name="??",
        )
    )

    deactivated = service.deactivate_patient(patient.patient_id)
    assert deactivated.is_active is False

    activated = service.activate_patient(patient.patient_id)
    assert activated.is_active is True


def test_service_raises_for_unknown_patient() -> None:
    service, _ = create_service()

    missing_id = UUID("22222222-2222-2222-2222-222222222222")

    with pytest.raises(
        PatientNotFoundError,
        match="Patient not found",
    ):
        service.get_patient(missing_id)

    with pytest.raises(PatientNotFoundError):
        service.deactivate_patient(missing_id)
