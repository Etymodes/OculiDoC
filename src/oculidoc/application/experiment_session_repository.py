"""Experiment session repository application ports."""

from typing import Protocol
from uuid import UUID

from oculidoc.domain.experiment_session import (
    ExperimentSession,
    SessionArtifact,
)


class ExperimentSessionRepository(Protocol):
    """Storage operations for experiment sessions."""

    def add(
        self,
        session: ExperimentSession,
    ) -> ExperimentSession: ...

    def get(
        self,
        session_id: UUID,
    ) -> ExperimentSession | None: ...

    def list_for_patient(
        self,
        patient_id: UUID,
    ) -> list[ExperimentSession]: ...

    def update(
        self,
        session: ExperimentSession,
    ) -> ExperimentSession: ...

    def delete(
        self,
        session_id: UUID,
    ) -> None: ...


class SessionArtifactRepository(Protocol):
    """Storage operations for session file manifests."""

    def add(
        self,
        artifact: SessionArtifact,
    ) -> SessionArtifact: ...

    def get_by_path(
        self,
        session_id: UUID,
        relative_path: str,
    ) -> SessionArtifact | None: ...

    def list_for_session(
        self,
        session_id: UUID,
    ) -> list[SessionArtifact]: ...
