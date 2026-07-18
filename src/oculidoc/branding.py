from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

APPLICATION_NAME = "OculiDoC"
ORGANIZATION_NAME = "Etymodes and TiantanDoC"
WINDOWS_APP_USER_MODEL_ID = "Etymodes.OculiDoC"
ASSET_DIRECTORY = Path(__file__).resolve().parent / "assets"
_ALLOWED_ASSETS = frozenset(
    {
        "app_icon.ico",
        "app_icon.png",
        "brand_mark_blue.png",
        "brand_mark_white.png",
        "brand_wordmark_blue.png",
    }
)


def brand_asset_path(
    name: str,
) -> Path:
    """Return one known packaged brand asset."""

    if name not in _ALLOWED_ASSETS:
        raise ValueError(f"Unknown branding asset: {name}")

    return ASSET_DIRECTORY / name


def application_icon() -> QIcon:
    """Load the cross-platform application icon."""

    for name in (
        "app_icon.png",
        "app_icon.ico",
    ):
        path = brand_asset_path(name)

        if not path.is_file():
            continue

        icon = QIcon(str(path))

        if not icon.isNull():
            return icon

    return QIcon()


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return

    try:
        shell32 = ctypes.windll.shell32
        shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except (
        AttributeError,
        OSError,
    ):
        return


def apply_application_branding(
    app: QApplication,
) -> None:
    """Apply names and icons to one Qt application."""

    app.setApplicationName(APPLICATION_NAME)
    app.setApplicationDisplayName(APPLICATION_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)

    icon = application_icon()

    if not icon.isNull():
        app.setWindowIcon(icon)

    _set_windows_app_user_model_id()


def brand_mark_pixmap(
    *,
    variant: str = "blue",
    max_width: int = 160,
    max_height: int = 100,
) -> QPixmap:
    """Load a scaled transparent logo mark."""

    if max_width <= 0 or max_height <= 0:
        raise ValueError("Logo dimensions must be positive.")

    names = {
        "blue": "brand_mark_blue.png",
        "white": "brand_mark_white.png",
    }

    try:
        name = names[variant]
    except KeyError as error:
        raise ValueError(f"Unsupported logo variant: {variant}") from error

    path = brand_asset_path(name)

    if not path.is_file():
        return QPixmap()

    pixmap = QPixmap(str(path))

    if pixmap.isNull():
        return pixmap

    return pixmap.scaled(
        max_width,
        max_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def brand_wordmark_pixmap(
    *,
    max_width: int = 480,
    max_height: int = 440,
) -> QPixmap:
    """Load the transparent OculiDoC wordmark."""

    if max_width <= 0 or max_height <= 0:
        raise ValueError("Wordmark dimensions must be positive.")

    path = brand_asset_path("brand_wordmark_blue.png")

    if not path.is_file():
        return QPixmap()

    pixmap = QPixmap(str(path))

    if pixmap.isNull():
        return pixmap

    return pixmap.scaled(
        max_width,
        max_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
