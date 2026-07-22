from __future__ import annotations

import pytest

from oculidoc.config import Settings
from oculidoc.devices.auto_detect import AutoDetectEyeTrackerDevice
from oculidoc.devices.contracts import DeviceInfo, DeviceKind, DeviceState
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.simulated import SimulatedEyeTrackerDevice
from oculidoc.devices.tobii_legacy_bridge import TobiiLegacyBridgeDevice
from oculidoc.tasks.gaze_stream import create_eye_tracker


class FakeHardwareEyeTracker(SimulatedEyeTrackerDevice):
    def __init__(self, *, max_samples: int) -> None:
        super().__init__(max_samples=max_samples, realtime=False)
        self._info = DeviceInfo(
            device_id="fake-hardware",
            kind=DeviceKind.EYE_TRACKER,
            name="Fake Hardware Eye Tracker",
            manufacturer="Test",
            model="Deterministic Adapter",
            is_simulated=False,
        )


def test_auto_detect_tries_candidates_until_one_emits_a_sample() -> None:
    calls: list[str] = []

    def unavailable_factory():
        calls.append("unavailable")
        raise DeviceConnectionError("not installed")

    def working_factory():
        calls.append("working")
        return FakeHardwareEyeTracker(max_samples=2)

    device = AutoDetectEyeTrackerDevice(
        (unavailable_factory, working_factory),
        probe_timeout_seconds=0.1,
    )

    device.connect()
    assert calls == ["unavailable", "working"]
    assert device.state is DeviceState.CONNECTED
    assert device.active_device is not None
    device.start_stream()
    assert device.read_sample().gaze_valid is True
    device.stop_stream()
    device.disconnect()
    assert device.state is DeviceState.DISCONNECTED


def test_auto_detect_never_falls_back_when_no_hardware_emits_data() -> None:
    device = AutoDetectEyeTrackerDevice(
        (lambda: FakeHardwareEyeTracker(max_samples=0),),
        probe_timeout_seconds=0.05,
    )

    with pytest.raises(DeviceConnectionError, match="自动检测未发现"):
        device.connect()

    assert device.active_device is None
    assert device.state is DeviceState.DISCONNECTED


def test_auto_detect_rejects_simulation_even_if_configured_as_a_candidate() -> None:
    device = AutoDetectEyeTrackerDevice(
        (lambda: SimulatedEyeTrackerDevice(max_samples=1),),
        probe_timeout_seconds=0.05,
    )

    with pytest.raises(DeviceConnectionError, match="禁止使用模拟眼动源"):
        device.connect()


def test_configured_auto_source_contains_only_hardware_candidates() -> None:
    device = create_eye_tracker(
        Settings(
            gaze_source="auto",
            tobii_bridge_host="127.0.0.1",
            tobii_bridge_port=4567,
        )
    )

    assert isinstance(device, AutoDetectEyeTrackerDevice)
    candidates = [factory() for factory in device.candidate_factories]
    assert candidates
    assert all(candidate.info.is_simulated is False for candidate in candidates)
    bridge = next(
        candidate for candidate in candidates if isinstance(candidate, TobiiLegacyBridgeDevice)
    )
    assert bridge.host == "127.0.0.1"
    assert bridge.port == 4567
