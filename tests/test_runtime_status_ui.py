from __future__ import annotations

from pytestqt.qtbot import QtBot

from oculidoc.config import Settings
from oculidoc.ui.main_window import AdminMainWindow


def test_main_window_displays_native_tobii_source(
    qtbot: QtBot,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            gaze_source="tobii_stream_engine",
        )
    )
    qtbot.addWidget(window)

    assert window.gaze_status_label.text() == ("眼动源：Tobii Eye Tracker 5 · 原生 Stream Engine")


def test_main_window_marks_mock_source(
    qtbot: QtBot,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)

    assert window.gaze_status_label.text() == "眼动源：模拟数据源"


def test_main_window_exposes_lan_pairing_status(
    qtbot: QtBot,
    tmp_path,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)

    assert "本地后台：准备启动" in window.backend_status_button.text()
    assert window._lan_control_url.startswith("http://")
    assert "/control?token=" in window._lan_control_url


def test_main_window_polls_mobile_display_state(
    qtbot: QtBot,
    tmp_path,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)

    state = window._lan_state_store.set_display(
        "来自手机的患者端文字",
        mode="message",
    )
    window._poll_lan_control_state()

    assert window._last_lan_revision == state.revision
    assert window._patient_window.placeholder_label.text() == "来自手机的患者端文字"
