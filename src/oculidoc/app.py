"""Qt application construction."""

import sys
from collections.abc import Sequence

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from oculidoc.app_paths import application_root
from oculidoc.application.builtin_test_patient import (
    ensure_builtin_test_patient,
    load_or_create_install_date,
)
from oculidoc.branding import (
    apply_application_branding,
)
from oculidoc.config import Settings, apply_saved_gaze_device_config, get_settings
from oculidoc.infrastructure.database import (
    DatabaseRuntime,
    initialize_database,
)
from oculidoc.ui.main_window import AdminMainWindow


def create_qt_application(
    argv: Sequence[str] | None = None,
) -> QApplication:
    "Return the existing Qt application or create one."

    existing = QApplication.instance()

    if isinstance(
        existing,
        QApplication,
    ):
        apply_application_branding(existing)
        return existing

    app = QApplication(list(argv) if argv is not None else sys.argv)
    apply_application_branding(app)

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
        default_patient = ensure_builtin_test_patient(
            database_runtime.patient_service,
            enrollment_date=load_or_create_install_date(
                settings.data_dir,
                installation_root=application_root(),
            ),
        )
        window = AdminMainWindow(
            settings,
            database_runtime.patient_service,
            database_runtime.experiment_session_service,
        )
        window._set_current_patient(default_patient)
    except Exception:
        database_runtime.dispose()
        raise

    return window, database_runtime


def _show_admin_window_on_primary(
    window: AdminMainWindow,
    application: QApplication,
) -> None:
    """Show the administrator window centered on the primary display."""
    primary_screen = application.primaryScreen()
    if primary_screen is None:
        window.show()
        return

    window.setScreen(primary_screen)
    available = primary_screen.availableGeometry()
    size = window.size()
    window.move(
        QPoint(
            available.x() + max(0, (available.width() - size.width()) // 2),
            available.y() + max(0, (available.height() - size.height()) // 2),
        )
    )
    window.show()


def run() -> int:
    """Run the desktop application."""
    settings = apply_saved_gaze_device_config(get_settings())
    app = create_qt_application()

    window, database_runtime = create_admin_window(settings)

    try:
        _show_admin_window_on_primary(window, app)
        return app.exec()
    finally:
        database_runtime.dispose()
