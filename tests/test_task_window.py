"""Tests for reusable timed task windows."""

from pytestqt.qtbot import QtBot

from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionTask,
)
from oculidoc.tasks.task_window import (
    TimedTaskWindow,
)
from oculidoc.tasks.tracking_ball import (
    TrackingBallConfig,
    TrackingBallTask,
)


def test_tracking_config_has_duration() -> None:
    config = TrackingBallConfig(duration_seconds=90)

    assert config.duration_seconds == 90


def test_binary_config_has_duration() -> None:
    config = BinaryQuestionConfig(
        question="继续吗？",
        left_answer="继续",
        right_answer="停止",
        duration_seconds=45,
    )

    assert config.duration_seconds == 45


def test_task_window_has_emergency_exit(
    qtbot: QtBot,
) -> None:
    task = TrackingBallTask(TrackingBallConfig())
    window = TimedTaskWindow(
        task,
        duration_seconds=60,
        title="追踪球",
    )
    qtbot.addWidget(window)

    assert window.exit_button.text() == "✕ 退出"

    with qtbot.waitSignal(
        window.finished,
        timeout=1_000,
    ) as signal:
        window.exit_button.click()

    assert signal.args == ["manual_exit"]


def test_binary_answers_use_large_regions(
    qtbot: QtBot,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="是否听到？",
            left_answer="是",
            right_answer="否",
        )
    )
    qtbot.addWidget(task)
    task.resize(1_280, 720)
    task.show()
    qtbot.wait(10)

    small_height = task.left_button.height()

    assert task.left_button.width() >= task.width() * 0.45
    assert small_height >= task.height() * 0.70

    task.resize(3_840, 2_160)
    qtbot.wait(10)

    assert task.left_button.height() > small_height * 3
    assert task.right_button.height() == task.left_button.height()
