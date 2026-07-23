"""Tests for configurable gaze-driven tasks."""

from datetime import UTC, datetime
from time import monotonic_ns

from PySide6.QtWidgets import QSizePolicy
from pytestqt.qtbot import QtBot

from oculidoc.config import Settings
from oculidoc.devices.contracts import (
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.devices.simulated import (
    SimulatedEyeTrackerDevice,
)
from oculidoc.devices.tobii_hospital_bridge import (
    TobiiHospitalBridgeDevice,
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
            tobii_bridge_mode="client",
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


def test_horizontal_and_vertical_paths_support_three_screen_positions(
    qtbot: QtBot,
) -> None:
    horizontal = TrackingBallTask(
        TrackingBallConfig(path=TargetPath.HORIZONTAL, horizontal_position="top")
    )
    vertical = TrackingBallTask(
        TrackingBallConfig(path=TargetPath.VERTICAL, vertical_position="right")
    )
    qtbot.addWidget(horizontal)
    qtbot.addWidget(vertical)

    assert horizontal.target_center_normalized(0.0)[1] == 0.20
    assert vertical.target_center_normalized(0.0)[0] == 0.80


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


def test_random_tracking_path_is_smooth(
    qtbot: QtBot,
) -> None:
    task = TrackingBallTask(TrackingBallConfig(path=TargetPath.RANDOM))
    qtbot.addWidget(task)

    previous = None

    for index in range(120):
        phase = index * 0.04
        x, y = task.target_center_normalized(phase)

        assert 0.0 <= x <= 1.0
        assert 0.0 <= y <= 1.0

        if previous is not None:
            previous_x, previous_y = previous

            assert abs(x - previous_x) < 0.08
            assert abs(y - previous_y) < 0.08

        previous = (x, y)


def test_binary_question_uses_question_font(
    qtbot: QtBot,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="请看左边还是右边？",
            left_answer="左边",
            right_answer="右边",
            question_font_family="Arial",
            question_font_size_pt=48,
        )
    )
    qtbot.addWidget(task)

    assert task.config.question_font_family == "Arial"
    assert task.config.question_font_size_pt == 48
    assert "Arial" in task.question_label.styleSheet()
    assert "48pt" in task.question_label.styleSheet()
    assert task.left_button.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Expanding
    assert task.right_button.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Expanding


def test_create_hospital_tobii_bridge() -> None:
    device = create_eye_tracker(
        Settings(
            gaze_source=("tobii_legacy_bridge"),
            tobii_bridge_mode=("hospital_server"),
            tobii_bridge_bind_host=("127.0.0.1"),
            tobii_bridge_port=9999,
            tobii_screen_width_px=1920,
            tobii_screen_height_px=1080,
        )
    )

    assert isinstance(
        device,
        TobiiHospitalBridgeDevice,
    )
    assert device.screen_width_px == 1920
    assert device.screen_height_px == 1080
