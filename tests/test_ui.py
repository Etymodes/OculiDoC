from pathlib import Path

from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from oculidoc.config import Settings
from oculidoc.lan_control import DEFAULT_IDLE_TEXT
from oculidoc.modules.registry import DEFAULT_MODULES
from oculidoc.ui.main_window import AdminMainWindow
from oculidoc.ui.patient_window import PatientDisplayWindow


def test_admin_window_builds(qtbot: QtBot, tmp_path: Path) -> None:
    window = AdminMainWindow(Settings(environment="test", data_dir=tmp_path))
    qtbot.addWidget(window)

    assert window.windowTitle() == "OculiDoC 管理员端"
    assert len(window.module_buttons) == len(DEFAULT_MODULES)

    tracking_button = window.findChild(
        QPushButton,
        "moduleButton_tracking_ball",
    )
    assert tracking_button is not None
    assert tracking_button.property("moduleId") == "tracking_ball"
    assert window.update_button.text() == "检查更新"

    image_button = window.findChild(QPushButton, "moduleButton_image_choice")
    assert image_button is not None
    assert image_button.isEnabled()


def test_patient_window_builds(qtbot: QtBot) -> None:
    window = PatientDisplayWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "OculiDoC 患者显示端"
    assert window.placeholder_label.text() == DEFAULT_IDLE_TEXT
