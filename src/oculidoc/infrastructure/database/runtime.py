"""Database initialization and dependency assembly."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from oculidoc.application import PatientService
from oculidoc.infrastructure.database.base import Base
from oculidoc.infrastructure.database.engine import (
    create_sqlite_engine,
)
from oculidoc.infrastructure.database.models import (
    PatientRecord as PatientRecord,
)
from oculidoc.infrastructure.database.repositories import (
    SQLitePatientRepository,
)
from oculidoc.infrastructure.database.session import (
    create_session_factory,
)


@dataclass(slots=True)
class DatabaseRuntime:
    """Initialized database dependencies used by the application."""

    engine: Engine
    session_factory: sessionmaker[Session]
    patient_repository: SQLitePatientRepository
    patient_service: PatientService

    def dispose(self) -> None:
        """Release database connection resources."""
        self.engine.dispose()


def initialize_database(
    database_path: str | Path,
    *,
    echo: bool = False,
) -> DatabaseRuntime:
    """Initialize SQLite, create tables, and assemble services."""
    engine = create_sqlite_engine(
        database_path,
        echo=echo,
    )

    Base.metadata.create_all(engine)

    session_factory = create_session_factory(engine)
    patient_repository = SQLitePatientRepository(session_factory)
    patient_service = PatientService(patient_repository)

    return DatabaseRuntime(
        engine=engine,
        session_factory=session_factory,
        patient_repository=patient_repository,
        patient_service=patient_service,
    )
