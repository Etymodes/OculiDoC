from __future__ import annotations

from pytestqt.qtbot import QtBot

from oculidoc.config import Settings
from oculidoc.ui.main_window import AdminMainWindow


def test_main_window_displays_native_tobii_source(
    qtbot: QtBot,
) -> None:
    window = AdminMainWindow(
        Settings(
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
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)

    assert window.gaze_status_label.text() == "眼动源：模拟数据源"
