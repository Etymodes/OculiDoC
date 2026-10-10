"""Contract tests for the reserved Seveninvensun SDK bridge."""

from __future__ import annotations

import json
import socket
from threading import Thread

import pytest

from oculidoc.config import Settings
from oculidoc.devices.errors import DeviceReadError
from oculidoc.devices.seveninvensun_bridge import (
    SEVENINVENSUN_BRIDGE_PROTOCOL,
    SeveninvensunBridgeDevice,
    parse_seveninvensun_bridge_payload,
)
from oculidoc.tasks.gaze_stream import create_eye_tracker


def _screen_bar_frame() -> dict[str, object]:
    return {
        "protocol": SEVENINVENSUN_BRIDGE_PROTOCOL,
        "bridge_status": "ready",
        "device_mode": "screen_bar",
        "device_model": "aSee Pro",
        "serial_number": "SITE-DEVICE-1",
        "calibration_status": "calibrated",
        "sample": {
            "coordinate_space": "screen_normalized",
            "sequence": 8,
            "source_timestamp_ns": 1_234_567,
            "gaze_x_normalized": 0.25,
            "gaze_y_normalized": 0.75,
            "left_eye_valid": True,
            "right_eye_valid": False,
            "left_pupil_diameter_mm": 3.2,
        },
    }


def test_parse_screen_bar_frame_preserves_sdk_timestamp() -> None:
    sample = parse_seveninvensun_bridge_payload(
        _screen_bar_frame(),
        fallback_sequence=0,
    )

    assert sample.timestamp.sequence == 8
    assert sample.timestamp.source_timestamp_ns == 1_234_567
    assert sample.timestamp.source_clock_id == "seveninvensun-sdk"
    assert sample.gaze_x_normalized == 0.25
    assert sample.gaze_y_normalized == 0.75
    assert sample.left_pupil_diameter_mm == 3.2


def test_wearable_scene_coordinates_are_never_used_as_screen_gaze() -> None:
    frame = _screen_bar_frame()
    frame["device_mode"] = "wearable_glasses"
    frame["mapping_status"] = "not_mapped"
    sample = frame["sample"]
    assert isinstance(sample, dict)
    sample["coordinate_space"] = "scene_normalized"

    with pytest.raises(DeviceReadError, match="场景/世界坐标"):
        parse_seveninvensun_bridge_payload(frame, fallback_sequence=0)


def test_bridge_status_explains_missing_vendor_sdk() -> None:
    frame = _screen_bar_frame()
    frame["bridge_status"] = "sdk_missing"

    with pytest.raises(DeviceReadError, match="Windows SDK"):
        parse_seveninvensun_bridge_payload(frame, fallback_sequence=0)


def test_seveninvensun_bridge_only_accepts_loopback() -> None:
    with pytest.raises(ValueError, match="本机回环"):
        SeveninvensunBridgeDevice(host="192.168.1.20")


def test_configured_source_builds_reserved_bridge() -> None:
    device = create_eye_tracker(
        Settings(
            environment="test",
            gaze_source="seveninvensun_bridge",
            tobii_bridge_host="127.0.0.1",
            tobii_bridge_port=8765,
        )
    )

    assert isinstance(device, SeveninvensunBridgeDevice)
    assert device.host == "127.0.0.1"
    assert device.port == 8765


def test_device_reads_local_bridge_and_reports_model() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.sendall((json.dumps(_screen_bar_frame()) + "\n").encode("utf-8"))
        listener.close()

    server = Thread(target=serve, daemon=True)
    server.start()
    device = SeveninvensunBridgeDevice(
        host="127.0.0.1",
        port=port,
        read_timeout_seconds=1.0,
    )

    device.connect()
    device.start_stream()
    sample = device.read_sample()

    assert sample.gaze_valid
    assert device.info.manufacturer == "七鑫易维 / 7invensun"
    assert device.info.model == "aSee Pro"
    assert device.info.serial_number == "SITE-DEVICE-1"
    assert "screen_bar" in device.info.capabilities

    device.stop_stream()
    device.disconnect()
    server.join(timeout=2.0)
