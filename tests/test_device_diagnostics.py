"""Device diagnostic service tests."""

import json
from datetime import UTC, datetime
from pathlib import Path

import cv2

from oculidoc.devices.diagnostics import (
    collect_system_snapshot,
    probe_camera,
    probe_cameras,
    write_diagnostic_report,
)
from oculidoc.devices.models import (
    CameraProbeResult,
    DeviceDiagnosticReport,
    ProbeStatus,
    SystemSnapshot,
)


class FakeFrame:
    """Minimal frame object used by the fake camera."""

    size = 100


class FakeCapture:
    """Controllable OpenCV capture replacement."""

    def __init__(
        self,
        *,
        opened: bool,
        frame_received: bool,
    ) -> None:
        self.opened = opened
        self.frame_received = frame_received
        self.released = False

    def isOpened(self) -> bool:
        return self.opened

    def read(self):
        if self.frame_received:
            return True, FakeFrame()

        return False, None

    def get(self, property_id: int) -> float:
        values = {
            cv2.CAP_PROP_FRAME_WIDTH: 1920.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 1080.0,
            cv2.CAP_PROP_FPS: 30.0,
        }

        return values.get(property_id, 0.0)

    def getBackendName(self) -> str:
        return "FAKE"

    def release(self) -> None:
        self.released = True


def test_probe_camera_reports_available_camera() -> None:
    capture = FakeCapture(
        opened=True,
        frame_received=True,
    )

    result = probe_camera(
        0,
        backend=123,
        capture_factory=lambda index, backend: capture,
    )

    assert result.status is ProbeStatus.AVAILABLE
    assert result.available is True
    assert result.backend == "FAKE"
    assert result.width_px == 1920
    assert result.height_px == 1080
    assert result.fps == 30.0
    assert capture.released is True


def test_probe_camera_reports_unavailable_camera() -> None:
    capture = FakeCapture(
        opened=False,
        frame_received=False,
    )

    result = probe_camera(
        1,
        capture_factory=lambda index, backend: capture,
    )

    assert result.status is ProbeStatus.UNAVAILABLE
    assert result.available is False
    assert capture.released is True


def test_probe_camera_reports_missing_frame() -> None:
    capture = FakeCapture(
        opened=True,
        frame_received=False,
    )

    result = probe_camera(
        2,
        capture_factory=lambda index, backend: capture,
    )

    assert result.status is ProbeStatus.ERROR
    assert result.frame_received is False
    assert result.backend == "FAKE"


def test_probe_cameras_checks_requested_range() -> None:
    requested_indices: list[int] = []

    def factory(index: int, backend: int):
        del backend
        requested_indices.append(index)

        return FakeCapture(
            opened=index == 0,
            frame_received=index == 0,
        )

    results = probe_cameras(
        3,
        capture_factory=factory,
    )

    assert requested_indices == [0, 1, 2, 3]
    assert len(results) == 4
    assert results[0].available is True
    assert all(not result.available for result in results[1:])


def test_collect_system_snapshot_has_valid_values(
    tmp_path: Path,
) -> None:
    snapshot = collect_system_snapshot(tmp_path)

    assert snapshot.hostname
    assert snapshot.operating_system
    assert snapshot.machine
    assert snapshot.python_version.startswith("3.11")
    assert snapshot.logical_cpu_count > 0
    assert snapshot.memory_total_bytes > 0
    assert snapshot.memory_available_bytes > 0
    assert snapshot.disk_free_bytes >= 0


def test_write_diagnostic_report_round_trip(
    tmp_path: Path,
) -> None:
    captured_at = datetime(
        2026,
        7,
        14,
        12,
        0,
        tzinfo=UTC,
    )
    system = SystemSnapshot(
        hostname="Augusta",
        operating_system="Windows",
        operating_system_version="test",
        machine="AMD64",
        processor="Test CPU",
        python_version="3.11.9",
        logical_cpu_count=32,
        physical_cpu_count=16,
        memory_total_bytes=32 * 1024**3,
        memory_available_bytes=24 * 1024**3,
        disk_free_bytes=500 * 1024**3,
        nvidia_gpu_names=("Test GPU",),
        captured_at=captured_at,
    )
    report = DeviceDiagnosticReport(
        system=system,
        cameras=(
            CameraProbeResult(
                index=0,
                status=ProbeStatus.AVAILABLE,
                backend="DSHOW",
                width_px=1920,
                height_px=1080,
                fps=30.0,
                frame_received=True,
            ),
        ),
        generated_at=captured_at,
    )
    output_path = tmp_path / "device-report.json"

    written_path = write_diagnostic_report(
        report,
        output_path,
    )
    payload = json.loads(written_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.0"
    assert payload["system"]["hostname"] == "Augusta"
    assert payload["system"]["nvidia_gpu_names"] == ["Test GPU"]
    assert payload["cameras"][0]["status"] == "available"
    assert not list(tmp_path.glob(".device-report.json.*.tmp"))


def test_probe_camera_restores_opencv_log_level(
    monkeypatch,
) -> None:
    """Support OpenCV builds without getLogLevel."""
    level_changes: list[int] = []

    monkeypatch.delattr(
        cv2,
        "getLogLevel",
        raising=False,
    )
    monkeypatch.setattr(
        cv2,
        "setLogLevel",
        level_changes.append,
        raising=False,
    )
    monkeypatch.setattr(
        cv2,
        "LOG_LEVEL_ERROR",
        2,
        raising=False,
    )
    monkeypatch.setattr(
        cv2,
        "LOG_LEVEL_WARNING",
        3,
        raising=False,
    )

    capture = FakeCapture(
        opened=False,
        frame_received=False,
    )

    result = probe_camera(
        7,
        capture_factory=lambda index, backend: capture,
    )

    assert result.status is ProbeStatus.UNAVAILABLE
    assert level_changes == [2, 3]
