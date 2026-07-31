from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample
from oculidoc.experiments.gaze_coordinates import TaskGazeCoordinateTransform
from oculidoc.experiments.recording import AoiRole, NormalizedAoi
from oculidoc.experiments.task_runtime import RecordedTaskRuntime
from oculidoc.tasks.binary_question import BinaryQuestionConfig, BinaryQuestionTask
from oculidoc.tasks.sequential_choice import SequentialChoiceTask
from oculidoc.tasks.task_window import TimedTaskWindow


def gaze_sample(x: float, y: float, *, sequence: int = 1) -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=sequence * 1_000_000,
            utc_timestamp=datetime.now(UTC),
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=True,
        right_eye_valid=True,
    )


def test_transform_maps_display_gaze_to_task_and_task_aoi_back() -> None:
    transform = TaskGazeCoordinateTransform(
        screen_left_px=-1_920.0,
        screen_top_px=0.0,
        screen_width_px=1_920.0,
        screen_height_px=1_080.0,
        task_left_px=-1_920.0,
        task_top_px=82.0,
        task_width_px=1_920.0,
        task_height_px=998.0,
    )
    screen_x, screen_y = transform.task_point_to_screen(0.5, 0.5)
    mapped = transform.sample_to_task(gaze_sample(screen_x, screen_y))
    aoi = transform.aoi_to_screen(
        NormalizedAoi(
            aoi_id="task",
            role=AoiRole.TARGET,
            left=0.0,
            top=0.0,
            right=1.0,
            bottom=1.0,
        )
    )

    assert mapped.gaze_x_normalized == pytest.approx(0.5)
    assert mapped.gaze_y_normalized == pytest.approx(0.5)
    assert aoi is not None
    assert aoi.left == pytest.approx(0.0)
    assert aoi.top == pytest.approx(82.0 / 1_080.0)
    assert aoi.right == pytest.approx(1.0)
    assert aoi.bottom == pytest.approx(1.0)


def test_transform_invalidates_gaze_in_task_header_instead_of_clamping() -> None:
    transform = TaskGazeCoordinateTransform(
        screen_left_px=0.0,
        screen_top_px=0.0,
        screen_width_px=1_000.0,
        screen_height_px=800.0,
        task_left_px=0.0,
        task_top_px=82.0,
        task_width_px=1_000.0,
        task_height_px=718.0,
    )

    mapped = transform.sample_to_task(gaze_sample(0.5, 0.05))

    assert mapped.gaze_x_normalized is None
    assert mapped.gaze_y_normalized is None
    assert not mapped.gaze_valid


def test_transform_clears_numeric_gaze_from_device_invalid_sample() -> None:
    transform = TaskGazeCoordinateTransform(
        screen_left_px=0.0,
        screen_top_px=0.0,
        screen_width_px=1_000.0,
        screen_height_px=800.0,
        task_left_px=0.0,
        task_top_px=80.0,
        task_width_px=1_000.0,
        task_height_px=720.0,
    )
    invalid = gaze_sample(0.5, 0.5)
    invalid = EyeTrackerSample(
        timestamp=invalid.timestamp,
        gaze_x_normalized=invalid.gaze_x_normalized,
        gaze_y_normalized=invalid.gaze_y_normalized,
        left_eye_valid=False,
        right_eye_valid=False,
    )

    mapped = transform.sample_to_task(invalid)

    assert mapped.gaze_x_normalized is None
    assert mapped.gaze_y_normalized is None
    assert not mapped.gaze_valid


class AoiTask(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.samples: list[EyeTrackerSample] = []

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        self.samples.append(sample)

    def recording_context_for_sample(
        self,
        sample: EyeTrackerSample,
    ) -> dict[str, object]:
        return {
            "question_id": "mapped-task",
            "phase": f"y={sample.gaze_y_normalized}",
            "aois": [
                {
                    "aoi_id": "task-viewport",
                    "role": "target",
                    "left": 0.0,
                    "top": 0.0,
                    "right": 1.0,
                    "bottom": 1.0,
                }
            ],
            "reference_aoi": {
                "aoi_id": "task-viewport",
                "role": "target",
                "left": 0.0,
                "top": 0.0,
                "right": 1.0,
                "bottom": 1.0,
            },
        }


def test_runtime_records_raw_screen_gaze_and_forwards_task_local_copy(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    task = AoiTask()
    window = TimedTaskWindow(
        task,
        duration_seconds=30,
        title="坐标测试",
    )
    qtbot.addWidget(window)
    window.resize(640, 480)
    window.show()
    qtbot.wait(10)

    transform = TaskGazeCoordinateTransform.from_widget(task)
    assert transform is not None
    raw_x, raw_y = transform.task_point_to_screen(0.5, 0.5)
    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        map_screen_gaze_to_task=True,
        session_directory=tmp_path / "session",
        patient_id="patient",
        session_id="session",
    )

    runtime.handle_sample(gaze_sample(raw_x, raw_y))
    window.resize(700, 520)
    qtbot.wait(10)
    resized_transform = TaskGazeCoordinateTransform.from_widget(task)
    assert resized_transform is not None
    resized_x, resized_y = resized_transform.task_point_to_screen(0.25, 0.75)
    runtime.handle_sample(gaze_sample(resized_x, resized_y, sequence=2))
    runtime.finish("test_complete")

    assert task.samples[0].gaze_x_normalized == pytest.approx(0.5, abs=0.01)
    assert task.samples[0].gaze_y_normalized == pytest.approx(0.5, abs=0.01)
    assert task.samples[1].gaze_x_normalized == pytest.approx(0.25, abs=0.01)
    assert task.samples[1].gaze_y_normalized == pytest.approx(0.75, abs=0.01)
    assert runtime.run_directory is not None

    rows = pq.read_table(runtime.run_directory / "gaze_events.parquet").to_pylist()
    row = rows[0]
    assert row["gaze_x_normalized"] == pytest.approx(raw_x)
    assert row["gaze_y_normalized"] == pytest.approx(raw_y)
    assert row["aoi_id"] == "task-viewport"
    assert rows[1]["gaze_x_normalized"] == pytest.approx(resized_x)
    assert rows[1]["gaze_y_normalized"] == pytest.approx(resized_y)

    layouts = json.loads(
        (runtime.run_directory / "question_layouts.json").read_text(encoding="utf-8")
    )
    recorded_aoi = layouts["questions"][0]["aois"][0]
    assert recorded_aoi["top"] > 0.0
    assert recorded_aoi["top"] == pytest.approx(transform.task_point_to_screen(0.0, 0.0)[1])
    assert recorded_aoi["bottom"] == pytest.approx(transform.task_point_to_screen(1.0, 1.0)[1])
    assert row["reference_aoi_top"] == pytest.approx(recorded_aoi["top"])
    assert row["reference_aoi_bottom"] == pytest.approx(recorded_aoi["bottom"])


def test_vertical_binary_hits_the_actual_top_button_center(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="是否听到？",
            dwell_time_ms=250,
            neutral_zone_width=0.08,
        ),
        layout="vertical",
    )
    window = TimedTaskWindow(
        task,
        duration_seconds=30,
        title="上下二分问答",
    )
    qtbot.addWidget(window)
    window.resize(640, 560)
    window.show()
    window.start()
    qtbot.wait(10)

    screen = task.screen()
    assert screen is not None
    geometry = screen.geometry()
    top_center = task.left_button.mapToGlobal(task.left_button.rect().center())
    raw_x = (top_center.x() - geometry.x()) / geometry.width()
    raw_y = (top_center.y() - geometry.y()) / geometry.height()
    header_center = window.title_label.mapToGlobal(window.title_label.rect().center())
    header_x = (header_center.x() - geometry.x()) / geometry.width()
    header_y = (header_center.y() - geometry.y()) / geometry.height()
    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        map_screen_gaze_to_task=True,
        session_directory=tmp_path / "vertical-session",
        patient_id="patient",
        session_id="vertical-session",
    )

    runtime.handle_sample(gaze_sample(header_x, header_y, sequence=1))
    runtime.handle_sample(gaze_sample(raw_x, raw_y, sequence=2))
    runtime.handle_sample(gaze_sample(raw_x, raw_y, sequence=302))
    runtime.finish("answered")

    assert task.result is not None
    assert task.result[0] == "top"
    assert runtime.run_directory is not None
    rows = pq.read_table(runtime.run_directory / "gaze_events.parquet").to_pylist()
    assert rows[0]["aoi_id"] is None
    assert rows[0]["aoi_role"] == "non_option"
    assert rows[-1]["aoi_id"] == "top_answer"


def test_sequential_choice_exposes_the_current_leaf_coordinate_widget(
    qtbot: QtBot,
) -> None:
    config = BinaryQuestionConfig(question="是否听到？")
    task = SequentialChoiceTask(
        config=config,
        question_ids=("q1", "q2"),
        task_factory=lambda _index: BinaryQuestionTask(config),
        layout_orientation="horizontal",
    )
    qtbot.addWidget(task)
    task.start()
    first = task.gaze_coordinate_widget

    task.skip_question("Space")

    assert task.current_question_number == 2
    assert task.gaze_coordinate_widget is task.current_task
    assert task.gaze_coordinate_widget is not first


def test_sequential_choice_maps_gaze_after_replacing_the_leaf(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    config = BinaryQuestionConfig(
        question="是否听到？",
        dwell_time_ms=250,
        randomize_sides=False,
    )
    task = SequentialChoiceTask(
        config=config,
        question_ids=("q1", "q2"),
        task_factory=lambda _index: BinaryQuestionTask(config, layout="vertical"),
        layout_orientation="vertical",
    )
    window = TimedTaskWindow(
        task,
        duration_seconds=30,
        title="连续题坐标测试",
    )
    qtbot.addWidget(window)
    window.resize(640, 560)
    window.show()
    window.start()
    qtbot.wait(10)

    first = task.current_task
    assert task.skip_question("Space") is False
    qtbot.wait(10)
    leaf = task.current_task
    assert leaf is not first

    screen = leaf.screen()
    assert screen is not None
    geometry = screen.geometry()
    top_center = leaf.left_button.mapToGlobal(leaf.left_button.rect().center())
    raw_x = (top_center.x() - geometry.x()) / geometry.width()
    raw_y = (top_center.y() - geometry.y()) / geometry.height()
    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        map_screen_gaze_to_task=True,
        session_directory=tmp_path / "sequence-session",
        patient_id="patient",
        session_id="sequence-session",
    )

    runtime.handle_sample(gaze_sample(raw_x, raw_y, sequence=1))
    runtime.handle_sample(gaze_sample(raw_x, raw_y, sequence=302))
    runtime.finish("test_complete")

    assert leaf.result is not None
    assert leaf.result[0] == "top"
    assert runtime.run_directory is not None
    rows = pq.read_table(runtime.run_directory / "gaze_events.parquet").to_pylist()
    assert rows[-1]["question_id"] == "q2"
    assert rows[-1]["aoi_id"] == "top_answer"


def test_task_cli_enables_screen_mapping_before_gaze_delivery() -> None:
    source = Path("src/oculidoc/tasks/__main__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    runtime_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RecordedTaskRuntime"
    ]
    assert len(runtime_calls) == 1
    mapping_keyword = next(
        keyword for keyword in runtime_calls[0].keywords if keyword.arg == "map_screen_gaze_to_task"
    )
    assert isinstance(mapping_keyword.value, ast.Constant)
    assert mapping_keyword.value.value is True

    start_task = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "start_task"
    )
    calls = [node for node in ast.walk(start_task) if isinstance(node, ast.Call)]

    def line_for_call(attribute: str) -> int:
        return next(
            node.lineno
            for node in calls
            if isinstance(node.func, ast.Attribute) and node.func.attr == attribute
        )

    assert line_for_call("showFullScreen") < line_for_call("start")
    assert line_for_call("start") < line_for_call("singleShot")
