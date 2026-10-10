from pathlib import Path
from unittest.mock import Mock, call

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import QFrame, QPushButton
from pytestqt.qtbot import QtBot

from oculidoc.app import _show_admin_window_on_primary
from oculidoc.config import Settings
from oculidoc.lan_control import DEFAULT_IDLE_TEXT
from oculidoc.modules.registry import DEFAULT_MODULES
from oculidoc.ui.main_window import (
    AdminMainWindow,
    _default_patient_screen_name,
    _place_window_on_named_screen,
)
from oculidoc.ui.patient_window import PatientDisplayWindow


def test_admin_startup_centers_on_primary_available_geometry() -> None:
    primary = Mock()
    primary.availableGeometry.return_value = QRect(1920, 0, 1920, 1080)
    application = Mock()
    application.primaryScreen.return_value = primary
    window = Mock()
    window.size.return_value = QSize(1280, 820)

    _show_admin_window_on_primary(window, application)

    window.setScreen.assert_called_once_with(primary)
    window.show.assert_called_once_with()
    window.move.assert_called_once_with(QPoint(2240, 130))
    assert window.method_calls == [
        call.setScreen(primary),
        call.size(),
        call.move(QPoint(2240, 130)),
        call.show(),
    ]


def test_patient_display_prefers_non_primary_screen() -> None:
    primary = Mock()
    primary.name.return_value = "operator"
    patient = Mock()
    patient.name.return_value = "patient"
    application = Mock()
    application.primaryScreen.return_value = primary
    application.screens.return_value = [primary, patient]

    assert _default_patient_screen_name(application) == "patient"


def test_patient_display_uses_primary_when_started_with_one_screen() -> None:
    primary = Mock()
    primary.name.return_value = "operator"
    application = Mock()
    application.primaryScreen.return_value = primary
    application.screens.return_value = [primary]

    assert _default_patient_screen_name(application) == "operator"


def test_patient_display_moves_to_named_screen_with_negative_origin() -> None:
    patient = Mock()
    patient.name.return_value = "patient"
    patient.geometry.return_value = QRect(-1920, 0, 1920, 1080)
    application = Mock()
    application.screens.return_value = [patient]
    window = Mock()

    assert _place_window_on_named_screen(window, application, "patient") is True
    window.setScreen.assert_called_once_with(patient)
    window.move.assert_called_once_with(QPoint(-1920, 0))


def test_patient_display_does_not_fall_back_when_named_screen_is_missing() -> None:
    operator = Mock()
    operator.name.return_value = "operator"
    application = Mock()
    application.screens.return_value = [operator]
    window = Mock()

    assert _place_window_on_named_screen(window, application, "patient") is False
    window.setScreen.assert_not_called()
    window.move.assert_not_called()


def test_admin_window_builds(qtbot: QtBot, tmp_path: Path) -> None:
    window = AdminMainWindow(Settings(environment="test", data_dir=tmp_path))
    qtbot.addWidget(window)

    assert window.windowTitle() == "OculiDoC 管理员端"
    expected_height = min(
        960,
        max(700, window.screen().availableGeometry().height() - 48),
    )
    assert window.width() == 1280
    assert window.height() == expected_height
    assert window.minimumWidth() == 1080
    assert window.minimumHeight() == 700
    assert len(window.module_buttons) == len(DEFAULT_MODULES)

    tracking_button = window.findChild(
        QPushButton,
        "moduleButton_tracking_ball",
    )
    assert tracking_button is not None
    assert tracking_button.property("moduleId") == "tracking_ball"
    assert tracking_button.text() == "追踪球"
    assert tracking_button.property("idleText") == "追踪球"
    assert window.findChild(QFrame, "workbenchTaskStrip") is not None
    assert window.findChild(QPushButton, "singleModuleButton") is None
    assert "QPushButton:pressed" in window.styleSheet()
    assert window.update_button.text() == "检查更新"

    image_button = window.findChild(QPushButton, "moduleButton_image_choice")
    fixation_button = window.findChild(QPushButton, "moduleButton_instruction_fixation")
    assert image_button is not None
    assert fixation_button is not None
    assert image_button.isEnabled()


def test_patient_window_builds(qtbot: QtBot) -> None:
    window = PatientDisplayWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "OculiDoC 患者显示端"
    assert window.placeholder_label.text() == DEFAULT_IDLE_TEXT
