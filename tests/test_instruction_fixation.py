from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic_ns
from typing import cast

from pytestqt.qtbot import QtBot

from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample
from oculidoc.tasks.instruction_fixation import (
    POSITION_CENTERS,
    FixationCondition,
    InstructionFixationConfig,
    InstructionFixationTask,
    instruction_fixation_protocol,
)


def make_sample(sequence: int, *, timestamp_ns: int, x: float, y: float) -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=timestamp_ns,
            utc_timestamp=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
            source_timestamp_ns=timestamp_ns,
            source_clock_id="instruction-fixation-test",
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=True,
        right_eye_valid=True,
    )


def test_protocol_contains_configured_conditions_and_separates_positions() -> None:
    config = InstructionFixationConfig(
        position_ids=("top_left", "top_right", "bottom_left", "bottom_right"),
        target_only_trial_count=2,
        distractor_trial_count=2,
        no_target_trial_count=1,
        distractor_count=2,
        randomization_seed=17,
    )

    first = instruction_fixation_protocol(config)
    second = instruction_fixation_protocol(config)

    assert first == second
    assert [trial.condition for trial in first].count(FixationCondition.TARGET_ONLY) == 2
    assert [trial.condition for trial in first].count(FixationCondition.DISTRACTOR) == 2
    assert [trial.condition for trial in first].count(FixationCondition.NO_TARGET) == 1

    for trial in first:
        assert trial.target_position not in trial.distractor_positions
        assert len(set(trial.distractor_positions)) == len(trial.distractor_positions)


def test_target_dwell_records_entry_and_stable_fixation(qtbot: QtBot) -> None:
    task = InstructionFixationTask(
        InstructionFixationConfig(
            position_ids=("center",),
            target_only_trial_count=1,
            distractor_trial_count=0,
            no_target_trial_count=0,
            dwell_time_ms=250,
            randomization_seed=3,
        )
    )
    task.resize(1_000, 700)
    qtbot.addWidget(task)
    task.start()
    base = monotonic_ns()
    x, y = POSITION_CENTERS["center"]

    for sequence, offset_ms in enumerate((0, 100, 200, 300)):
        task.consume_sample(
            make_sample(
                sequence,
                timestamp_ns=base + offset_ms * 1_000_000,
                x=x,
                y=y,
            )
        )

    result = task.recording_result("completed")
    trials = cast(list[dict[str, object]], result["trials"])
    trial = trials[0]
    acquired_ms = trial["first_target_acquired_ms"]

    assert trial["target_acquired"] is True
    assert trial["first_target_entry_ms"] is not None
    assert isinstance(acquired_ms, (int, float))
    assert acquired_ms >= 250
    assert result["target_acquisition_ratio"] == 1.0
    assert result["randomization_seed"] == 3
    assert {event["event_type"] for event in task.drain_recording_events()} >= {
        "stimulus_presented",
        "aoi_entered",
        "selection_committed",
        "trial_completed",
    }
    completed: list[bool] = []
    task.protocol_completed.connect(lambda: completed.append(True))
    task.advance_after_feedback()

    assert completed == [True]
    assert task.recording_result("completed")["completion_status"] == "completed"
    task.stop()


def test_stop_preserves_partial_current_trial_for_recording(qtbot: QtBot) -> None:
    task = InstructionFixationTask(
        InstructionFixationConfig(
            position_ids=("center",),
            target_only_trial_count=1,
            distractor_trial_count=0,
            no_target_trial_count=0,
            randomization_seed=11,
        )
    )
    qtbot.addWidget(task)
    task.start()
    task.stop()
    result = task.recording_result("manual_exit")
    trials = cast(list[dict[str, object]], result["trials"])

    assert result["completion_status"] == "partial"
    assert len(trials) == 1
    assert trials[0]["completion_reason"] == "manual_exit"


def test_no_target_trial_records_distractor_fixation_without_scoring(qtbot: QtBot) -> None:
    task = InstructionFixationTask(
        InstructionFixationConfig(
            position_ids=("top_left", "bottom_right"),
            target_only_trial_count=0,
            distractor_trial_count=0,
            no_target_trial_count=1,
            distractor_count=1,
            dwell_time_ms=250,
            randomization_seed=5,
        )
    )
    task.resize(1_000, 700)
    qtbot.addWidget(task)
    task.start()
    base = monotonic_ns()
    position = task.current_trial.distractor_positions[0]
    x, y = POSITION_CENTERS[position]

    for sequence, offset_ms in enumerate((0, 100, 200, 300)):
        task.consume_sample(
            make_sample(
                sequence,
                timestamp_ns=base + offset_ms * 1_000_000,
                x=x,
                y=y,
            )
        )

    task.expire_current_trial(timestamp_ns=base + 500_000_000)
    result = task.recording_result("completed")

    assert result["no_target_trial_count"] == 1
    assert result["no_target_false_fixation_count"] == 1
    assert result["interpretation"] == "descriptive_command_following_evidence_only"
    trials = cast(list[dict[str, object]], result["trials"])
    assert trials[0]["outcome"] == "distractor_fixation_observed"
    task.stop()


def test_recording_context_marks_target_and_distractors(qtbot: QtBot) -> None:
    task = InstructionFixationTask(
        InstructionFixationConfig(
            position_ids=("top_left", "top_right", "bottom_left"),
            target_only_trial_count=0,
            distractor_trial_count=1,
            no_target_trial_count=0,
            distractor_count=2,
            randomization_seed=9,
        )
    )
    task.resize(1_000, 700)
    qtbot.addWidget(task)
    context = task.recording_context_for_sample(
        make_sample(0, timestamp_ns=monotonic_ns(), x=0.5, y=0.5)
    )
    aois = cast(list[dict[str, object]], context["aois"])
    reference = cast(dict[str, object], context["reference_aoi"])
    metadata = cast(dict[str, object], context["question_metadata"])
    roles = [aoi["role"] for aoi in aois]

    assert roles.count("target") == 1
    assert roles.count("incorrect_option") == 2
    assert reference["role"] == "target"
    assert metadata["condition"] == "target_with_distractors"
