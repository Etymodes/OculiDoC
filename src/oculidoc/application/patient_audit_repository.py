"""Patient audit repository application port."""

from typing import Protocol
from uuid import UUID

from oculidoc.domain.patient_audit import PatientAuditEvent


class PatientAuditRepository(Protocol):
    """Storage operations required for patient audit events."""

    def add(
        self,
        event: PatientAuditEvent,
    ) -> PatientAuditEvent:
        """Persist one audit event."""
        ...

    def list_for_patient(
        self,
        patient_id: UUID,
        *,
        limit: int = 50,
    ) -> list[PatientAuditEvent]:
        """Return recent events for one patient."""
        ...
