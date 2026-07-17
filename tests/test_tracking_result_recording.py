"""Tracking-ball semantic event and result tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

from oculidoc.devices.contracts import (
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.experiments.task_runtime import (
    RecordedTaskRuntime,
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
    timestamp_ns: int,
    x: float | None,
    y: float | None = 0.5,
    valid: bool = True,
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
            source_clock_id=("tracking-result-test"),
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=valid,
        right_eye_valid=valid,
    )


def tracking_task(
    qtbot: QtBot,
    monkeypatch: MonkeyPatch,
) -> TrackingBallTask:
    task = TrackingBallTask(
        TrackingBallConfig(
            path=TargetPath.HORIZONTAL,
            effect=TargetEffect.NONE,
            diameter_px=100,
            period_seconds=12.0,
            dwell_time_ms=400,
            dwell_hit_radius_scale=1.0,
        )
    )
    task.resize(1_000, 500)
    qtbot.addWidget(task)
    monkeypatch.setattr(
        task,
        "_phase",
        lambda: 0.0,
    )
    return task


def transition_samples() -> tuple[EyeTrackerSample, ...]:
    return (
        make_sample(
            sequence=0,
            timestamp_ns=1_000_000_000,
            x=0.5,
        ),
        make_sample(
            sequence=1,
            timestamp_ns=1_200_000_000,
            x=0.5,
        ),
        make_sample(
            sequence=2,
            timestamp_ns=1_400_000_000,
            x=0.5,
        ),
        make_sample(
            sequence=3,
            timestamp_ns=1_600_000_000,
            x=0.9,
        ),
        make_sample(
            sequence=4,
            timestamp_ns=1_800_000_000,
            x=0.5,
        ),
        make_sample(
            sequence=5,
            timestamp_ns=2_000_000_000,
            x=0.5,
        ),
        make_sample(
            sequence=6,
            timestamp_ns=2_200_000_000,
            x=0.5,
        ),
    )


def event_types(
    events: tuple[
        dict[str, object],
        ...,
    ],
) -> list[str]:
    return [str(event["event_type"]) for event in events]


def test_tracking_task_records_acquire_loss_and_resume(
    qtbot: QtBot,
    monkeypatch: MonkeyPatch,
) -> None:
    task = tracking_task(
        qtbot,
        monkeypatch,
    )

    for sample in transition_samples():
        task.consume_sample(sample)

    events = task.drain_recording_events()

    assert event_types(events) == [
        "tracking_started",
        "target_entered",
        "target_acquired",
        "target_lost",
        "target_entered",
        "tracking_resumed",
    ]

    result = task.recording_result("timeout")
    final_events = task.drain_recording_events()

    assert event_types(final_events) == ["tracking_completed"]
    assert result["completion_status"] == "completed"
    assert result["sample_count"] == 7
    assert result["valid_sample_count"] == 7
    assert result["target_inside_sample_count"] == 6
    assert result["target_hit_ratio"] == (6 / 7)
    assert result["valid_tracking_duration_ms"] == 1_200.0
    assert result["target_inside_duration_ms"] == 1_000.0
    assert result["longest_continuous_tracking_ms"] == 600.0
    assert result["target_loss_count"] == 1
    assert result["target_reacquisition_count"] == 1
    assert result["first_target_entry_ms"] == 0.0
    assert result["first_target_acquired_ms"] == 400.0
    assert result["target_acquired_at_finish"] is True
    normalized_error = result["tracking_error_normalized"]

    assert normalized_error["sample_count"] == 7
    assert normalized_error["mean"] > 0
    assert normalized_error["p95"] > 0


def test_runtime_persists_tracking_events_and_result(
    qtbot: QtBot,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = tracking_task(
        qtbot,
        monkeypatch,
    )
    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=(tmp_path / "tracking-session"),
        patient_id="patient",
        session_id="tracking-session",
    )

    for sample in transition_samples():
        runtime.handle_sample(sample)

    runtime.finish("timeout")

    assert runtime.run_directory is not None
    result_document = json.loads(
        (runtime.run_directory / "task_result.json").read_text(encoding="utf-8")
    )
    result = result_document["result"]

    assert result_document["end_reason"] == "timeout"
    assert result["completion_status"] == "completed"
    assert result["completion_reason"] == ("timeout")
    assert result["sample_count"] == 7
    assert result["target_loss_count"] == 1
    assert result["target_reacquisition_count"] == 1
    assert result["first_target_acquired_ms"] == 400.0
    assert result["recording_failed"] is False

    events = [
        json.loads(line)
        for line in (runtime.run_directory / "task_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert [event["event_type"] for event in events] == [
        "tracking_started",
        "target_entered",
        "target_acquired",
        "target_lost",
        "target_entered",
        "tracking_resumed",
        "tracking_completed",
    ]
    lost = next(event for event in events if event["event_type"] == "target_lost")

    assert lost["payload"]["reason"] == "outside_target"
    assert lost["payload"]["continuous_inside_ms"] == 600.0


def test_tracking_manual_exit_is_interrupted(
    qtbot: QtBot,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = tracking_task(
        qtbot,
        monkeypatch,
    )
    runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        session_directory=(tmp_path / "interrupted-session"),
        patient_id="patient",
        session_id=("interrupted-session"),
    )

    runtime.finish("manual_exit")

    assert runtime.run_directory is not None
    result_document = json.loads(
        (runtime.run_directory / "task_result.json").read_text(encoding="utf-8")
    )
    result = result_document["result"]

    assert result["completion_status"] == "interrupted"
    assert result["completion_reason"] == "manual_exit"
    assert result["sample_count"] == 0
    assert result["target_hit_ratio"] == 0.0

    events = [
        json.loads(line)
        for line in (runtime.run_directory / "task_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert [event["event_type"] for event in events] == [
        "tracking_started",
        "tracking_interrupted",
    ]
