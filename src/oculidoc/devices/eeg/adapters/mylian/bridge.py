"""Translate local Mylian bridge JSON into the standard EEG contract.

The compatibility-only keys ``brainPayload``, ``targetFre_est`` and
``frequencyFeaturesStr`` never leave this module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from time import monotonic_ns

import numpy as np

from oculidoc.signals.models import EEGSampleBlock, SignalMarker


class MylianBridgeStatus(StrEnum):
    """OculiDoC-owned capability states reported by the external bridge."""

    AVAILABLE = "available"
    MISSING_RUNTIME = "missing_runtime"
    UNSUPPORTED_DEVICE = "unsupported_device"
    LICENCE_REQUIRED = "licence_required"
    CONNECTION_UNAVAILABLE = "connection_unavailable"


class MylianBridgeUnavailable(RuntimeError):
    """The optional external bridge cannot provide EEG blocks."""

    def __init__(self, status: MylianBridgeStatus, detail: str = "") -> None:
        message = {
            MylianBridgeStatus.MISSING_RUNTIME: (
                "Mylian bridge runtime or required symbols are missing."
            ),
            MylianBridgeStatus.UNSUPPORTED_DEVICE: (
                "The connected device is unsupported by the Mylian bridge."
            ),
            MylianBridgeStatus.LICENCE_REQUIRED: (
                "The Mylian bridge reports insufficient licence permission."
            ),
            MylianBridgeStatus.CONNECTION_UNAVAILABLE: (
                "The local Mylian/JustSsvep bridge is not reachable or returned no data."
            ),
            MylianBridgeStatus.AVAILABLE: "Mylian bridge did not provide an EEG block.",
        }[status]
        if detail.strip():
            message += f" {detail.strip()}"
        super().__init__(message)
        self.status = status


class MylianPayloadAdapter:
    """Strict boundary for one decoded newline-delimited JSON payload."""

    def decode(
        self,
        payload: Mapping[str, object],
        *,
        received_timestamp_ns: int | None = None,
    ) -> EEGSampleBlock:
        raw_status = payload.get("oculidocBridgeStatus", MylianBridgeStatus.AVAILABLE.value)
        try:
            status = MylianBridgeStatus(str(raw_status))
        except ValueError as error:
            raise ValueError(f"Unsupported Mylian bridge status: {raw_status}") from error
        if status is not MylianBridgeStatus.AVAILABLE:
            raise MylianBridgeUnavailable(status, str(payload.get("detail") or ""))
        try:
            device_id = str(payload.get("deviceId") or payload.get("device_id") or "mylian")
            sample_rate_value = payload.get("sampleRate") or payload["sample_rate_hz"]
            if not isinstance(sample_rate_value, (str, int, float)):
                raise TypeError("sample rate must be numeric")
            sample_rate_hz = float(sample_rate_value)
            channel_names_value = payload.get("channelNames") or payload["channel_names"]
            if not isinstance(channel_names_value, (list, tuple)):
                raise TypeError("channel names must be an array")
            channel_names = tuple(str(item) for item in channel_names_value)
            values = np.asarray(payload["brainPayload"], dtype=np.float64)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Mylian bridge payload is missing required EEG fields.") from error

        if values.ndim == 1:
            if values.size % len(channel_names) != 0:
                raise ValueError("Mylian brainPayload length does not match channel count.")
            values = values.reshape(len(channel_names), -1)
        elif values.ndim == 2 and values.shape[0] != len(channel_names):
            if values.shape[1] == len(channel_names):
                values = values.T
            else:
                raise ValueError("Mylian brainPayload shape does not match channel count.")

        raw_timestamp = payload.get("timestampNs") or payload.get("timestamp_ns")
        if raw_timestamp is not None and not isinstance(raw_timestamp, (str, int, float)):
            raise ValueError("Mylian bridge timestamp must be numeric.")
        timestamp_ns = (
            int(raw_timestamp)  # type: ignore[arg-type]
            if raw_timestamp is not None
            else int(received_timestamp_ns if received_timestamp_ns is not None else monotonic_ns())
        )
        quality_value = payload.get("quality")
        quality = (
            {str(name): float(value) for name, value in quality_value.items()}
            if isinstance(quality_value, dict)
            else {}
        )
        marker_value = payload.get("marker")
        markers = (
            (SignalMarker("mylian_marker", timestamp_ns, str(marker_value)),)
            if marker_value is not None
            else ()
        )

        return EEGSampleBlock(
            device_id=device_id,
            start_timestamp_ns=timestamp_ns,
            sample_rate_hz=sample_rate_hz,
            channel_names=channel_names,
            values_uv=values,
            quality=quality,
            markers=markers,
            simulated=False,
        )

    def decode_line(
        self,
        line: str | bytes,
        *,
        received_timestamp_ns: int | None = None,
    ) -> EEGSampleBlock:
        try:
            payload = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Mylian bridge line is not valid UTF-8 JSON.") from error
        if not isinstance(payload, dict):
            raise ValueError("Mylian bridge line must contain one JSON object.")
        return self.decode(payload, received_timestamp_ns=received_timestamp_ns)


class MylianJsonLineSource:
    """Read the most recent complete block exported by a local bridge."""

    def __init__(self, path: str | Path, *, adapter: MylianPayloadAdapter | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self.adapter = adapter or MylianPayloadAdapter()

    def acquire(self, duration_seconds: float) -> EEGSampleBlock:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive.")
        try:
            lines = [line for line in self.path.read_bytes().splitlines() if line.strip()]
        except OSError as error:
            raise RuntimeError(f"Cannot read Mylian local bridge output: {self.path}") from error
        if not lines:
            raise RuntimeError("Mylian local bridge output contains no complete JSON block.")
        block = self.adapter.decode_line(lines[-1], received_timestamp_ns=monotonic_ns())
        if block.duration_seconds <= duration_seconds:
            return block
        sample_count = max(1, round(duration_seconds * block.sample_rate_hz))
        return EEGSampleBlock(
            device_id=block.device_id,
            start_timestamp_ns=block.start_timestamp_ns,
            sample_rate_hz=block.sample_rate_hz,
            channel_names=block.channel_names,
            values_uv=block.values_uv[:, :sample_count],
            quality=block.quality,
            markers=block.markers,
            simulated=False,
        )
