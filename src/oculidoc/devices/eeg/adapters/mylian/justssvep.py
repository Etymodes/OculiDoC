"""Read the evidenced JustSsvep CSV and local WebSocket data contracts.

This module intentionally stops at the local JustSsvep bridge. The attached
site audit did not establish a serial baud rate, binary frame layout, or a
vendor-confirmed microvolt conversion, so OculiDoC must not open COM4 or guess
those values. Private bridge fields such as the brain MAC never leave this
adapter.
"""

from __future__ import annotations

import csv
from collections import deque
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from threading import Event
from time import monotonic, time_ns
from typing import Protocol
from urllib.parse import urlsplit

import numpy as np

from oculidoc.devices.eeg.adapters.mylian.bridge import (
    MylianBridgeStatus,
    MylianBridgeUnavailable,
)
from oculidoc.signals.models import EEGSampleBlock, SignalMarker

JUSTSSVEP_CHANNEL_NAMES = ("FP1", "FP2", "O1", "O2", "Oz", "PO3", "PO4", "POz")
COLLECTION_START = "usbPortSwitch<jp>1<jp>"
COLLECTION_STOP = "usbPortSwitch<jp>0<jp>"
SSVEP_MARK_START = "ssvep_mark<jp>1<jp>0<jp>"
SSVEP_MARK_STOP = "ssvep_mark<jp>1<jp>-1<jp>"


def _local_websocket_uri(uri: str) -> bool:
    host = urlsplit(uri).hostname
    if host is None:
        return False
    if host.casefold() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _reverse_groups_of_four(values: tuple[int, ...]) -> tuple[int, ...]:
    result: list[int] = []
    for index in range(0, len(values), 4):
        result.extend(reversed(values[index : index + 4]))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class JustSsvepWireFrame:
    """The non-identifying subset of one evidenced ``<split>`` frame."""

    version: int
    machine_status: int
    bit_width: int
    raw_counts: tuple[int, ...]
    quality_levels: tuple[int, ...]
    battery_percent: int
    bci_port: int
    score: int
    speed: int


def parse_justssvep_wire_frame(message: str | bytes) -> JustSsvepWireFrame | None:
    """Parse one data frame, ignoring result/heartbeat messages.

    The layout mirrors the observed JustSsvep 1.0.1 Electron client. Returned
    quality levels use its 0 (disconnected) through 3 (good) scale.
    """

    text = message.decode("utf-8") if isinstance(message, bytes) else message
    if "<split>" not in text:
        return None
    parts = text.split("<split>")
    if len(parts) < 8 or not parts[2].strip():
        raise ValueError("JustSsvep WebSocket frame is incomplete.")
    try:
        version = int(parts[0])
        machine_status = int(parts[1])
        payload = tuple(int(value) if value.strip() else 0 for value in parts[2].split(","))
        bci_port = int(parts[4])
        score = int(parts[5])
        speed = int(parts[6])
    except ValueError as error:
        raise ValueError("JustSsvep WebSocket frame contains non-numeric fields.") from error
    if len(payload) < 3:
        raise ValueError("JustSsvep WebSocket payload is too short.")
    bit_width, channel_count = payload[:2]
    if channel_count <= 0 or len(payload) < channel_count + 3:
        raise ValueError("JustSsvep WebSocket channel count is invalid.")
    raw_counts = _reverse_groups_of_four(payload[2 : 2 + channel_count])
    status_words = payload[2 + channel_count : -1]
    status_bits = "".join(format(max(0, value), "b") for value in status_words)
    status_bits = status_bits.zfill(channel_count * 2)[-channel_count * 2 :]
    quality_levels = tuple(
        3 - int(status_bits[index : index + 2], 2) for index in range(0, len(status_bits), 2)
    )
    quality_levels = _reverse_groups_of_four(quality_levels)
    return JustSsvepWireFrame(
        version=version,
        machine_status=machine_status,
        bit_width=bit_width,
        raw_counts=raw_counts,
        quality_levels=quality_levels,
        battery_percent=payload[-1],
        bci_port=bci_port,
        score=score,
        speed=speed,
    )


class _WireConnection(Protocol):
    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...


_Connector = Callable[[str], AbstractContextManager[_WireConnection]]


def _websocket_connector(uri: str) -> AbstractContextManager[_WireConnection]:
    try:
        from websockets.sync.client import connect
    except ImportError as error:  # pragma: no cover - installation gate exercises this.
        raise MylianBridgeUnavailable(
            MylianBridgeStatus.MISSING_RUNTIME,
            "Python package 'websockets' is unavailable.",
        ) from error
    return connect(uri, open_timeout=3.0, close_timeout=1.0)  # type: ignore[return-value]


class MylianWebSocketEEGSource:
    """Acquire real samples from the local JustSsvep bridge, never COM4 directly."""

    def __init__(
        self,
        uri: str,
        *,
        sample_rate_hz: float,
        channel_names: tuple[str, ...],
        value_scale_uv_per_count: float,
        raw_capture_path: str | Path,
        mark_ssvep: bool,
        cancel_event: Event | None = None,
        connector: _Connector | None = None,
    ) -> None:
        normalized_uri = uri.strip()
        if not normalized_uri.startswith(("ws://", "wss://")):
            raise ValueError("Mylian WebSocket address must start with ws:// or wss://.")
        if not _local_websocket_uri(normalized_uri):
            raise ValueError("The evidenced JustSsvep WebSocket bridge must use a loopback host.")
        unknown = tuple(name for name in channel_names if name not in JUSTSSVEP_CHANNEL_NAMES)
        if unknown:
            raise MylianBridgeUnavailable(
                MylianBridgeStatus.UNSUPPORTED_DEVICE,
                f"Observed 8-channel bridge does not provide: {', '.join(unknown)}.",
            )
        if sample_rate_hz <= 0 or value_scale_uv_per_count <= 0:
            raise ValueError("Mylian sample rate and microvolt scale must be positive.")
        self.uri = normalized_uri
        self.sample_rate_hz = float(sample_rate_hz)
        self.channel_names = tuple(channel_names)
        self.value_scale_uv_per_count = float(value_scale_uv_per_count)
        self.raw_capture_path = Path(raw_capture_path).expanduser().resolve()
        self.mark_ssvep = bool(mark_ssvep)
        self.cancel_event = cancel_event
        self.connector = connector or _websocket_connector
        self.last_telemetry: dict[str, object] = {}

    def acquire(self, duration_seconds: float) -> EEGSampleBlock:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive.")
        requested_samples = max(2, round(duration_seconds * self.sample_rate_hz))
        rows: list[tuple[int, JustSsvepWireFrame]] = []
        deadline = monotonic() + duration_seconds + 1.0
        try:
            with self.connector(self.uri) as connection:
                connection.send(COLLECTION_START)
                if self.mark_ssvep:
                    connection.send(SSVEP_MARK_START)
                try:
                    while len(rows) < requested_samples and monotonic() < deadline:
                        if self.cancel_event is not None and self.cancel_event.is_set():
                            raise InterruptedError(
                                "Signal task cancelled while reading the Mylian bridge."
                            )
                        try:
                            timeout = min(0.25, max(0.01, deadline - monotonic()))
                            message = connection.recv(timeout=timeout)
                        except TimeoutError:
                            continue
                        frame = parse_justssvep_wire_frame(message)
                        if frame is None:
                            continue
                        if len(frame.raw_counts) != len(JUSTSSVEP_CHANNEL_NAMES):
                            raise MylianBridgeUnavailable(
                                MylianBridgeStatus.UNSUPPORTED_DEVICE,
                                "Only the evidenced 8-channel JustSsvep frame is supported.",
                            )
                        rows.append((time_ns(), frame))
                finally:
                    if self.mark_ssvep:
                        try:
                            connection.send(SSVEP_MARK_STOP)
                        except Exception:  # noqa: BLE001 - best-effort external cleanup.
                            pass
                    try:
                        connection.send(COLLECTION_STOP)
                    except Exception:  # noqa: BLE001 - best-effort external cleanup.
                        pass
        except MylianBridgeUnavailable:
            raise
        except InterruptedError:
            raise
        except (OSError, RuntimeError) as error:
            raise MylianBridgeUnavailable(
                MylianBridgeStatus.CONNECTION_UNAVAILABLE,
                f"Cannot acquire from {self.uri}: {error}",
            ) from error
        finally:
            if rows:
                self._write_raw_capture(rows)
        if len(rows) < 2:
            raise MylianBridgeUnavailable(
                MylianBridgeStatus.CONNECTION_UNAVAILABLE,
                "The local bridge returned fewer than two complete samples.",
            )
        last_frame = rows[-1][1]
        self.last_telemetry = {
            "battery_percent": last_frame.battery_percent,
            "bridge_version": last_frame.version,
            "machine_status": last_frame.machine_status,
            "bci_port": last_frame.bci_port,
            "received_sample_count": len(rows),
            "requested_sample_count": requested_samples,
        }
        incoming_indices = tuple(JUSTSSVEP_CHANNEL_NAMES.index(name) for name in self.channel_names)
        raw_values = np.asarray(
            [[frame.raw_counts[index] for _timestamp, frame in rows] for index in incoming_indices],
            dtype=np.float64,
        )
        quality = {
            name: float(np.mean([frame.quality_levels[index] / 3.0 for _timestamp, frame in rows]))
            for name, index in zip(self.channel_names, incoming_indices, strict=True)
        }
        first_timestamp = rows[0][0]
        return EEGSampleBlock(
            device_id="mylian-justssvep-local-websocket",
            start_timestamp_ns=first_timestamp,
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self.channel_names,
            values_uv=raw_values * self.value_scale_uv_per_count,
            quality=quality,
            markers=(
                SignalMarker("mylian_collection_start", first_timestamp, 1),
                SignalMarker("mylian_collection_end", rows[-1][0], 0),
            ),
            simulated=False,
        )

    def _write_raw_capture(self, rows: list[tuple[int, JustSsvepWireFrame]]) -> None:
        self.raw_capture_path.parent.mkdir(parents=True, exist_ok=True)
        with self.raw_capture_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(
                (
                    "received_timestamp_ns",
                    *JUSTSSVEP_CHANNEL_NAMES,
                    *(f"quality_{name}" for name in JUSTSSVEP_CHANNEL_NAMES),
                    "tag",
                    "battery_percent",
                    "bridge_version",
                    "machine_status",
                    "bit_width",
                    "bci_port",
                    "score",
                    "speed",
                )
            )
            for timestamp, frame in rows:
                writer.writerow(
                    (
                        timestamp,
                        *frame.raw_counts,
                        *frame.quality_levels,
                        int(self.mark_ssvep),
                        frame.battery_percent,
                        frame.version,
                        frame.machine_status,
                        frame.bit_width,
                        frame.bci_port,
                        frame.score,
                        frame.speed,
                    )
                )


def _timestamp_ns(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1_000_000_000)
    except ValueError:
        return None


class MylianCsvEEGSource:
    """Replay the irregular ``elements,flag,timestamp`` files from JustSsvep."""

    def __init__(
        self,
        path: str | Path,
        *,
        sample_rate_hz: float,
        channel_names: tuple[str, ...],
        value_scale_uv_per_count: float,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.sample_rate_hz = float(sample_rate_hz)
        self.channel_names = tuple(channel_names)
        self.value_scale_uv_per_count = float(value_scale_uv_per_count)
        unknown = tuple(name for name in channel_names if name not in JUSTSSVEP_CHANNEL_NAMES)
        if unknown:
            raise ValueError(f"JustSsvep CSV does not provide: {', '.join(unknown)}.")
        if self.sample_rate_hz <= 0 or self.value_scale_uv_per_count <= 0:
            raise ValueError("CSV sample rate and microvolt scale must be positive.")

    def acquire(self, duration_seconds: float) -> EEGSampleBlock:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive.")
        sample_limit = max(2, round(duration_seconds * self.sample_rate_hz))
        rows: deque[tuple[tuple[float, ...], str, int | None]] = deque(maxlen=sample_limit)
        channel_names: tuple[str, ...] = JUSTSSVEP_CHANNEL_NAMES
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as stream:
                for row_number, row in enumerate(csv.reader(stream), start=1):
                    if not row:
                        continue
                    normalized = tuple(value.strip() for value in row)
                    if normalized[:3] == ("elements", "flag", "timestamp"):
                        continue
                    possible_names = normalized[:-2]
                    if possible_names and all(
                        name in JUSTSSVEP_CHANNEL_NAMES for name in possible_names
                    ):
                        channel_names = tuple(possible_names)
                        continue
                    if len(normalized) != len(channel_names) + 2:
                        raise ValueError(f"JustSsvep CSV row {row_number} is not rectangular.")
                    try:
                        values = tuple(float(value) for value in normalized[: len(channel_names)])
                    except ValueError as error:
                        raise ValueError(
                            f"JustSsvep CSV row {row_number} is not numeric."
                        ) from error
                    rows.append((values, normalized[-2], _timestamp_ns(normalized[-1])))
        except OSError as error:
            raise RuntimeError(f"Cannot read JustSsvep CSV: {self.path}") from error
        if len(rows) < 2:
            raise ValueError("JustSsvep CSV contains fewer than two complete EEG rows.")
        indices = tuple(channel_names.index(name) for name in self.channel_names)
        values_uv = (
            np.asarray(
                [[values[index] for values, _tag, _timestamp in rows] for index in indices],
                dtype=np.float64,
            )
            * self.value_scale_uv_per_count
        )
        timestamps = tuple(timestamp for _values, _tag, timestamp in rows if timestamp is not None)
        start_timestamp = timestamps[0] if timestamps else time_ns()
        markers: list[SignalMarker] = []
        previous_tag: str | None = None
        for sample_index, (_values, tag, timestamp) in enumerate(rows):
            if tag != previous_tag:
                markers.append(
                    SignalMarker(
                        "mylian_tag",
                        timestamp
                        if timestamp is not None
                        else start_timestamp
                        + round(sample_index / self.sample_rate_hz * 1_000_000_000),
                        tag,
                    )
                )
                previous_tag = tag
        return EEGSampleBlock(
            device_id="mylian-justssvep-csv",
            start_timestamp_ns=start_timestamp,
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self.channel_names,
            values_uv=values_uv,
            quality={},
            markers=tuple(markers),
            simulated=False,
        )
