"""Tests for the hospital Tobii TCP protocol."""

import json
import socket
from threading import Thread

from oculidoc.devices.contracts import (
    DeviceState,
)
from oculidoc.devices.tobii_hospital_bridge import (
    TobiiHospitalBridgeDevice,
)


def unused_port() -> int:
    probe = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()

    return port


def test_hospital_bridge_receives_gaze_json() -> None:
    port = unused_port()
    device = TobiiHospitalBridgeDevice(
        host="127.0.0.1",
        port=port,
        screen_width_px=1920,
        screen_height_px=1080,
        read_timeout_seconds=1.0,
    )

    device.connect()
    device.start_stream()

    payload = {
        "RawX": 0.17,
        "RawY": -0.08,
        "ScreenX": 960.0,
        "ScreenY": 540.0,
    }

    def sender() -> None:
        connection = socket.create_connection(
            ("127.0.0.1", port),
            timeout=2.0,
        )

        with connection:
            connection.sendall((json.dumps(payload) + "\n").encode("utf-8"))

    thread = Thread(
        target=sender,
        daemon=True,
    )
    thread.start()

    sample = device.read_sample()

    assert sample.gaze_valid is True
    assert sample.gaze_x_normalized == 0.5
    assert sample.gaze_y_normalized == 0.5
    assert sample.timestamp.source_clock_id == "MCeyegazethesisNET461"

    device.stop_stream()
    device.disconnect()
    thread.join(timeout=2.0)

    assert device.state is (DeviceState.DISCONNECTED)


def test_hospital_bridge_marks_outside_screen_invalid() -> None:
    port = unused_port()
    device = TobiiHospitalBridgeDevice(
        host="127.0.0.1",
        port=port,
        screen_width_px=1920,
        screen_height_px=1080,
        read_timeout_seconds=1.0,
    )

    device.connect()
    device.start_stream()

    def sender() -> None:
        connection = socket.create_connection(
            ("127.0.0.1", port),
            timeout=2.0,
        )

        with connection:
            connection.sendall(
                (
                    json.dumps(
                        {
                            "RawX": 0.0,
                            "RawY": 0.0,
                            "ScreenX": -100.0,
                            "ScreenY": 400.0,
                        }
                    )
                    + "\n"
                ).encode("utf-8")
            )

    thread = Thread(
        target=sender,
        daemon=True,
    )
    thread.start()

    sample = device.read_sample()

    assert sample.gaze_valid is False
    assert sample.gaze_x_normalized is None
    assert sample.gaze_y_normalized is None

    device.stop_stream()
    device.disconnect()
    thread.join(timeout=2.0)
