"""Tests for binary-question clinical result semantics."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

from pytestqt.qtbot import QtBot

from oculidoc.devices.contracts import (
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.experiments.task_runtime import (
    RecordedTaskRuntime,
)
from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionTask,
)
from oculidoc.tasks.question_bank import (
    BinaryQuestionType,
)


def make_sample(
    *,
    sequence: int,
    timestamp_ns: int,
    x: float,
    y: float = 0.5,
) -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=(timestamp_ns),
            utc_timestamp=datetime(
                2026,
                7,
                17,
                12,
                0,
                tzinfo=UTC,
            ),
            source_timestamp_ns=(timestamp_ns),
            source_clock_id=("binary-result-test"),
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=True,
        right_eye_valid=True,
    )


def event_types(
    events: tuple[
        dict[str, object],
        ...,
    ],
) -> list[str]:
    return [str(event["event_type"]) for event in events]


def test_task_records_dwell_transitions_and_answer(
    qtbot: QtBot,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="一加一等于几？",
            option_1="二",
            option_2="三",
            question_type=(BinaryQuestionType.QUESTION_ANSWER),
            dwell_time_ms=500,
            randomization_seed=1,
        )
    )
    qtbot.addWidget(task)
    task.start()

    assert event_types(task.drain_recording_events()) == ["question_presented"]

    correct_side = task.displayed_correct_side

    assert correct_side is not None

    task.advance_dwell(
        correct_side,
        200.0,
    )
    task.advance_dwell(
        None,
        50.0,
        interruption_reason=("neutral_zone"),
    )
    task.advance_dwell(
        correct_side,
        500.0,
    )

    events = task.drain_recording_events()
    types = event_types(events)

    assert types == [
        "gaze_entered_option",
        "dwell_started",
        "dwell_cancelled",
        "gaze_entered_option",
        "dwell_started",
        "answer_committed",
    ]
    cancelled = events[2]

    assert cancelled["payload"]["reason"] == "neutral_zone"
    assert cancelled["payload"]["accumulated_dwell_ms"] == 200.0

    result = task.recording_result("answered")
    final_events = task.drain_recording_events()

    assert event_types(final_events) == ["task_completed"]
    assert result["selected_option_id"] == "option_1"
    assert result["selected_side"] == correct_side
    assert result["selected_answer"] == "二"
    assert result["is_scored"] is True
    assert result["correct"] is True
    assert result["completion_status"] == "answered"
    assert result["confirmation_dwell_ms"] == 500.0
    assert result["selection_method"] == "gaze_dwell"
    assert result["randomization_seed"] == 1


def test_unscored_question_preserves_answer_without_correctness(
    qtbot: QtBot,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="你更想听音乐还是休息？",
            option_1="听音乐",
            option_2="休息",
            question_type=(BinaryQuestionType.INQUIRY),
            dwell_time_ms=300,
            randomization_seed=2,
        )
    )
    qtbot.addWidget(task)

    task.advance_dwell(
        "left",
        300.0,
    )
    result = task.recording_result("answered")

    assert result["is_scored"] is False
    assert result["correct_option_id"] is None
    assert result["correct"] is None
    assert result["selected_option_id"] in {"option_1", "option_2"}
    assert result["selected_answer"] in {"听音乐", "休息"}


def test_runtime_persists_binary_events_and_result(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="北京是中国的首都吗？",
            option_1="是",
            option_2="否",
            question_type=(BinaryQuestionType.YES_NO),
            dwell_time_ms=300,
            randomization_seed=1,
        )
    )
    task.resize(1_200, 800)
    qtbot.addWidget(task)

    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=(tmp_path / "binary-session"),
        patient_id="patient",
        session_id="binary-session",
    )
    correct_side = task.displayed_correct_side

    assert correct_side is not None
    gaze_x = 0.25 if correct_side == "left" else 0.75

    runtime.handle_sample(
        make_sample(
            sequence=0,
            timestamp_ns=1_000_000_000,
            x=gaze_x,
        )
    )
    runtime.handle_sample(
        make_sample(
            sequence=1,
            timestamp_ns=1_200_000_000,
            x=gaze_x,
        )
    )
    runtime.handle_sample(
        make_sample(
            sequence=2,
            timestamp_ns=1_400_000_000,
            x=gaze_x,
        )
    )
    runtime.finish("answered")

    assert runtime.run_directory is not None
    result_document = json.loads(
        (runtime.run_directory / "task_result.json").read_text(encoding="utf-8")
    )
    result = result_document["result"]

    assert result_document["end_reason"] == "answered"
    assert result["completion_status"] == "answered"
    assert result["selected_option_id"] == "option_1"
    assert result["selected_side"] == correct_side
    assert result["correct"] is True
    assert result["reaction_time_ms"] == 400.0
    assert result["confirmation_dwell_ms"] == 400.0
    assert result["recording_failed"] is False

    event_lines = [
        json.loads(line)
        for line in (runtime.run_directory / "task_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    types = [event["event_type"] for event in event_lines]

    assert types == [
        "question_presented",
        "gaze_entered_option",
        "dwell_started",
        "answer_committed",
        "task_completed",
    ]
    committed = next(event for event in event_lines if (event["event_type"] == "answer_committed"))

    assert committed["payload"]["selected_option_id"] == "option_1"
    assert committed["payload"]["correct"] is True


def test_runtime_marks_timeout_as_unanswered(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="你现在感到舒服吗？",
            option_1="是",
            option_2="否",
            question_type=(BinaryQuestionType.INQUIRY),
        )
    )
    qtbot.addWidget(task)

    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=(tmp_path / "timeout-session"),
        patient_id="patient",
        session_id="timeout-session",
    )
    runtime.finish("timeout")

    assert runtime.run_directory is not None
    result_document = json.loads(
        (runtime.run_directory / "task_result.json").read_text(encoding="utf-8")
    )
    result = result_document["result"]

    assert result["completion_status"] == "unanswered"
    assert result["completion_reason"] == "timeout"
    assert result["selected_side"] is None
    assert result["selected_option_id"] is None
    assert result["correct"] is None

    events = [
        json.loads(line)
        for line in (runtime.run_directory / "task_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert [event["event_type"] for event in events] == [
        "question_presented",
        "task_unanswered",
    ]


def test_cli_forwards_window_reason_to_runtime() -> None:
    source = Path("src/oculidoc/tasks/__main__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    connections: list[ast.Call] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        function = node.func

        if not (
            isinstance(
                function,
                ast.Attribute,
            )
            and function.attr == "connect"
        ):
            continue

        signal = function.value

        if (
            isinstance(
                signal,
                ast.Attribute,
            )
            and signal.attr == "finished"
            and isinstance(
                signal.value,
                ast.Name,
            )
            and signal.value.id == "window"
        ):
            connections.append(node)

    assert any(
        isinstance(
            connection.args[0],
            ast.Attribute,
        )
        and isinstance(
            connection.args[0].value,
            ast.Name,
        )
        and (connection.args[0].value.id == "recorded_runtime")
        and (connection.args[0].attr == "finish")
        for connection in connections
    )
