"""Patient audit database mapping functions."""

from datetime import UTC, datetime
from uuid import UUID

from oculidoc.domain.patient_audit import (
    PatientAuditAction,
    PatientAuditEvent,
)
from oculidoc.infrastructure.database.models import (
    PatientAuditRecord,
)


def _as_utc(value: datetime) -> datetime:
    """Interpret SQLite naive datetimes as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def audit_event_to_record(
    event: PatientAuditEvent,
) -> PatientAuditRecord:
    """Map one domain audit event to a database record."""
    return PatientAuditRecord(
        event_id=str(event.event_id),
        patient_id=str(event.patient_id),
        action=event.action.value,
        changed_fields=list(event.changed_fields),
        actor=event.actor,
        occurred_at=event.occurred_at,
    )


def record_to_audit_event(
    record: PatientAuditRecord,
) -> PatientAuditEvent:
    """Map one database record to a domain audit event."""
    return PatientAuditEvent(
        event_id=UUID(record.event_id),
        patient_id=UUID(record.patient_id),
        action=PatientAuditAction(record.action),
        changed_fields=tuple(record.changed_fields or []),
        actor=record.actor,
        occurred_at=_as_utc(record.occurred_at),
    )
