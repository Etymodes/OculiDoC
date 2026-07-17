"""Tests that real eye tracking ignores task-area mouse input."""

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionTask,
)
from oculidoc.tasks.tracking_ball import (
    TrackingBallConfig,
    TrackingBallTask,
)


def test_tracking_disables_mouse_fallback(
    qtbot: QtBot,
) -> None:
    task = TrackingBallTask(
        TrackingBallConfig(),
        allow_mouse_fallback=False,
    )
    qtbot.addWidget(task)

    assert task.allow_mouse_fallback is False
    assert task.hasMouseTracking() is False
    assert task.cursor().shape() is (Qt.CursorShape.BlankCursor)


def test_binary_blocks_mouse_selection(
    qtbot: QtBot,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="是否听到？",
            left_answer="是",
            right_answer="否",
        ),
        allow_mouse_fallback=False,
    )
    qtbot.addWidget(task)

    assert task.left_button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert task.right_button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert task.cursor().shape() is (Qt.CursorShape.BlankCursor)
