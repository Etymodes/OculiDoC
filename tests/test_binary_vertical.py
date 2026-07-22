from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pytestqt.qtbot import QtBot

from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample
from oculidoc.experiments.task_runtime import RecordedTaskRuntime
from oculidoc.process_launch import gaze_task_process_command
from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionSetupDialog,
    BinaryQuestionTask,
)


def sample(sequence: int, *, x: float, y: float) -> EyeTrackerSample:
    timestamp_ns = 1_000_000_000 + sequence * 300_000_000
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=timestamp_ns,
            utc_timestamp=datetime(2026, 7, 20, tzinfo=UTC),
            source_timestamp_ns=timestamp_ns,
            source_clock_id="binary-vertical-test",
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=True,
        right_eye_valid=True,
    )


def test_vertical_binary_uses_y_axis_for_dwell(qtbot: QtBot) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="上面还是下面？",
            option_1="上面",
            option_2="下面",
            dwell_time_ms=500,
            randomize_sides=False,
        ),
        layout="vertical",
    )
    qtbot.addWidget(task)

    with qtbot.waitSignal(task.answered, timeout=1_000) as signal:
        task.consume_sample(sample(0, x=0.1, y=0.8))
        task.consume_sample(sample(1, x=0.9, y=0.8))
        task.consume_sample(sample(2, x=0.1, y=0.8))

    assert signal.args == ["bottom", "下面"]
    assert task.result == ("bottom", "下面")
    assert task.recording_result("answered")["selected_position"] == "bottom"


def test_vertical_binary_registers_top_and_bottom_aois(qtbot: QtBot) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="请选择",
            option_1="上",
            option_2="下",
            question_type="yes_no",
            correct_option_id="option_1",
            randomize_sides=False,
        ),
        layout="vertical",
    )
    qtbot.addWidget(task)
    task.resize(900, 1_000)
    task.show()
    qtbot.wait(10)

    context = task.recording_context_for_sample(sample(0, x=0.9, y=0.2))
    aois = {aoi["aoi_id"]: aoi for aoi in context["aois"]}

    assert context["question_metadata"]["layout"] == "vertical"
    assert context["question_metadata"]["correct_position"] == "top"
    assert set(aois) == {"top_answer", "bottom_answer"}
    assert aois["top_answer"]["role"] == "correct_option"
    assert aois["top_answer"]["bottom"] <= aois["bottom_answer"]["top"]


def test_vertical_binary_reuses_settings_with_vertical_labels(qtbot: QtBot) -> None:
    dialog = BinaryQuestionSetupDialog(
        config=BinaryQuestionConfig(question="请选择"),
        layout="vertical",
    )
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "上下二分问答设置"
    assert "上下位置" in dialog.randomize_sides_check.text()
    assert dialog.build_config().question == "请选择"


def test_vertical_binary_has_distinct_runtime_and_frozen_routes(qtbot: QtBot) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(question="请选择"),
        layout="vertical",
    )
    qtbot.addWidget(task)

    assert RecordedTaskRuntime._infer_task_kind(task) == "binary_vertical"
    assert gaze_task_process_command(
        "binary-vertical",
        executable="OculiDoC.exe",
        frozen=True,
    ) == ("OculiDoC.exe", ["--task", "binary-vertical"])


def test_binary_layout_rejects_unknown_value(qtbot: QtBot) -> None:
    with pytest.raises(ValueError, match="horizontal or vertical"):
        task = BinaryQuestionTask(
            BinaryQuestionConfig(question="请选择"),
            layout="diagonal",
        )
        qtbot.addWidget(task)
