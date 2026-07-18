"""Tests for gaze-task runtime recording."""

from __future__ import annotations

import ast
import json
import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault(
    "QT_QPA_PLATFORM",
    "offscreen",
)

import pyarrow.parquet as pq
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from oculidoc.devices.contracts import (
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.experiments.task_runtime import (
    RecordedTaskRuntime,
)


class FakeTask(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.samples: list[EyeTrackerSample] = []
        self.resize(800, 600)

    def consume_sample(
        self,
        sample: EyeTrackerSample,
    ) -> None:
        self.samples.append(sample)


def make_sample(
    sequence: int,
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
            source_clock_id="runtime-test",
        ),
        gaze_x_normalized=0.5,
        gaze_y_normalized=0.5,
        left_eye_valid=True,
        right_eye_valid=True,
    )


def test_runtime_records_and_forwards(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    task = FakeTask()
    qtbot.addWidget(task)

    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=(tmp_path / "session"),
        patient_id="patient",
        session_id="session",
    )

    runtime.handle_sample(make_sample(0))
    runtime.handle_sample(make_sample(1))
    runtime.finish("timeout")

    assert len(task.samples) == 2
    assert runtime.run_directory is not None

    gaze_path = runtime.run_directory / "gaze_events.parquet"
    result_path = runtime.run_directory / "task_result.json"

    assert gaze_path.exists()
    assert result_path.exists()
    assert pq.read_table(gaze_path).num_rows == 2

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["end_reason"] == "timeout"


def test_window_close_finalizes_run(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    task = FakeTask()
    qtbot.addWidget(task)
    task.show()

    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=(tmp_path / "close-session"),
        patient_id="patient",
        session_id="close-session",
    )

    runtime.handle_sample(make_sample(0))
    task.close()

    qtbot.waitUntil(
        lambda: (
            runtime.run_directory is not None
            and (runtime.run_directory / "task_result.json").exists()
        ),
        timeout=2_000,
    )

    assert runtime.run_directory is not None

    result = json.loads((runtime.run_directory / "task_result.json").read_text(encoding="utf-8"))
    assert result["end_reason"] == "window_closed"


def test_runtime_finish_is_idempotent(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    task = FakeTask()
    qtbot.addWidget(task)

    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=tmp_path,
        patient_id="patient",
        session_id="session",
    )

    runtime.handle_sample(make_sample(0))
    runtime.finish("timeout")

    first_directory = runtime.run_directory
    runtime.finish("window_closed")

    assert runtime.run_directory == first_directory


def test_cli_routes_samples_through_recorder() -> None:
    source = Path("src/oculidoc/tasks/__main__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    sample_connections: list[ast.Call] = []

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
            and signal.attr == "sample_received"
        ):
            sample_connections.append(node)

    assert len(sample_connections) == 1

    target = sample_connections[0].args[0]

    assert isinstance(
        target,
        ast.Attribute,
    )
    assert target.attr == "handle_sample"
