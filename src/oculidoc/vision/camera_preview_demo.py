"""Launch the standalone camera preview workbench."""

import sys

from PySide6.QtWidgets import QApplication

from oculidoc.vision.camera_preview_window import (
    CameraPreviewWindow,
)


def main() -> int:
    """Run the PySide6 camera preview workbench."""
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("OculiDoC Camera Preview")

    window = CameraPreviewWindow()
    window.show()

    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
