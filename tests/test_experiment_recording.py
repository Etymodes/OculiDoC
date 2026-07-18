"""Tests for normalized gaze experiment recording."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from oculidoc.devices.contracts import (
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.experiments.recording import (
    AoiRole,
    NormalizedAoi,
    RecorderState,
    ScreenContext,
    TaskRunRecorder,
)


def make_sample(
    *,
    sequence: int,
    timestamp_ns: int,
    x: float | None,
    y: float | None,
    valid: bool,
) -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=timestamp_ns,
            utc_timestamp=datetime(
                2026,
                7,
                17,
                12,
                0,
                tzinfo=UTC,
            ),
            source_timestamp_ns=timestamp_ns,
            source_clock_id="test-eye-tracker",
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=valid,
        right_eye_valid=valid,
    )


def test_normalized_aoi_is_resolution_independent() -> None:
    aoi = NormalizedAoi(
        aoi_id="left-answer",
        role=AoiRole.CORRECT_OPTION,
        left=0.0,
        top=0.1,
        right=0.45,
        bottom=0.9,
    )

    full_hd = ScreenContext(
        screen_width_px=1920,
        screen_height_px=1080,
    )
    four_k = ScreenContext(
        screen_width_px=3840,
        screen_height_px=2160,
    )

    assert aoi.contains(0.25, 0.5)
    assert full_hd.to_dict()["screen_width_px"] == 1920
    assert four_k.to_dict()["screen_width_px"] == 3840


def test_recorder_writes_gaze_and_aoi_summary(
    tmp_path: Path,
) -> None:
    recorder = TaskRunRecorder(
        session_directory=tmp_path / "session",
        patient_id="patient-uuid",
        session_id="session-uuid",
        task_kind="binary_horizontal",
        task_config={
            "dwell_time_ms": 1200,
            "question_count": 10,
        },
        screen_context=ScreenContext(
            screen_width_px=1920,
            screen_height_px=1080,
            device_pixel_ratio=1.25,
            dpi_x=120.0,
            dpi_y=120.0,
        ),
        run_id="run-uuid",
        task_started_monotonic_ns=(1_000_000_000),
    )

    recorder.register_question(
        "question-1",
        aois=[
            NormalizedAoi(
                aoi_id="left",
                role=(AoiRole.CORRECT_OPTION),
                left=0.0,
                top=0.1,
                right=0.45,
                bottom=0.9,
                label="是",
            ),
            NormalizedAoi(
                aoi_id="right",
                role=(AoiRole.INCORRECT_OPTION),
                left=0.55,
                top=0.1,
                right=1.0,
                bottom=0.9,
                label="否",
            ),
        ],
        metadata={
            "correct_side": "left",
            "layout": "horizontal",
        },
    )

    recorder.record_event(
        "question_presented",
        monotonic_timestamp_ns=(1_000_000_000),
        payload={"question_id": "question-1"},
    )

    samples = [
        make_sample(
            sequence=0,
            timestamp_ns=1_100_000_000,
            x=0.20,
            y=0.50,
            valid=True,
        ),
        make_sample(
            sequence=1,
            timestamp_ns=1_300_000_000,
            x=0.25,
            y=0.50,
            valid=True,
        ),
        make_sample(
            sequence=2,
            timestamp_ns=1_500_000_000,
            x=0.80,
            y=0.50,
            valid=True,
        ),
        make_sample(
            sequence=3,
            timestamp_ns=1_700_000_000,
            x=0.50,
            y=0.05,
            valid=True,
        ),
        make_sample(
            sequence=4,
            timestamp_ns=1_900_000_000,
            x=None,
            y=None,
            valid=False,
        ),
    ]

    for sample in samples:
        recorder.record_sample(
            sample,
            question_id="question-1",
            phase="response",
        )

    summary = recorder.finish(
        reason="answered",
        result={
            "selected_side": "left",
            "correct": True,
        },
    )

    assert recorder.state is (RecorderState.FINISHED)
    assert summary["sample_count"] == 5
    assert summary["valid_sample_count"] == 4
    assert summary["invalid_sample_count"] == 1
    assert summary["valid_sample_ratio"] == pytest.approx(0.8)
    assert summary["first_valid_reaction_ms"] == pytest.approx(100.0)
    assert summary["role_switch_count"] == 2

    dwell_by_role = summary["dwell_by_role_ms"]

    assert dwell_by_role[AoiRole.CORRECT_OPTION.value] == pytest.approx(400.0)
    assert dwell_by_role[AoiRole.INCORRECT_OPTION.value] == pytest.approx(200.0)
    assert dwell_by_role[AoiRole.NON_OPTION.value] == pytest.approx(200.0)

    table = pq.read_table(recorder.gaze_events_path)

    assert table.num_rows == 5
    assert {
        "gaze_x_normalized",
        "gaze_y_normalized",
        "aoi_role",
        "duration_ms",
    }.issubset(table.column_names)

    result_document = json.loads(recorder.result_path.read_text(encoding="utf-8"))
    assert result_document["end_reason"] == "answered"
    assert result_document["result"]["correct"] is True

    layouts = json.loads(
        (recorder.run_directory / "question_layouts.json").read_text(encoding="utf-8")
    )
    assert layouts["questions"][0]["metadata"]["correct_side"] == "left"


def test_recorder_rejects_samples_after_finish(
    tmp_path: Path,
) -> None:
    recorder = TaskRunRecorder(
        session_directory=tmp_path,
        patient_id="patient",
        session_id="session",
        task_kind="tracking_ball",
        task_config={},
        screen_context=ScreenContext(
            screen_width_px=1280,
            screen_height_px=720,
        ),
        run_id="finished-run",
    )

    recorder.finish(reason="timeout")

    with pytest.raises(
        RuntimeError,
        match="already finished",
    ):
        recorder.record_sample(
            make_sample(
                sequence=0,
                timestamp_ns=1,
                x=0.5,
                y=0.5,
                valid=True,
            )
        )
