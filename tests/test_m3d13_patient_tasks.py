"""Simulated-gaze integration tests for the M3D13 P2 patient tasks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns
from typing import Any

import pyarrow.parquet as pq
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample
from oculidoc.experiments.task_runtime import RecordedTaskRuntime
from oculidoc.image_library import ImageLibraryStore
from oculidoc.tasks.gaze_contingency import (
    GardenBlockType,
    GazeContingencyConfig,
    GazeContingencyTask,
)
from oculidoc.tasks.visual_hunt import (
    VisualHuntCondition,
    VisualHuntConfig,
    VisualHuntPhase,
    VisualHuntTask,
)
from oculidoc.tasks.visual_preference import (
    PreferencePair,
    VisualPreferenceConfig,
    VisualPreferencePhase,
    VisualPreferenceTask,
)


def gaze_sample(
    sequence: int,
    timestamp_ns: int,
    *,
    x: float | None,
    y: float | None,
) -> EyeTrackerSample:
    valid = x is not None and y is not None
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=timestamp_ns,
            utc_timestamp=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
            source_timestamp_ns=timestamp_ns,
            source_clock_id="m3d13-simulated-gaze",
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=valid,
        right_eye_valid=valid,
    )


def result_document(runtime: RecordedTaskRuntime) -> dict[str, Any]:
    assert runtime.run_directory is not None
    return json.loads((runtime.run_directory / "task_result.json").read_text(encoding="utf-8"))


def recorded_event_types(runtime: RecordedTaskRuntime) -> list[str]:
    assert runtime.run_directory is not None
    return [
        json.loads(line)["event_type"]
        for line in (runtime.run_directory / "task_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


def test_garden_runs_four_blocks_and_completes_after_four_persistent_flowers(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    config = GazeContingencyConfig(
        dwell_time_ms=250,
        baseline_seconds=5,
        contingent_block_seconds=10,
        replay_block_seconds=10,
        reward_animation_ms=500,
        sound_enabled=True,
        randomization_seed=2_026_072_301,
    )
    task = GazeContingencyTask(config)
    spoken: list[str] = []
    task.speech_requested.connect(spoken.append)
    task.resize(1_000, 700)
    qtbot.addWidget(task)
    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=tmp_path / "garden-session",
        patient_id="Beta00",
        session_id="garden-session",
        task_kind="gaze_games",
    )

    started_ns = monotonic_ns()
    task.start(started_ns)
    task.expire_current_block()
    assert task.current_block.block_type is GardenBlockType.CONTINGENT_1
    assert task.block_deadline_ns is not None
    block_started_ns = task.block_deadline_ns - config.contingent_block_seconds * 1_000_000_000
    sequence = 0

    for flower, offsets in zip(
        task.protocol.objects[:2],
        ((0, 250), (300, 550)),
        strict=True,
    ):
        for offset_ms in offsets:
            runtime.handle_sample(
                gaze_sample(
                    sequence,
                    block_started_ns + offset_ms * 1_000_000,
                    x=flower.x_normalized,
                    y=flower.y_normalized,
                )
            )
            sequence += 1

    first_flower = task.protocol.objects[0]
    runtime.handle_sample(
        gaze_sample(
            sequence,
            block_started_ns + 800 * 1_000_000,
            x=first_flower.x_normalized,
            y=first_flower.y_normalized,
        )
    )
    sequence += 1
    assert len(task.completed_flower_ids) == 2
    assert task.flower_open_progress[first_flower.object_id] == 1.0
    assert task.sky_brightness == 0.5

    task.expire_current_block()
    assert task.current_block.block_type is GardenBlockType.REPLAY
    task.expire_current_block()
    assert task.current_block.block_type is GardenBlockType.CONTINGENT_2
    assert task.block_deadline_ns is not None
    second_block_started_ns = (
        task.block_deadline_ns - config.contingent_block_seconds * 1_000_000_000
    )
    for flower, offsets in zip(
        task.protocol.objects[2:],
        ((0, 250), (300, 550)),
        strict=True,
    ):
        for offset_ms in offsets:
            runtime.handle_sample(
                gaze_sample(
                    sequence,
                    second_block_started_ns + offset_ms * 1_000_000,
                    x=flower.x_normalized,
                    y=flower.y_normalized,
                )
            )
            sequence += 1

    assert task.phase == "celebrating"
    assert task.sky_brightness == 1.0
    assert spoken[-1] == "恭喜您成功点亮花园"
    task.advance_time(second_block_started_ns + 4_000 * 1_000_000)
    assert task.phase == "completed"
    runtime.finish("test_complete")

    document = result_document(runtime)
    result = document["result"]
    assert result["completion_status"] == "completed"
    assert result["replay_source"] == "recorded_contingent_1"
    assert len(result["blocks"]) == 4
    assert len(result["objects"]) == config.object_count
    assert result["configuration"]["dwell_time_ms"] == config.dwell_time_ms
    assert result["completed_flower_count"] == 4
    assert result["sky_brightness"] == 1.0
    assert result["garden_goal_reached"] is True

    event_types = recorded_event_types(runtime)
    assert event_types.count("reward_triggered") == 4
    assert event_types.count("replay_reward_presented") == 2
    assert event_types.count("garden_completed") == 1

    assert runtime.run_directory is not None
    table = pq.read_table(runtime.run_directory / "gaze_events.parquet")
    assert table.num_rows == 9
    assert "target" in set(table.column("aoi_role").to_pylist())


def test_garden_partial_lotus_opens_with_gaze_and_slowly_closes_after_exit(
    qtbot: QtBot,
) -> None:
    config = GazeContingencyConfig(
        dwell_time_ms=250,
        baseline_seconds=5,
        contingent_block_seconds=10,
        replay_block_seconds=10,
        sound_enabled=False,
        randomization_seed=41,
    )
    task = GazeContingencyTask(config)
    task.resize(1_000, 700)
    qtbot.addWidget(task)
    started_ns = monotonic_ns()
    task.start(started_ns)
    task.expire_current_block()
    assert task.block_deadline_ns is not None
    block_started_ns = task.block_deadline_ns - config.contingent_block_seconds * 1_000_000_000
    flower = task.protocol.objects[0]
    task.consume_sample(
        gaze_sample(
            0,
            block_started_ns,
            x=flower.x_normalized,
            y=flower.y_normalized,
        )
    )
    task.consume_sample(
        gaze_sample(
            1,
            block_started_ns + 125_000_000,
            x=flower.x_normalized,
            y=flower.y_normalized,
        )
    )
    opening = task.flower_open_progress[flower.object_id]
    assert 0.45 <= opening <= 0.55

    task.consume_sample(
        gaze_sample(
            2,
            block_started_ns + 150_000_000,
            x=0.5,
            y=0.98,
        )
    )
    task.advance_time(block_started_ns + 275_000_000)
    closing = task.flower_open_progress[flower.object_id]
    assert 0.0 < closing < opening
    task.stop()


def _advance_hunt_to_array(task: VisualHuntTask) -> None:
    while task.phase not in {
        VisualHuntPhase.ARRAY,
        VisualHuntPhase.COMPLETED,
    }:
        task.expire_current_phase()


def test_visual_hunt_records_success_timeout_semantics_and_actual_layout(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    config = VisualHuntConfig(
        preview_trial_count=1,
        popout_trial_count=1,
        catch_trial_count=1,
        distractor_count=2,
        target_preview_ms=500,
        interstimulus_ms=250,
        dwell_time_ms=250,
        trial_duration_seconds=3,
        reward_animation_ms=500,
        sound_enabled=False,
        randomization_seed=2_026_072_302,
    )
    store = ImageLibraryStore(tmp_path / "images")
    task = VisualHuntTask(config, store)
    task.resize(1_000, 700)
    qtbot.addWidget(task)
    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=tmp_path / "hunt-session",
        patient_id="Beta00",
        session_id="hunt-session",
        task_kind="gaze_games",
    )
    sequence = 0
    observed_roles: set[str] = set()

    task.start(monotonic_ns())

    while task.phase is not VisualHuntPhase.COMPLETED:
        _advance_hunt_to_array(task)

        if task.phase is VisualHuntPhase.COMPLETED:
            break

        trial = task.current_trial
        assert task.phase_deadline_ns is not None
        array_started_ns = task.phase_deadline_ns - config.trial_duration_seconds * 1_000_000_000
        context = task.recording_context_for_sample(
            gaze_sample(sequence, array_started_ns, x=0.5, y=0.5)
        )
        context_aois = context["aois"]
        assert isinstance(context_aois, tuple)
        observed_roles.update(str(aoi["role"]) for aoi in context_aois)
        rectangles = task.array_rectangles_normalized()
        position = trial.target_position if trial.target_position is not None else 0
        center = rectangles[position].center()

        for offset_ms in (0, 250):
            runtime.handle_sample(
                gaze_sample(
                    sequence,
                    array_started_ns + offset_ms * 1_000_000,
                    x=center.x(),
                    y=center.y(),
                )
            )
            sequence += 1

        task.expire_current_phase()

    runtime.finish("test_complete")
    document = result_document(runtime)
    result = document["result"]
    trials = result["trials"]

    assert result["completion_status"] == "completed"
    assert result["star_count"] == 2
    assert len(trials) == 3
    assert result["configuration"]["catch_trial_count"] == config.catch_trial_count
    assert {"correct_option", "incorrect_option", "other"} <= observed_roles
    assert all(trial["array_layout"] for trial in trials)
    assert all(trial["array_stimulus_ids"] for trial in trials)
    assert (
        next(trial for trial in trials if trial["condition"] == VisualHuntCondition.CATCH.value)[
            "target_position"
        ]
        is None
    )

    event_types = recorded_event_types(runtime)
    assert event_types.count("target_acquired") == 2
    assert event_types.count("catch_false_selection") == 1
    assert "incorrect" not in event_types


def test_visual_preference_timing_is_not_gaze_contingent_and_keeps_low_quality(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    pairs = (
        PreferencePair("fruit", "banana", "apple", "水果"),
        PreferencePair("familiar", "lion", "car", "熟悉事物"),
    )
    config = VisualPreferenceConfig(
        pair_ids=tuple(pair.pair_id for pair in pairs),
        pairs=pairs,
        presentation_seconds=3,
        center_cue_ms=0,
        intertrial_ms=500,
        sound_intro_enabled=False,
        minimum_trial_valid_ratio=0.75,
        randomization_seed=2_026_072_303,
    )
    store = ImageLibraryStore(tmp_path / "images")
    task = VisualPreferenceTask(config, store)
    task.resize(1_000, 700)
    qtbot.addWidget(task)
    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=tmp_path / "preference-session",
        patient_id="Beta00",
        session_id="preference-session",
        task_kind="visual_preference",
    )
    sequence = 0
    task.start(monotonic_ns())
    task.expire_current_phase()

    while task.phase is not VisualPreferencePhase.COMPLETED:
        assert task.phase is VisualPreferencePhase.PAIR_VISIBLE
        assert task.phase_deadline_ns is not None
        fixed_deadline_ns = task.phase_deadline_ns
        pair_started_ns = fixed_deadline_ns - config.presentation_seconds * 1_000_000_000
        left, right = task.pair_rectangles_normalized()
        context = task.recording_context_for_sample(
            gaze_sample(sequence, pair_started_ns, x=left.center().x(), y=left.center().y())
        )
        context_aois = context["aois"]
        assert isinstance(context_aois, tuple)
        assert [aoi["role"] for aoi in context_aois] == [
            "target",
            "target",
            "other",
        ]

        runtime.handle_sample(
            gaze_sample(
                sequence,
                pair_started_ns + 100_000_000,
                x=left.center().x(),
                y=left.center().y(),
            )
        )
        sequence += 1
        runtime.handle_sample(
            gaze_sample(
                sequence,
                pair_started_ns + 1_000_000_000,
                x=(None if sequence == 1 else right.center().x()),
                y=(None if sequence == 1 else right.center().y()),
            )
        )
        sequence += 1

        assert task.phase is VisualPreferencePhase.PAIR_VISIBLE
        assert task.phase_deadline_ns == fixed_deadline_ns
        task.expire_current_phase()
        task.expire_current_phase()

    runtime.finish("test_complete")
    document = result_document(runtime)
    result = document["result"]

    assert result["completion_status"] == "completed"
    assert len(result["trials"]) == 4
    assert result["configuration"]["pair_ids"] == ["fruit", "familiar"]
    assert any(trial["quality"] == "low_validity" for trial in result["trials"])
    assert {trial["left_image_id"] for trial in result["trials"]} == {
        "banana",
        "apple",
        "lion",
        "car",
    }

    forbidden = ("selected", "correct", "incorrect", "recognized", "reward")
    event_types = recorded_event_types(runtime)
    assert not any(token in event_type for event_type in event_types for token in forbidden)

    assert runtime.run_directory is not None
    table = pq.read_table(runtime.run_directory / "gaze_events.parquet")
    assert table.num_rows == 8
    assert set(table.column("aoi_role").to_pylist()) <= {"target", "other", None}


def test_escape_stops_all_three_patient_task_timers(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    store = ImageLibraryStore(tmp_path / "images")
    pairs = (
        PreferencePair("fruit", "banana", "apple", "水果"),
        PreferencePair("familiar", "lion", "car", "熟悉事物"),
    )
    tasks = (
        GazeContingencyTask(GazeContingencyConfig(sound_enabled=False, randomization_seed=11)),
        VisualHuntTask(
            VisualHuntConfig(
                preview_trial_count=1,
                popout_trial_count=1,
                catch_trial_count=0,
                sound_enabled=False,
                randomization_seed=12,
            ),
            store,
        ),
        VisualPreferenceTask(
            VisualPreferenceConfig(
                pair_ids=tuple(pair.pair_id for pair in pairs),
                pairs=pairs,
                sound_intro_enabled=False,
                randomization_seed=13,
            ),
            store,
        ),
    )

    for task in tasks:
        qtbot.addWidget(task)
        task.show()
        task.start()
        assert task._timer.isActive()
        qtbot.keyClick(task, Qt.Key.Key_Escape)
        assert not task._timer.isActive()
