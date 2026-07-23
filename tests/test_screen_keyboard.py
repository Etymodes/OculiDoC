from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy
from pytestqt.qtbot import QtBot

from oculidoc.tasks.screen_keyboard import (
    KeyboardStage,
    ScreenKeyboardConfig,
    ScreenKeyboardSetupDialog,
    ScreenKeyboardTask,
    apply_tone,
)


def select(task: ScreenKeyboardTask, value: str) -> None:
    task.advance_dwell(task.visible_option_id(value), task.config.dwell_time_ms)


def test_tone_marks_follow_pinyin_priority() -> None:
    assert apply_tone("cheng", 2) == "chéng"
    assert apply_tone("uai", 3) == "uǎi"
    assert apply_tone("ui", 4) == "uì"
    assert apply_tone("lüe", 1) == "lüē"
    assert apply_tone("ma", 5) == "ma"


def test_staged_keyboard_commits_and_returns_to_initial(qtbot: QtBot) -> None:
    task = ScreenKeyboardTask(ScreenKeyboardConfig(dwell_time_ms=500))
    qtbot.addWidget(task)

    select(task, "ch")
    assert task.stage is KeyboardStage.CONFIRM_INITIAL
    select(task, "yes")
    select(task, "e")
    select(task, "yes")
    select(task, "ng")
    select(task, "yes")
    select(task, "2")
    select(task, "yes")

    assert task.output_text == "chéng"
    assert task.stage is KeyboardStage.INITIAL
    assert task.composing_text == "等待选择"
    assert task.recording_result("manual_exit")["final_text"] == "chéng"


def test_wrong_confirmation_returns_to_same_selection(qtbot: QtBot) -> None:
    task = ScreenKeyboardTask(ScreenKeyboardConfig(dwell_time_ms=500))
    qtbot.addWidget(task)

    select(task, "b")
    select(task, "no")

    assert task.stage is KeyboardStage.INITIAL
    assert task.output_text == ""


def test_tone_step_can_be_disabled(qtbot: QtBot) -> None:
    task = ScreenKeyboardTask(ScreenKeyboardConfig(dwell_time_ms=500, enable_tone_step=False))
    qtbot.addWidget(task)

    for value in ("b", "yes", "a", "yes", "n", "yes"):
        select(task, value)

    assert task.output_text == "ban"
    assert task.stage is KeyboardStage.INITIAL


def test_setup_preserves_large_font_settings(qtbot: QtBot) -> None:
    config = ScreenKeyboardConfig(
        output_font_size_pt=64,
        instruction_font_size_pt=38,
        option_font_size_pt=42,
        enable_tone_step=False,
    )
    dialog = ScreenKeyboardSetupDialog(config=config)
    qtbot.addWidget(dialog)

    assert dialog.build_config() == config


def test_stage_options_expand_with_high_resolution(qtbot: QtBot) -> None:
    task = ScreenKeyboardTask(ScreenKeyboardConfig())
    qtbot.addWidget(task)
    task.resize(1_920, 1_080)
    task.show()
    qtbot.wait(10)
    small_height = task._buttons["stage:0"].height()

    task.resize(3_840, 2_160)
    qtbot.wait(10)
    first = task._buttons["stage:0"]

    assert first.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Expanding
    assert first.height() > small_height * 2
    assert first.width() > 600
