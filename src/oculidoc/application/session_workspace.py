"""Experiment session filesystem workspace port."""

from pathlib import Path
from typing import Protocol

from oculidoc.domain.experiment_session import ExperimentSession


class SessionWorkspace(Protocol):
    """Filesystem operations required by session services."""

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
