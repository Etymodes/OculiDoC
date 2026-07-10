"""Qt application construction."""

import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from oculidoc.config import get_settings
from oculidoc.ui.main_window import AdminMainWindow


def create_qt_application(argv: Sequence[str] | None = None) -> QApplication:
    existing = QApplication.instance()

    if isinstance(existing, QApplication):
        return existing

    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName("OculiDoC")
    app.setApplicationDisplayName("OculiDoC")
    app.setOrganizationName("Etymodes and TiantanDoC")
    return app


def run() -> int:
    settings = get_settings()
    app = create_qt_application()
    window = AdminMainWindow(settings)
    window.show()
    return app.exec()
