"""Experiment session filesystem workspace port."""

from pathlib import Path
from typing import Protocol

from oculidoc.domain.experiment_session import ExperimentSession


class SessionWorkspace(Protocol):
    """Filesystem operations required by session services."""

    def resolve_session_directory(
        self,
        session: ExperimentSession,
    ) -> Path:
        """Resolve the concrete directory for one session."""
        ...

    def initialize(
        self,
        session: ExperimentSession,
    ) -> Path:
        """Create and return the session directory."""
        ...

    def write_metadata(
        self,
        session: ExperimentSession,
    ) -> Path:
        """Atomically write and return session.json."""
        ...

    def archive_for_deletion(
        self,
        session: ExperimentSession,
    ) -> Path | None:
        """Move a session directory into the application recovery area."""
        ...

    def restore_archived(
        self,
        session: ExperimentSession,
        archived_directory: Path,
    ) -> Path:
        """Restore a directory when deleting its database record fails."""
        ...
