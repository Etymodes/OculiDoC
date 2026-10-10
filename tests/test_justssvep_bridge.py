"""Contract tests for the evidenced JustSsvep CSV and local WebSocket bridge."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

import numpy as np
import pytest

from oculidoc.devices.eeg.adapters.mylian import (
    MylianBridgeStatus,
    MylianBridgeUnavailable,
    MylianCsvEEGSource,
    MylianWebSocketEEGSource,
    parse_justssvep_wire_frame,
)
from oculidoc.devices.eeg.adapters.mylian.justssvep import (
    COLLECTION_STOP,
    SSVEP_MARK_STOP,
)
from oculidoc.signals.sources import save_eeg_block_csv


def _wire_message(offset: int = 0) -> str:
    values = ",".join(str(offset + value) for value in range(1, 9))
    payload = f"24,8,{values},0,90"
    return f"1<split>2<split>{payload}<split>private-mac<split>4<split>0<split>0<split>x"


def test_observed_wire_frame_is_parsed_without_private_identifiers() -> None:
    frame = parse_justssvep_wire_frame(_wire_message())
    assert frame is not None
    assert frame.raw_counts == (4, 3, 2, 1, 8, 7, 6, 5)
    assert frame.quality_levels == (3,) * 8
    assert not hasattr(frame, "brain_mac")


class _FakeConnection:
    def __init__(self, messages: Iterator[str]) -> None:
        self.messages = messages
        self.sent: list[str] = []

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str:
        del timeout
        return next(self.messages)


def test_websocket_source_records_raw_counts_and_uses_oculidoc_algorithm_input(
    tmp_path: Path,
) -> None:
    connection = _FakeConnection(iter((_wire_message(), _wire_message(10))))
    raw_path = tmp_path / "raw.csv"
    source = MylianWebSocketEEGSource(
        "ws://127.0.0.1:12991",
        sample_rate_hz=2.0,
        channel_names=("O1", "Oz"),
        value_scale_uv_per_count=0.5,
        raw_capture_path=raw_path,
        mark_ssvep=True,
        connector=lambda _uri: connection,
    )
    block = source.acquire(1.0)
    assert block.device_id == "mylian-justssvep-local-websocket"
    assert np.array_equal(block.values_uv, np.array([[1.0, 6.0], [4.0, 9.0]]))
    assert block.quality == {"O1": 1.0, "Oz": 1.0}
    assert source.last_telemetry == {
        "battery_percent": 90,
        "bridge_version": 1,
        "machine_status": 2,
        "bci_port": 4,
        "received_sample_count": 2,
        "requested_sample_count": 2,
    }
    assert connection.sent == [
        "usbPortSwitch<jp>1<jp>",
        "ssvep_mark<jp>1<jp>0<jp>",
        "ssvep_mark<jp>1<jp>-1<jp>",
        "usbPortSwitch<jp>0<jp>",
    ]
    header = raw_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == (
        "received_timestamp_ns,FP1,FP2,O1,O2,Oz,PO3,PO4,POz,"
        "quality_FP1,quality_FP2,quality_O1,quality_O2,quality_Oz,quality_PO3,quality_PO4,"
        "quality_POz,tag,battery_percent,bridge_version,machine_status,bit_width,bci_port,"
        "score,speed"
    )
    assert "private-mac" not in raw_path.read_text(encoding="utf-8")


def test_websocket_connection_failure_is_explicit_and_never_simulated(tmp_path: Path) -> None:
    def unavailable(_uri: str) -> _FakeConnection:
        raise OSError("connection refused")

    source = MylianWebSocketEEGSource(
        "ws://127.0.0.1:12991",
        sample_rate_hz=250.0,
        channel_names=("O1", "Oz", "O2"),
        value_scale_uv_per_count=1.0,
        raw_capture_path=tmp_path / "raw.csv",
        mark_ssvep=True,
        connector=unavailable,
    )
    with pytest.raises(MylianBridgeUnavailable) as caught:
        source.acquire(1.0)
    assert caught.value.status is MylianBridgeStatus.CONNECTION_UNAVAILABLE
    assert not (tmp_path / "raw.csv").exists()


def test_partial_raw_counts_survive_bridge_disconnect(tmp_path: Path) -> None:
    class DisconnectingConnection(_FakeConnection):
        def recv(self, timeout: float | None = None) -> str:
            del timeout
            try:
                return next(self.messages)
            except StopIteration as error:
                raise OSError("bridge disconnected") from error

    raw_path = tmp_path / "partial.csv"
    connection = DisconnectingConnection(iter((_wire_message(),)))
    source = MylianWebSocketEEGSource(
        "ws://127.0.0.1:12991",
        sample_rate_hz=2.0,
        channel_names=("O1", "Oz"),
        value_scale_uv_per_count=1.0,
        raw_capture_path=raw_path,
        mark_ssvep=True,
        connector=lambda _uri: connection,
    )

    with pytest.raises(MylianBridgeUnavailable):
        source.acquire(1.0)

    assert len(raw_path.read_text(encoding="utf-8").splitlines()) == 2
    assert connection.sent[-2:] == [SSVEP_MARK_STOP, COLLECTION_STOP]


def test_websocket_source_rejects_nonlocal_hosts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        MylianWebSocketEEGSource(
            "ws://192.168.1.20:12991",
            sample_rate_hz=250.0,
            channel_names=("O1", "Oz", "O2"),
            value_scale_uv_per_count=1.0,
            raw_capture_path=tmp_path / "raw.csv",
            mark_ssvep=True,
        )


def test_historical_justssvep_csv_is_normalized_to_rectangular_oculidoc_csv(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "historical.csv"
    source_path.write_text(
        "elements,flag,timestamp\n"
        "FP1,FP2,O1,O2,Oz,PO3,PO4,POz,tag,2026-08-06T23:00:00Z\n"
        "1,2,3,4,5,6,7,8,1,2026-08-06T23:00:00.000Z\n"
        "2,3,4,5,6,7,8,9,0,2026-08-06T23:00:00.004Z\n",
        encoding="utf-8",
    )
    block = MylianCsvEEGSource(
        source_path,
        sample_rate_hz=250.0,
        channel_names=("O1", "Oz"),
        value_scale_uv_per_count=2.0,
    ).acquire(1.0)
    assert np.array_equal(block.values_uv, np.array([[6.0, 8.0], [10.0, 12.0]]))
    normalized = save_eeg_block_csv(tmp_path / "normalized.csv", block, tag="calibration")
    with normalized.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == ["O1", "Oz", "tag", "timestamp"]
    assert {len(row) for row in rows} == {4}
    assert rows[1][2] == "calibration"
