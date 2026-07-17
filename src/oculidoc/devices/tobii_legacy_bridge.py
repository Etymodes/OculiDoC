"""TCP adapter for a legacy Tobii gaze bridge."""

import json
import socket
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from oculidoc.devices.contracts import (
    DeviceInfo,
    DeviceKind,
    DeviceState,
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.devices.errors import (
    DeviceConnectionError,
    DeviceReadError,
    DeviceStreamEndedError,
    InvalidDeviceStateError,
)

TOBII_BRIDGE_PROTOCOL = "oculidoc-gaze-v1"


def _optional_float(
    value: object,
    *,
    field_name: str,
) -> float | None:
    if value is None:
        return None

    if isinstance(value, bool):
        raise DeviceReadError(f"{field_name} cannot be boolean.")

    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise DeviceReadError(f"{field_name} must be numeric.") from error

    if not isfinite(result):
        raise DeviceReadError(f"{field_name} must be finite.")

    return result


def _first_float(
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> float | None:
    for key in keys:
        if key in payload:
            return _optional_float(
                payload[key],
                field_name=key,
            )

    return None


def _boolean_value(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    *,
    default: bool,
) -> bool:
    for key in keys:
        if key not in payload:
            continue

        value = payload[key]

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in {
                "1",
                "true",
                "valid",
                "yes",
            }:
                return True

            if normalized in {
                "0",
                "false",
                "invalid",
                "no",
            }:
                return False

        raise DeviceReadError(f"{key} must represent a boolean.")

    return default


def _utc_timestamp(
    payload: dict[str, Any],
) -> datetime:
    raw_value = payload.get(
        "utc_timestamp",
        payload.get("timestamp_utc"),
    )

    if raw_value is None:
        return datetime.now(UTC)

    if not isinstance(raw_value, str):
        raise DeviceReadError("utc_timestamp must be ISO-8601 text.")

    normalized = raw_value.strip().replace(
        "Z",
        "+00:00",
    )

    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise DeviceReadError("utc_timestamp is not valid ISO-8601.") from error

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _source_timestamp_ns(
    payload: dict[str, Any],
) -> int | None:
    if "source_timestamp_ns" in payload:
        value = int(payload["source_timestamp_ns"])
    elif "timestamp_us" in payload:
        value = int(payload["timestamp_us"]) * 1_000
    elif "timestamp_ms" in payload:
        value = int(payload["timestamp_ms"]) * 1_000_000
    else:
        return None

    if value < 0:
        raise DeviceReadError("Source timestamps cannot be negative.")

    return value


def _merged_gaze_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(payload)
    nested = payload.get("gaze")

    if isinstance(nested, dict):
        merged.update(nested)

    return merged


def _normalized_coordinates(
    payload: dict[str, Any],
) -> tuple[float | None, float | None]:
    gaze_x = _first_float(
        payload,
        (
            "gaze_x_normalized",
            "gaze_x",
            "x_normalized",
            "x",
        ),
    )
    gaze_y = _first_float(
        payload,
        (
            "gaze_y_normalized",
            "gaze_y",
            "y_normalized",
            "y",
        ),
    )

    if gaze_x is not None and gaze_y is not None:
        return gaze_x, gaze_y

    x_px = _first_float(
        payload,
        ("gaze_x_px", "x_px"),
    )
    y_px = _first_float(
        payload,
        ("gaze_y_px", "y_px"),
    )
    width_px = _first_float(
        payload,
        ("screen_width_px", "width_px"),
    )
    height_px = _first_float(
        payload,
        ("screen_height_px", "height_px"),
    )

    if (
        x_px is not None
        and y_px is not None
        and width_px is not None
        and height_px is not None
        and width_px > 0
        and height_px > 0
    ):
        return (
            x_px / width_px,
            y_px / height_px,
        )

    return gaze_x, gaze_y


def parse_tobii_bridge_payload(
    payload: dict[str, Any],
    *,
    fallback_sequence: int,
) -> EyeTrackerSample:
    """Convert one bridge JSON object into a gaze sample."""
    merged = _merged_gaze_payload(payload)
    gaze_x, gaze_y = _normalized_coordinates(merged)
    coordinate_valid = gaze_x is not None and gaze_y is not None
    combined_valid = _boolean_value(
        merged,
        ("valid", "gaze_valid"),
        default=coordinate_valid,
    )
    left_valid = _boolean_value(
        merged,
        ("left_eye_valid", "left_valid"),
        default=combined_valid,
    )
    right_valid = _boolean_value(
        merged,
        ("right_eye_valid", "right_valid"),
        default=combined_valid,
    )

    raw_sequence = merged.get(
        "sequence",
        fallback_sequence,
    )

    try:
        sequence = int(raw_sequence)
    except (TypeError, ValueError) as error:
        raise DeviceReadError("sequence must be an integer.") from error

    if sequence < 0:
        raise DeviceReadError("sequence cannot be negative.")

    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=(__import__("time").monotonic_ns()),
            utc_timestamp=_utc_timestamp(merged),
            source_timestamp_ns=(_source_timestamp_ns(merged)),
            source_clock_id=(
                str(
                    merged.get(
                        "source_clock_id",
                        "tobii-legacy-bridge",
                    )
                )
            ),
        ),
        gaze_x_normalized=(gaze_x if combined_valid else None),
        gaze_y_normalized=(gaze_y if combined_valid else None),
        left_eye_valid=left_valid,
        right_eye_valid=right_valid,
        left_pupil_diameter_mm=_first_float(
            merged,
            (
                "left_pupil_diameter_mm",
                "left_pupil_mm",
            ),
        ),
        right_pupil_diameter_mm=_first_float(
            merged,
            (
                "right_pupil_diameter_mm",
                "right_pupil_mm",
            ),
        ),
    )


def _looks_like_gaze(
    payload: dict[str, Any],
) -> bool:
    merged = _merged_gaze_payload(payload)

    return any(
        key in merged
        for key in (
            "gaze_x_normalized",
            "gaze_y_normalized",
            "gaze_x",
            "gaze_y",
            "x",
            "y",
            "x_px",
            "y_px",
            "gaze_x_px",
            "gaze_y_px",
        )
    )


class TobiiLegacyBridgeDevice:
    """Read newline-delimited gaze JSON from a local bridge."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 9999,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 0.25,
        maximum_message_bytes: int = 1_048_576,
    ) -> None:
        normalized_host = host.strip()

        if not normalized_host:
            raise ValueError("host cannot be empty.")

        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535.")

        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive.")

        if read_timeout_seconds <= 0:
            raise ValueError("read_timeout_seconds must be positive.")

        if maximum_message_bytes <= 0:
            raise ValueError("maximum_message_bytes must be positive.")

        self.host = normalized_host
        self.port = port
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.read_timeout_seconds = float(read_timeout_seconds)
        self.maximum_message_bytes = maximum_message_bytes

        self._state = DeviceState.DISCONNECTED
        self._socket: socket.socket | None = None
        self._buffer = bytearray()
        self._sequence = 0
        self._info = DeviceInfo(
            device_id=(f"tobii-legacy-bridge:{self.host}:{self.port}"),
            kind=DeviceKind.EYE_TRACKER,
            name="Tobii Legacy Bridge",
            manufacturer="Tobii",
            model="TCP NDJSON Bridge",
            serial_number=None,
            is_simulated=False,
            capabilities=(
                "normalized_gaze",
                "binocular_validity",
                "pupil_diameter",
                "tcp",
                "ndjson",
                TOBII_BRIDGE_PROTOCOL,
            ),
        )

    @property
    def info(self) -> DeviceInfo:
        return self._info

    @property
    def state(self) -> DeviceState:
        return self._state

    def connect(self) -> None:
        if self._state is not DeviceState.DISCONNECTED:
            raise InvalidDeviceStateError("Only a disconnected bridge can connect.")

        try:
            bridge_socket = socket.create_connection(
                (self.host, self.port),
                timeout=self.connect_timeout_seconds,
            )
            bridge_socket.settimeout(self.read_timeout_seconds)
        except OSError as error:
            raise DeviceConnectionError(
                f"Unable to connect to Tobii bridge at {self.host}:{self.port}: {error}"
            ) from error

        self._socket = bridge_socket
        self._buffer.clear()
        self._state = DeviceState.CONNECTED

    def disconnect(self) -> None:
        if self._state is DeviceState.DISCONNECTED:
            return

        if self._state is DeviceState.STREAMING:
            self._state = DeviceState.CONNECTED

        bridge_socket = self._socket
        self._socket = None
        self._buffer.clear()
        self._state = DeviceState.DISCONNECTED

        if bridge_socket is not None:
            try:
                bridge_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

            bridge_socket.close()

    def start_stream(self) -> None:
        if self._state is not DeviceState.CONNECTED:
            raise InvalidDeviceStateError("Connect the Tobii bridge before streaming.")

        self._sequence = 0
        self._buffer.clear()
        self._state = DeviceState.STREAMING

    def stop_stream(self) -> None:
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("The Tobii bridge is not streaming.")

        self._state = DeviceState.CONNECTED

    def interrupt(self) -> None:
        """Unblock a pending socket read during shutdown."""
        if self._socket is None:
            return

        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _require_socket(self) -> socket.socket:
        if self._state is not DeviceState.STREAMING or self._socket is None:
            raise InvalidDeviceStateError("The Tobii bridge is not streaming.")

        return self._socket

    def _read_line(self) -> str:
        bridge_socket = self._require_socket()

        while b"\n" not in self._buffer:
            try:
                chunk = bridge_socket.recv(4096)
            except TimeoutError as error:
                raise TimeoutError("No Tobii sample is currently available.") from error
            except OSError as error:
                raise DeviceReadError(f"Tobii bridge read failed: {error}") from error

            if not chunk:
                raise DeviceStreamEndedError("The Tobii bridge closed the stream.")

            self._buffer.extend(chunk)

            if len(self._buffer) > self.maximum_message_bytes:
                raise DeviceReadError("Tobii bridge message exceeded the configured size limit.")

        raw_line, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)

        try:
            return raw_line.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise DeviceReadError("Tobii bridge output is not UTF-8.") from error

    def read_sample(self) -> EyeTrackerSample:
        while True:
            line = self._read_line()

            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise DeviceReadError("Tobii bridge emitted invalid JSON.") from error

            if not isinstance(payload, dict):
                continue

            if not _looks_like_gaze(payload):
                continue

            sample = parse_tobii_bridge_payload(
                payload,
                fallback_sequence=self._sequence,
            )
            self._sequence = sample.timestamp.sequence + 1

            return sample
