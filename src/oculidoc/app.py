"""Qt application construction."""

import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from oculidoc.config import Settings, get_settings
from oculidoc.infrastructure.database import (
    DatabaseRuntime,
    initialize_database,
)
from oculidoc.ui.main_window import AdminMainWindow


def create_qt_application(
    argv: Sequence[str] | None = None,
) -> QApplication:
    """Return the existing Qt application or create one."""
    existing = QApplication.instance()

    if isinstance(existing, QApplication):
        return existing

    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName("OculiDoC")
    app.setApplicationDisplayName("OculiDoC")
    app.setOrganizationName("Etymodes and TiantanDoC")

    return app


def create_admin_window(
    settings: Settings,
) -> tuple[AdminMainWindow, DatabaseRuntime]:
    """Initialize storage and construct the administrator window."""
    database_runtime = initialize_database(
        settings.database_path,
        data_root=settings.data_dir,
    )

    try:
        window = AdminMainWindow(
            settings,
            database_runtime.patient_service,
            database_runtime.experiment_session_service,
        )
    except Exception:
        database_runtime.dispose()
        raise

    return window, database_runtime


def run() -> int:
    """Run the desktop application."""
    settings = get_settings()
    app = create_qt_application()

    window, database_runtime = create_admin_window(settings)

    try:
        window.show()
        return app.exec()
    finally:
        database_runtime.dispose()
