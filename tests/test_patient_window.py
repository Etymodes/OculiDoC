from __future__ import annotations

from pytestqt.qtbot import QtBot

from oculidoc.ui.patient_window import (
    PatientDisplayWindow,
    patient_message_font_size,
)


def test_patient_message_font_is_large_for_short_text() -> None:
    assert patient_message_font_size("请看向中央") == 84
    assert patient_message_font_size("请看向屏幕中央并保持注视") == 72


def test_patient_message_font_adapts_to_long_text() -> None:
    short_size = patient_message_font_size("请睁眼")
    long_size = patient_message_font_size(
        "这是一段用于患者显示端的较长说明文字，需要自动缩小但仍保持清晰可读。"
    )

    assert short_size > long_size
    assert long_size >= 40


def test_patient_window_updates_text_and_font(
    qtbot: QtBot,
) -> None:
    window = PatientDisplayWindow()
    qtbot.addWidget(window)

    window.set_placeholder("请看向屏幕中央")

    assert window.placeholder_label.text() == "请看向屏幕中央"
    assert window.placeholder_label.font().pixelSize() >= 72
