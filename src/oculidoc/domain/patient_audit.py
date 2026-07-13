"""Patient record audit domain objects."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class PatientAuditAction(StrEnum):
    """Patient record operations that require an audit trail."""

    REGISTERED = "registered"
    UPDATED = "updated"
    DEACTIVATED = "deactivated"
    ACTIVATED = "activated"


@dataclass(frozen=True, slots=True)
class PatientAuditEvent:
    """One immutable patient-record audit event."""

    patient_id: UUID
    action: PatientAuditAction
    changed_fields: tuple[str, ...] = ()
    actor: str = "local_admin"
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
