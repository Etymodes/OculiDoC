"""Tests for configurable gaze-driven tasks."""

from datetime import UTC, datetime
from time import monotonic_ns

from pytestqt.qtbot import QtBot

from oculidoc.config import Settings
from oculidoc.devices.contracts import (
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.devices.simulated import (
    SimulatedEyeTrackerDevice,
)
from oculidoc.devices.tobii_legacy_bridge import (
    TobiiLegacyBridgeDevice,
)
from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionTask,
)
from oculidoc.tasks.gaze_stream import (
    create_eye_tracker,
)
from oculidoc.tasks.tracking_ball import (
    TargetEffect,
    TargetPath,
    TargetShape,
    TrackingBallConfig,
    TrackingBallTask,
)


def gaze_sample(
    x: float,
    y: float,
) -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=0,
            monotonic_timestamp_ns=monotonic_ns(),
            utc_timestamp=datetime.now(UTC),
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=True,
        right_eye_valid=True,
    )


def test_create_mock_eye_tracker() -> None:
    device = create_eye_tracker(Settings(gaze_source="mock"))

    assert isinstance(
        device,
        SimulatedEyeTrackerDevice,
    )


def test_create_tobii_bridge_eye_tracker() -> None:
    device = create_eye_tracker(
        Settings(
            gaze_source="tobii_legacy_bridge",
            tobii_bridge_host="127.0.0.1",
            tobii_bridge_port=4567,
        )
    )

    assert isinstance(
        device,
        TobiiLegacyBridgeDevice,
    )
    assert device.host == "127.0.0.1"
    assert device.port == 4567


def test_tracking_target_is_configurable(
    qtbot: QtBot,
) -> None:
    config = TrackingBallConfig(
        shape=TargetShape.STAR,
        effect=TargetEffect.SPIN,
        path=TargetPath.FIGURE_EIGHT,
        diameter_px=144,
        color="#33ccff",
        period_seconds=4.5,
    )
    task = TrackingBallTask(config)
    qtbot.addWidget(task)

    task.resize(1_000, 600)
    task.consume_sample(gaze_sample(0.25, 0.75))

    assert task.config.shape is TargetShape.STAR
    assert task.config.effect is TargetEffect.SPIN
    assert task.config.path is (TargetPath.FIGURE_EIGHT)
    assert task.config.diameter_px == 144
    assert task.last_gaze_normalized == (
        0.25,
        0.75,
    )


def test_tracking_paths_remain_normalized(
    qtbot: QtBot,
) -> None:
    task = TrackingBallTask(TrackingBallConfig(path=TargetPath.CIRCLE))
    qtbot.addWidget(task)

    for phase in (
        0.0,
        1.0,
        2.0,
        3.0,
    ):
        x, y = task.target_center_normalized(phase)

        assert 0.0 <= x <= 1.0
        assert 0.0 <= y <= 1.0


def test_binary_question_selects_by_dwell(
    qtbot: QtBot,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="是否听到声音？",
            left_answer="是",
            right_answer="否",
            dwell_time_ms=750,
        )
    )
    qtbot.addWidget(task)

    with qtbot.waitSignal(
        task.answered,
        timeout=1_000,
    ) as signal:
        task.advance_dwell(
            "left",
            400,
        )
        task.advance_dwell(
            "left",
            400,
        )

    assert signal.args == [
        "left",
        "是",
    ]
    assert task.result == (
        "left",
        "是",
    )


def test_binary_question_resets_in_neutral_zone(
    qtbot: QtBot,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="继续吗？",
            left_answer="继续",
            right_answer="停止",
            dwell_time_ms=1_000,
        )
    )
    qtbot.addWidget(task)

    task.advance_dwell(
        "right",
        600,
    )
    task.advance_dwell(
        None,
        100,
    )
    task.advance_dwell(
        "right",
        500,
    )

    assert task.result is None
    assert task.right_progress.value() == 500
