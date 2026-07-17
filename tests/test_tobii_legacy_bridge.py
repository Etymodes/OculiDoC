"""Tests for the legacy Tobii TCP bridge."""

import json
import socket
from datetime import UTC, datetime
from threading import Thread

import pytest

from oculidoc.devices.contracts import (
    DeviceState,
)
from oculidoc.devices.tobii_legacy_bridge import (
    TobiiLegacyBridgeDevice,
    parse_tobii_bridge_payload,
)


def test_parse_normalized_gaze_payload() -> None:
    sample = parse_tobii_bridge_payload(
        {
            "sequence": 12,
            "gaze_x_normalized": 0.25,
            "gaze_y_normalized": 0.75,
            "left_eye_valid": True,
            "right_eye_valid": False,
            "left_pupil_diameter_mm": 3.4,
            "timestamp_us": 123_456,
            "utc_timestamp": ("2026-07-17T12:00:00Z"),
        },
        fallback_sequence=0,
    )

    assert sample.timestamp.sequence == 12
    assert sample.timestamp.source_timestamp_ns == 123_456_000
    assert sample.timestamp.utc_timestamp == (
        datetime(
            2026,
            7,
            17,
            12,
            0,
            tzinfo=UTC,
        )
    )
    assert sample.gaze_x_normalized == 0.25
    assert sample.gaze_y_normalized == 0.75
    assert sample.left_eye_valid is True
    assert sample.right_eye_valid is False
    assert sample.left_pupil_diameter_mm == 3.4


def test_parse_pixel_gaze_payload() -> None:
    sample = parse_tobii_bridge_payload(
        {
            "x_px": 960,
            "y_px": 540,
            "screen_width_px": 1920,
            "screen_height_px": 1080,
            "valid": True,
        },
        fallback_sequence=5,
    )

    assert sample.timestamp.sequence == 5
    assert sample.gaze_x_normalized == 0.5
    assert sample.gaze_y_normalized == 0.5
    assert sample.gaze_valid is True


def test_bridge_reads_newline_json() -> None:
    listener = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    port = listener.getsockname()[1]
    payload = {
        "type": "gaze",
        "sequence": 3,
        "x": 0.2,
        "y": 0.8,
        "valid": True,
    }

    def serve() -> None:
        connection, _ = listener.accept()

        with connection:
            connection.sendall((json.dumps(payload) + "\n").encode("utf-8"))

        listener.close()

    server = Thread(
        target=serve,
        daemon=True,
    )
    server.start()

    device = TobiiLegacyBridgeDevice(
        host="127.0.0.1",
        port=port,
        read_timeout_seconds=1.0,
    )

    device.connect()
    assert device.state is DeviceState.CONNECTED

    device.start_stream()
    sample = device.read_sample()

    assert sample.timestamp.sequence == 3
    assert sample.gaze_x_normalized == 0.2
    assert sample.gaze_y_normalized == 0.8

    device.stop_stream()
    device.disconnect()
    server.join(timeout=2.0)

    assert device.state is DeviceState.DISCONNECTED


def test_bridge_rejects_invalid_port() -> None:
    with pytest.raises(ValueError):
        TobiiLegacyBridgeDevice(port=0)
