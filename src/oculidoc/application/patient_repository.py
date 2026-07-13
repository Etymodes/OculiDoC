"""Patient repository application port."""

from typing import Protocol, runtime_checkable
from uuid import UUID

from oculidoc.domain import Patient


@runtime_checkable
class PatientRepository(Protocol):
    """Storage operations required by patient application services."""

    def add(self, patient: Patient) -> Patient:
        """Persist a new patient."""
        ...

    def get(self, patient_id: UUID) -> Patient | None:
        """Return a patient by UUID."""
        ...

    def get_by_code(
        self,
        patient_code: str,
    ) -> Patient | None:
        """Return a patient by anonymous patient code."""
        ...

    def list_all(
        self,
        *,
        active_only: bool = False,
    ) -> list[Patient]:
        """Return stored patients."""
        ...

    def update(self, patient: Patient) -> Patient:
        """Persist changes to an existing patient."""
        ...
