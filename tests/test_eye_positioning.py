"""Tests for the clean-room eye-position display calculations."""

from datetime import UTC, datetime

import pytest

from oculidoc.application.eye_positioning import (
    EyePositioningParametersCalculator,
)
from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample


def _sample(
    *,
    left: tuple[float, float, float] | None = None,
    right: tuple[float, float, float] | None = None,
    left_mm: tuple[float, float, float] | None = None,
    right_mm: tuple[float, float, float] | None = None,
    sequence: int = 0,
) -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=sequence,
            utc_timestamp=datetime.now(UTC),
        ),
        gaze_x_normalized=None,
        gaze_y_normalized=None,
        left_eye_valid=left is not None,
        right_eye_valid=right is not None,
        left_eye_position_normalized=left,
        right_eye_position_normalized=right,
        left_eye_position_mm=left_mm,
        right_eye_position_mm=right_mm,
    )


def test_positioning_mirrors_horizontal_axis_and_extrapolates_short_dropout() -> None:
    calculator = EyePositioningParametersCalculator()

    first = calculator.receive_gaze_data(_sample(left=(0.2, 0.3, 0.4), right=(0.8, 0.5, 0.6)))
    second = calculator.receive_gaze_data(
        _sample(left=(0.3, 0.4, 0.4), right=(0.7, 0.6, 0.6), sequence=1)
    )
    dropout = calculator.receive_gaze_data(_sample(sequence=2))

    assert first.left_eye_position == pytest.approx((0.8, 0.3))
    assert first.right_eye_position == pytest.approx((0.2, 0.5))
    assert second.left_eye_position == pytest.approx((0.7, 0.4))
    assert dropout.left_eye_position == pytest.approx((0.6, 0.5))
    assert dropout.right_eye_position == pytest.approx((0.4, 0.7))
    assert dropout.left_eye_extrapolated is True
    assert dropout.right_eye_extrapolated is True


def test_positioning_stops_extrapolating_after_a_ten_sample_history() -> None:
    calculator = EyePositioningParametersCalculator()
    calculator.receive_gaze_data(_sample(left=(0.2, 0.3, 0.4)))
    calculator.receive_gaze_data(_sample(left=(0.3, 0.4, 0.4), sequence=1))

    state = None
    for sequence in range(2, 11):
        state = calculator.receive_gaze_data(_sample(sequence=sequence))

    assert state is not None
    assert state.left_eye_position is None
    assert state.left_eye_extrapolated is False


def test_positioning_smooths_physical_distance_and_rejects_known_sentinels() -> None:
    calculator = EyePositioningParametersCalculator()

    first = calculator.receive_gaze_data(
        _sample(
            left=(0.4, 0.5, 0.5),
            right=(0.6, 0.5, 0.5),
            left_mm=(-30.0, 0.0, 600.0),
            right_mm=(30.0, 0.0, 620.0),
        )
    )
    second = calculator.receive_gaze_data(
        _sample(
            left=(0.4, 0.5, 0.5),
            right=(0.6, 0.5, 0.5),
            left_mm=(-30.0, 0.0, 5.504),
            right_mm=(30.0, 0.0, 700.0),
            sequence=1,
        )
    )

    assert first.distance_mm == pytest.approx(610.0)
    assert first.is_distance_in_range is True
    assert second.distance_mm == pytest.approx(655.0)
    assert second.is_distance_in_range is True


def test_positioning_clamps_head_angle_to_original_twenty_degree_limit() -> None:
    calculator = EyePositioningParametersCalculator()

    state = calculator.receive_gaze_data(
        _sample(
            left=(0.8, 0.0, 0.5),
            right=(0.2, 1.0, 0.5),
        )
    )

    assert state.head_angle_degrees == 20.0
