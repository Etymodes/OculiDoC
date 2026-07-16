"""Experiment session lifecycle use cases."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns
from uuid import UUID, uuid4

from oculidoc.application.experiment_session_repository import (
    ExperimentSessionRepository,
    SessionArtifactRepository,
)
from oculidoc.application.patient_repository import (
    PatientRepository,
)
from oculidoc.application.session_workspace import (
    SessionWorkspace,
)
from oculidoc.domain.experiment_session import (
    ExperimentSession,
    SessionArtifact,
    SessionArtifactKind,
)


class ExperimentSessionNotFoundError(LookupError):
    """Raised when an experiment session cannot be found."""


class InactivePatientError(ValueError):
    """Raised when a session is requested for an inactive patient."""


class SessionWorkspaceUnavailableError(RuntimeError):
    """Raised when no filesystem workspace is configured."""


class DuplicateSessionArtifactError(ValueError):
    """Raised when an artifact path is already registered."""


@dataclass(frozen=True, slots=True)
class CreateExperimentSessionRequest:
    """Input data for creating one experiment session."""

    patient_id: UUID
    module_id: str
    data_directory: str | None = None
    schema_version: str = "1.0"


@dataclass(frozen=True, slots=True)
class RegisterSessionArtifactRequest:
    """Input data for adding one file to the manifest."""

    session_id: UUID
    kind: SessionArtifactKind
    relative_path: str
    source: str
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None


class ExperimentSessionService:
    """Coordinate session lifecycle and file manifests."""

    def __init__(
        self,
        patient_repository: PatientRepository,
        session_repository: ExperimentSessionRepository,
        artifact_repository: SessionArtifactRepository,
        workspace: SessionWorkspace | None = None,
    ) -> None:
        self._patient_repository = patient_repository
        self._session_repository = session_repository
        self._artifact_repository = artifact_repository
        self._workspace = workspace

    def _write_metadata(
        self,
        session: ExperimentSession,
    ) -> None:
        """Synchronize session.json when a workspace exists."""
        if self._workspace is not None:
            self._workspace.write_metadata(session)

    def _register_metadata_artifact(
        self,
        session: ExperimentSession,
    ) -> None:
        """Register session.json without duplicating the manifest."""
        existing = self._artifact_repository.get_by_path(
            session.session_id,
            "session.json",
        )

        if existing is not None:
            return

        self._artifact_repository.add(
            SessionArtifact(
                session_id=session.session_id,
                kind=(SessionArtifactKind.SESSION_METADATA),
                relative_path="session.json",
                source="system",
                mime_type="application/json",
            )
        )

    def resolve_session_directory(
        self,
        session_id: UUID,
    ) -> Path:
        """Return the filesystem directory for one session."""
        session = self.get_session(session_id)

        if self._workspace is None:
            raise SessionWorkspaceUnavailableError("No session workspace is configured.")

        return self._workspace.resolve_session_directory(session)

    def create_session(
        self,
        request: CreateExperimentSessionRequest,
    ) -> ExperimentSession:
        """Create a session for one active patient."""
        patient = self._patient_repository.get(request.patient_id)

        if patient is None:
            raise LookupError(f"Patient not found: {request.patient_id}")

        if not patient.is_active:
            raise InactivePatientError("Cannot create a session for an inactive patient.")

        session_id = uuid4()
        data_directory = request.data_directory or (f"sessions/{patient.patient_code}/{session_id}")

        session = ExperimentSession(
            session_id=session_id,
            patient_id=patient.patient_id,
            module_id=request.module_id,
            data_directory=data_directory,
            schema_version=request.schema_version,
        )

        saved_session = self._session_repository.add(session)

        if self._workspace is not None:
            self._workspace.initialize(saved_session)
            self._register_metadata_artifact(saved_session)
            self._write_metadata(saved_session)

        return saved_session

    def get_session(
        self,
        session_id: UUID,
    ) -> ExperimentSession:
        """Return one session or raise a stable error."""
        session = self._session_repository.get(session_id)

        if session is None:
            raise ExperimentSessionNotFoundError(f"Experiment session not found: {session_id}")

        return session

    def list_sessions_for_patient(
        self,
        patient_id: UUID,
    ) -> list[ExperimentSession]:
        """Return sessions belonging to one patient."""
        return self._session_repository.list_for_patient(patient_id)

    def start_session(
        self,
        session_id: UUID,
        *,
        monotonic_timestamp_ns: int | None = None,
        utc_timestamp: datetime | None = None,
    ) -> ExperimentSession:
        """Start a session and save its unified clock origin."""
        session = self.get_session(session_id)

        session.start(
            monotonic_timestamp_ns=(
                monotonic_timestamp_ns if monotonic_timestamp_ns is not None else monotonic_ns()
            ),
            utc_timestamp=(utc_timestamp if utc_timestamp is not None else datetime.now(UTC)),
        )

        saved_session = self._session_repository.update(session)
        self._write_metadata(saved_session)

        return saved_session

    def complete_session(
        self,
        session_id: UUID,
    ) -> ExperimentSession:
        """Complete a running session."""
        session = self.get_session(session_id)
        session.complete()

        saved_session = self._session_repository.update(session)
        self._write_metadata(saved_session)

        return saved_session

    def abort_session(
        self,
        session_id: UUID,
        reason: str | None = None,
    ) -> ExperimentSession:
        """Abort a created or running session."""
        session = self.get_session(session_id)
        session.abort(reason)

        saved_session = self._session_repository.update(session)
        self._write_metadata(saved_session)

        return saved_session

    def fail_session(
        self,
        session_id: UUID,
        reason: str,
    ) -> ExperimentSession:
        """Mark a created or running session as failed."""
        session = self.get_session(session_id)
        session.fail(reason)

        saved_session = self._session_repository.update(session)
        self._write_metadata(saved_session)

        return saved_session

    def register_artifact(
        self,
        request: RegisterSessionArtifactRequest,
    ) -> SessionArtifact:
        """Add one produced file to the session manifest."""
        self.get_session(request.session_id)

        existing = self._artifact_repository.get_by_path(
            request.session_id,
            request.relative_path,
        )

        if existing is not None:
            raise DuplicateSessionArtifactError(
                f"Artifact path already registered: {request.relative_path}"
            )

        artifact = SessionArtifact(
            session_id=request.session_id,
            kind=request.kind,
            relative_path=request.relative_path,
            source=request.source,
            mime_type=request.mime_type,
            size_bytes=request.size_bytes,
            sha256=request.sha256,
        )

        return self._artifact_repository.add(artifact)

    def list_artifacts(
        self,
        session_id: UUID,
    ) -> list[SessionArtifact]:
        """Return the session file manifest."""
        self.get_session(session_id)

        return self._artifact_repository.list_for_session(session_id)
