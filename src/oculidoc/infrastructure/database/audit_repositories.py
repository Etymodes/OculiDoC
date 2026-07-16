"""SQLite patient audit repository."""

from uuid import UUID

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session, sessionmaker

from oculidoc.domain.patient_audit import PatientAuditEvent
from oculidoc.infrastructure.database.audit_mappers import (
    audit_event_to_record,
    record_to_audit_event,
)
from oculidoc.infrastructure.database.models import (
    PatientAuditRecord,
)


class SQLitePatientAuditRepository:
    """Persist immutable patient audit events in SQLite."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._session_factory = session_factory

    def add(
        self,
        event: PatientAuditEvent,
    ) -> PatientAuditEvent:
        """Persist one audit event."""
        with self._session_factory() as session:
            record = audit_event_to_record(event)
            session.add(record)
            session.commit()
            session.refresh(record)

            return record_to_audit_event(record)

    def list_for_patient(
        self,
        patient_id: UUID,
        *,
        limit: int = 50,
    ) -> list[PatientAuditEvent]:
        """Return newest audit events first."""
        normalized_limit = max(1, limit)

        statement = (
            select(PatientAuditRecord)
            .where(PatientAuditRecord.patient_id == str(patient_id))
            .order_by(
                PatientAuditRecord.occurred_at.desc(),
                # UUID values do not encode insertion order.
                # SQLite rowid provides a deterministic
                # newest-first tie-breaker for immutable
                # audit records with equal timestamps.
                literal_column("rowid").desc(),
            )
            .limit(normalized_limit)
        )

        with self._session_factory() as session:
            records = session.scalars(statement).all()

            return [record_to_audit_event(record) for record in records]
