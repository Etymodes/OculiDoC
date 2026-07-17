"""Tests for the native Tobii Stream Engine adapter."""

import ctypes
from pathlib import Path

from oculidoc.config import Settings
from oculidoc.devices.tobii_stream_engine import (
    TobiiGazePoint,
    TobiiStreamEngineDevice,
    TobiiVector2,
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


def test_factory_creates_native_tobii_device() -> None:
    device = create_eye_tracker(Settings(gaze_source="tobii_stream_engine"))

    assert isinstance(
        device,
        TobiiStreamEngineDevice,
    )
