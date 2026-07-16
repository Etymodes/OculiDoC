"""Launch the standalone camera and eye workbench."""

import argparse
import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from oculidoc.app_paths import (
    UNASSIGNED_PATIENT_KEY,
)
from oculidoc.vision.camera_preview_window import (
    CameraPreviewWindow,
)


def build_parser() -> argparse.ArgumentParser:
    """Create the workbench command-line parser."""
    parser = argparse.ArgumentParser(prog=("python -m oculidoc.vision.camera_preview_demo"))
    parser.add_argument(
        "--patient-key",
        default=UNASSIGNED_PATIENT_KEY,
        help=("Opaque internal patient identifier. Do not use a patient name."),
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Run the PySide6 camera workbench."""
    args = build_parser().parse_args(argv)

    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("OculiDoC Camera Preview")

    window = CameraPreviewWindow(patient_key=args.patient_key)
    window.show()

    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
