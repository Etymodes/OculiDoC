from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

import oculidoc.tasks.gaze_stream as gaze_stream_module
from oculidoc.config import Settings
from oculidoc.devices.preflight import GazePreflightStore, run_gaze_preflight
from oculidoc.devices.simulated import SimulatedEyeTrackerDevice
from oculidoc.tasks.gaze_stream import GazeStreamWorker


class StepClock:
    def __init__(self, step: float = 0.1) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


def test_preflight_measures_rate_and_validity() -> None:
    device = SimulatedEyeTrackerDevice(realtime=False)
    device.connect()
    device.start_stream()

    result = run_gaze_preflight(
        device,
        source="mock",
        duration_seconds=0.5,
        minimum_valid_ratio=0.60,
        clock=StepClock(),
        sleeper=lambda _: None,
    )

    assert result.passed is True
    assert result.sample_count > 0
    assert result.valid_ratio == 1.0
    assert result.sample_rate_hz > 0


def test_preflight_rejects_low_validity() -> None:
    device = SimulatedEyeTrackerDevice(realtime=False, invalid_every_n=2)
    device.connect()
    device.start_stream()

    result = run_gaze_preflight(
        device,
        source="mock",
        duration_seconds=0.8,
        minimum_valid_ratio=0.75,
        clock=StepClock(),
        sleeper=lambda _: None,
    )

    assert result.passed is False
    assert result.valid_ratio < result.minimum_valid_ratio
    assert "重新校准" in str(result.error)


def test_preflight_store_round_trip(tmp_path: Path) -> None:
    device = SimulatedEyeTrackerDevice(realtime=False)
    device.connect()
    device.start_stream()
    result = run_gaze_preflight(
        device,
        source="mock",
        duration_seconds=0,
        minimum_valid_ratio=0.60,
        clock=StepClock(),
        sleeper=lambda _: None,
    )
    store = GazePreflightStore(tmp_path / "gaze_preflight.json")

    store.save(result)

    assert store.load() == result
    assert not list(tmp_path.glob(".gaze_preflight.json.*.tmp"))


def test_worker_runs_preflight_before_streaming(qtbot: QtBot, tmp_path: Path) -> None:
    store = GazePreflightStore(tmp_path / "gaze_preflight.json")
    samples = []
    worker = GazeStreamWorker(
        Settings(environment="test", data_dir=tmp_path, gaze_source="mock"),
        preflight_seconds=0,
        preflight_store=store,
    )
    worker.sample_received.connect(samples.append)

    with qtbot.waitSignal(worker.preflight_completed, timeout=2_000) as signal:
        worker.start()

    qtbot.wait(50)
    assert samples == []
    worker.enable_sample_delivery()
    qtbot.waitUntil(lambda: bool(samples), timeout=1_000)
    worker.stop()
    assert signal.args[0].passed is True
    assert store.load() is not None


def test_worker_reports_low_validity_without_mock_fallback(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        gaze_stream_module,
        "create_eye_tracker",
        lambda settings: SimulatedEyeTrackerDevice(
            realtime=False,
            invalid_every_n=1,
        ),
    )
    worker = GazeStreamWorker(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="tobii_stream_engine",
            gaze_minimum_valid_ratio=0.60,
        ),
        preflight_seconds=0,
        preflight_store=GazePreflightStore(tmp_path / "gaze_preflight.json"),
    )

    results = []
    errors: list[str] = []
    worker.preflight_completed.connect(results.append)
    worker.stream_error.connect(errors.append)

    with qtbot.waitSignal(worker.finished, timeout=2_000):
        worker.start()

    assert results[0].passed is False
    assert "重新校准" in errors[0]
