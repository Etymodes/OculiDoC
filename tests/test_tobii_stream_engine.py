"""Tests for the native Tobii Stream Engine adapter."""

import ctypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from oculidoc.config import Settings
from oculidoc.devices.contracts import DeviceState
from oculidoc.devices.tobii_stream_engine import (
    TobiiEyePositionNormalized,
    TobiiGazePoint,
    TobiiStreamEngineDevice,
    TobiiVector2,
    TobiiVector3,
    discover_tobii_stream_engine_dll,
    gaze_point_to_sample,
)
from oculidoc.tasks.gaze_stream import (
    create_eye_tracker,
)


def test_discovery_prefers_explicit_path(
    tmp_path: Path,
) -> None:
    library_path = tmp_path / "tobii_stream_engine.dll"
    library_path.write_bytes(b"test")

    assert discover_tobii_stream_engine_dll(library_path) == library_path.resolve()


def test_native_gaze_point_conversion() -> None:
    gaze_point = TobiiGazePoint(
        timestamp_us=123_456,
        validity=1,
        position=TobiiVector2(
            x=ctypes.c_float(0.25),
            y=ctypes.c_float(0.75),
        ),
    )

    sample = gaze_point_to_sample(
        gaze_point,
        sequence=7,
    )

    assert sample.timestamp.sequence == 7
    assert sample.timestamp.source_timestamp_ns == 123_456_000
    assert sample.gaze_valid is True
    assert abs(float(sample.gaze_x_normalized) - 0.25) < 0.0001
    assert abs(float(sample.gaze_y_normalized) - 0.75) < 0.0001


def test_invalid_native_gaze_point() -> None:
    gaze_point = TobiiGazePoint(
        timestamp_us=1,
        validity=0,
        position=TobiiVector2(
            x=ctypes.c_float(0.0),
            y=ctypes.c_float(0.0),
        ),
    )

    sample = gaze_point_to_sample(
        gaze_point,
        sequence=0,
    )

    assert sample.gaze_valid is False
    assert sample.gaze_x_normalized is None
    assert sample.gaze_y_normalized is None


def test_native_eye_position_is_attached_to_following_gaze_sample() -> None:
    device = TobiiStreamEngineDevice()
    eye_position = TobiiEyePositionNormalized(
        timestamp_us=100,
        left_validity=1,
        left=TobiiVector3(0.25, 0.45, 0.35),
        right_validity=1,
        right=TobiiVector3(0.75, 0.55, 0.65),
    )
    gaze_point = TobiiGazePoint(
        timestamp_us=101,
        validity=1,
        position=TobiiVector2(0.5, 0.5),
    )

    device._receive_eye_position_normalized(ctypes.pointer(eye_position), 0)
    device._receive_gaze_point(ctypes.pointer(gaze_point), 0)
    sample = device._samples.popleft()

    assert sample.left_eye_position_normalized == pytest.approx((0.25, 0.45, 0.35))
    assert sample.right_eye_position_normalized == pytest.approx((0.75, 0.55, 0.65))


def test_optional_eye_position_stream_cannot_break_native_gaze() -> None:
    class StubDll:
        @staticmethod
        def tobii_gaze_point_subscribe(*args: object) -> int:
            del args
            return 0

        @staticmethod
        def tobii_eye_position_normalized_subscribe(*args: object) -> int:
            del args
            raise OSError("optional stream unavailable")

    device = TobiiStreamEngineDevice()
    device._state = DeviceState.CONNECTED
    device._device = ctypes.c_void_p(1)
    device._library = cast(
        Any,
        SimpleNamespace(
            dll=StubDll(),
            eye_position_normalized_supported=True,
        ),
    )

    device.start_stream()

    assert device.state is DeviceState.STREAMING
    assert device._eye_position_subscribed is False
    assert device.eye_position_stream_status == "subscribe_error"
    assert "OSError" in device.eye_position_stream_detail
    assert "注视点采集继续运行" in device.capability_diagnostics()[0]


def test_optional_eye_position_rejection_preserves_exact_driver_reason() -> None:
    class StubDll:
        @staticmethod
        def tobii_gaze_point_subscribe(*args: object) -> int:
            del args
            return 0

        @staticmethod
        def tobii_eye_position_normalized_subscribe(*args: object) -> int:
            del args
            return 3

    device = TobiiStreamEngineDevice()
    device._state = DeviceState.CONNECTED
    device._device = ctypes.c_void_p(1)
    device._library = cast(
        Any,
        SimpleNamespace(
            dll=StubDll(),
            eye_position_normalized_supported=True,
            error_message=lambda status: f"driver status {status}",
        ),
    )

    device.start_stream()

    assert device.state is DeviceState.STREAMING
    assert device.eye_position_stream_status == "rejected_3"
    assert "不支持" in device.eye_position_stream_detail
    assert "driver status 3" in device.eye_position_stream_detail


def test_factory_creates_native_tobii_device() -> None:
    device = create_eye_tracker(Settings(gaze_source="tobii_stream_engine"))

    assert isinstance(
        device,
        TobiiStreamEngineDevice,
    )
