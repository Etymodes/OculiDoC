"""SQLite experiment session repositories."""

from uuid import UUID

from sqlalchemy import delete, literal_column, select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from oculidoc.domain.experiment_session import (
    ExperimentSession,
    SessionArtifact,
)
from oculidoc.infrastructure.database.experiment_session_mappers import (
    artifact_to_record,
    record_to_artifact,
    record_to_session,
    session_to_record,
)
from oculidoc.infrastructure.database.models import (
    ExperimentSessionRecord,
    SessionArtifactRecord,
)


class SQLiteExperimentSessionRepository:
    """Persist experiment sessions in SQLite."""

    def __init__(
        self,
        session_factory: sessionmaker[OrmSession],
    ) -> None:
        self._session_factory = session_factory

    def add(
        self,
        session: ExperimentSession,
    ) -> ExperimentSession:
        with self._session_factory() as database:
            record = session_to_record(session)
            database.add(record)
            database.commit()
            database.refresh(record)

            return record_to_session(record)

    def get(
        self,
        session_id: UUID,
    ) -> ExperimentSession | None:
        with self._session_factory() as database:
            record = database.get(
                ExperimentSessionRecord,
                str(session_id),
            )

            if record is None:
                return None

            return record_to_session(record)

    def list_for_patient(
        self,
        patient_id: UUID,
    ) -> list[ExperimentSession]:
        statement = (
            select(ExperimentSessionRecord)
            .where(ExperimentSessionRecord.patient_id == str(patient_id))
            .order_by(ExperimentSessionRecord.created_at.desc())
        )

        with self._session_factory() as database:
            records = database.scalars(statement).all()

            return [record_to_session(record) for record in records]

    def update(
        self,
        session: ExperimentSession,
    ) -> ExperimentSession:
        with self._session_factory() as database:
            record = database.get(
                ExperimentSessionRecord,
                str(session.session_id),
            )

            if record is None:
                raise KeyError(session.session_id)

            record.patient_id = str(session.patient_id)
            record.module_id = session.module_id
            record.status = session.status.value
            record.data_directory = session.data_directory
            record.schema_version = session.schema_version
            record.clock_origin_monotonic_ns = session.clock_origin_monotonic_ns
            record.clock_origin_utc = session.clock_origin_utc
            record.started_at = session.started_at
            record.ended_at = session.ended_at
            record.failure_reason = session.failure_reason
            record.created_at = session.created_at
            record.updated_at = session.updated_at

            database.commit()
            database.refresh(record)

            return record_to_session(record)

    def delete(
        self,
        session_id: UUID,
    ) -> None:
        """Delete one session and its artifact manifest atomically."""
        with self._session_factory() as database:
            record = database.get(
                ExperimentSessionRecord,
                str(session_id),
            )

            if record is None:
                raise KeyError(session_id)

            database.execute(
                delete(SessionArtifactRecord).where(
                    SessionArtifactRecord.session_id == str(session_id)
                )
            )
            database.delete(record)
            database.commit()


class SQLiteSessionArtifactRepository:
    """Persist session file-manifest entries in SQLite."""

    def __init__(
        self,
        session_factory: sessionmaker[OrmSession],
    ) -> None:
        self._session_factory = session_factory

    def add(
        self,
        artifact: SessionArtifact,
    ) -> SessionArtifact:
        with self._session_factory() as database:
            record = artifact_to_record(artifact)
            database.add(record)
            database.commit()
            database.refresh(record)

            return record_to_artifact(record)

    def get_by_path(
        self,
        session_id: UUID,
        relative_path: str,
    ) -> SessionArtifact | None:
        normalized_path = relative_path.replace(
            "\\",
            "/",
        ).strip()

        statement = select(SessionArtifactRecord).where(
            SessionArtifactRecord.session_id == str(session_id),
            SessionArtifactRecord.relative_path == normalized_path,
        )

        with self._session_factory() as database:
            record = database.scalar(statement)

            if record is None:
                return None

            return record_to_artifact(record)

    def list_for_session(
        self,
        session_id: UUID,
    ) -> list[SessionArtifact]:
        statement = (
            select(SessionArtifactRecord)
            .where(SessionArtifactRecord.session_id == str(session_id))
            .order_by(
                SessionArtifactRecord.created_at,
                # Relative paths do not encode registration
                # order. SQLite rowid preserves insertion
                # order when timestamps are equal.
                literal_column("rowid"),
            )
        )

        with self._session_factory() as database:
            records = database.scalars(statement).all()

            return [record_to_artifact(record) for record in records]
