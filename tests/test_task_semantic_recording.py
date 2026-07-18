"""Tests for semantic task AOI recording."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault(
    "QT_QPA_PLATFORM",
    "offscreen",
)

import pyarrow.parquet as pq
from PySide6.QtWidgets import (
    QApplication,
)
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
from oculidoc.tasks.tracking_ball import (
    TargetEffect,
    TargetPath,
    TrackingBallConfig,
    TrackingBallTask,
)


def make_sample(
    *,
    sequence: int,
    x: float,
    y: float,
) -> EyeTrackerSample:
    timestamp_ns = 1_000_000_000 + sequence * 100_000_000

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
            source_clock_id=("semantic-test"),
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=True,
        right_eye_valid=True,
    )


def process_events() -> None:
    application = QApplication.instance()

    assert application is not None
    application.processEvents()


def test_tracking_context_contains_dynamic_target(
    qtbot: QtBot,
) -> None:
    task = TrackingBallTask(
        TrackingBallConfig(
            path=TargetPath.HORIZONTAL,
            effect=TargetEffect.NONE,
            diameter_px=100,
        )
    )
    task.resize(1_000, 500)
    qtbot.addWidget(task)

    context = task.recording_context_for_sample(
        make_sample(
            sequence=0,
            x=0.5,
            y=0.5,
        )
    )

    reference = context["reference_aoi"]

    assert reference["aoi_id"] == ("moving_target")
    assert reference["role"] == "target"
    assert reference["left"] < 0.5 < reference["right"]
    assert reference["top"] < 0.5 < reference["bottom"]
    assert context["register_layout"] is False


def test_binary_context_uses_button_geometry(
    qtbot: QtBot,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="测试问题",
            left_answer="是",
            right_answer="否",
            correct_side="left",
        )
    )
    task.resize(1_200, 800)
    qtbot.addWidget(task)
    task.show()
    process_events()

    context = task.recording_context_for_sample(
        make_sample(
            sequence=0,
            x=0.25,
            y=0.6,
        )
    )
    aois = {aoi["aoi_id"]: aoi for aoi in context["aois"]}

    left = aois["left_answer"]
    right = aois["right_answer"]

    assert left["role"] == ("correct_option")
    assert right["role"] == ("incorrect_option")
    assert left["right"] <= (right["left"])
    assert left["bottom"] > left["top"]
    assert right["bottom"] > right["top"]


def test_binary_runtime_persists_semantic_layout(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="是否感到舒适？",
            left_answer="是",
            right_answer="否",
            correct_side="left",
        )
    )
    task.resize(1_200, 800)
    qtbot.addWidget(task)
    task.show()
    process_events()

    context = task.recording_context_for_sample(
        make_sample(
            sequence=0,
            x=0.25,
            y=0.6,
        )
    )
    left = next(aoi for aoi in context["aois"] if aoi["aoi_id"] == "left_answer")
    sample_x = (left["left"] + left["right"]) / 2.0
    sample_y = (left["top"] + left["bottom"]) / 2.0

    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=(tmp_path / "binary-session"),
        patient_id="patient",
        session_id="binary-session",
    )

    runtime.handle_sample(
        make_sample(
            sequence=0,
            x=sample_x,
            y=sample_y,
        )
    )
    runtime.finish("test_complete")

    assert runtime.run_directory is not None

    table = pq.read_table(runtime.run_directory / "gaze_events.parquet")
    row = table.to_pylist()[0]

    assert row["question_id"] == ("binary-question-1")
    assert row["aoi_id"] == ("left_answer")
    assert row["aoi_role"] == ("correct_option")

    layouts = json.loads(
        (runtime.run_directory / "question_layouts.json").read_text(encoding="utf-8")
    )

    assert len(layouts["questions"]) == 1
    assert layouts["questions"][0]["metadata"]["correct_side"] == "left"


def test_tracking_runtime_persists_reference_aoi(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    task = TrackingBallTask(
        TrackingBallConfig(
            path=TargetPath.HORIZONTAL,
            effect=TargetEffect.NONE,
            diameter_px=120,
        )
    )
    task.resize(1_000, 500)
    qtbot.addWidget(task)

    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=(tmp_path / "tracking-session"),
        patient_id="patient",
        session_id="tracking-session",
    )

    runtime.handle_sample(
        make_sample(
            sequence=0,
            x=0.5,
            y=0.5,
        )
    )
    runtime.finish("test_complete")

    assert runtime.run_directory is not None

    table = pq.read_table(runtime.run_directory / "gaze_events.parquet")
    row = table.to_pylist()[0]

    assert row["aoi_id"] == ("moving_target")
    assert row["aoi_role"] == "target"
    assert row["reference_aoi_id"] == "moving_target"
    assert row["reference_aoi_role"] == "target"
    assert row["reference_aoi_left"] < row["reference_aoi_right"]
    assert row["reference_aoi_top"] < row["reference_aoi_bottom"]
