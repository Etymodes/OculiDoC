"""Database initialization and dependency assembly."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from oculidoc.application import (
    ExperimentSessionService,
    PatientService,
)
from oculidoc.infrastructure.database.audit_repositories import (
    SQLitePatientAuditRepository,
)
from oculidoc.infrastructure.database.base import Base
from oculidoc.infrastructure.database.engine import (
    create_sqlite_engine,
)
from oculidoc.infrastructure.database.experiment_session_repositories import (
    SQLiteExperimentSessionRepository,
    SQLiteSessionArtifactRepository,
)
from oculidoc.infrastructure.database.models import (
    ExperimentSessionRecord as ExperimentSessionRecord,
)
from oculidoc.infrastructure.database.models import (
    PatientAuditRecord as PatientAuditRecord,
)
from oculidoc.infrastructure.database.models import (
    PatientRecord as PatientRecord,
)
from oculidoc.infrastructure.database.models import (
    SessionArtifactRecord as SessionArtifactRecord,
)
from oculidoc.infrastructure.database.repositories import (
    SQLitePatientRepository,
)
from oculidoc.infrastructure.database.session import (
    create_session_factory,
)
from oculidoc.infrastructure.session_workspace import (
    FileSystemSessionWorkspace,
)


@dataclass(slots=True)
class DatabaseRuntime:
    """Initialized database dependencies used by the application."""

    engine: Engine
    session_factory: sessionmaker[Session]
    patient_repository: SQLitePatientRepository
    patient_audit_repository: SQLitePatientAuditRepository
    experiment_session_repository: SQLiteExperimentSessionRepository
    session_artifact_repository: SQLiteSessionArtifactRepository
    session_workspace: FileSystemSessionWorkspace | None
    patient_service: PatientService
    experiment_session_service: ExperimentSessionService

    def dispose(self) -> None:
        """Release database connection resources."""
        self.engine.dispose()


def initialize_database(
    database_path: str | Path,
    *,
    echo: bool = False,
    data_root: str | Path | None = None,
) -> DatabaseRuntime:
    """Initialize SQLite, create tables, and assemble services."""
    engine = create_sqlite_engine(
        database_path,
        echo=echo,
    )

    Base.metadata.create_all(engine)

    session_factory = create_session_factory(engine)

    patient_repository = SQLitePatientRepository(session_factory)
    patient_audit_repository = SQLitePatientAuditRepository(session_factory)
    experiment_session_repository = SQLiteExperimentSessionRepository(session_factory)
    session_artifact_repository = SQLiteSessionArtifactRepository(session_factory)
    session_workspace = FileSystemSessionWorkspace(data_root) if data_root is not None else None

    patient_service = PatientService(
        patient_repository,
        patient_audit_repository,
    )
    experiment_session_service = ExperimentSessionService(
        patient_repository,
        experiment_session_repository,
        session_artifact_repository,
        session_workspace,
    )

    return DatabaseRuntime(
        engine=engine,
        session_factory=session_factory,
        patient_repository=patient_repository,
        patient_audit_repository=patient_audit_repository,
        experiment_session_repository=(experiment_session_repository),
        session_artifact_repository=(session_artifact_repository),
        session_workspace=session_workspace,
        patient_service=patient_service,
        experiment_session_service=(experiment_session_service),
    )
