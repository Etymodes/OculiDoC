"""Multi-device lifecycle coordination tests."""

import pytest

from oculidoc.devices import (
    DeviceCoordinationError,
    DeviceCoordinator,
    DeviceInfo,
    DeviceKind,
    DeviceState,
    InvalidDeviceStateError,
    SimulatedCameraDevice,
    SimulatedEyeTrackerDevice,
)


class ControllableDevice:
    """Device fake with operation logging and injected failures."""

    def __init__(
        self,
        device_id: str,
        events: list[str],
        *,
        fail_operation: str | None = None,
    ) -> None:
        self._info = DeviceInfo(
            device_id=device_id,
            kind=DeviceKind.CAMERA,
            name=device_id,
            manufacturer="Test",
            model="Controllable",
            is_simulated=True,
        )
        self._state = DeviceState.DISCONNECTED
        self.events = events
        self.fail_operation = fail_operation

    @property
    def info(self) -> DeviceInfo:
        return self._info

    @property
    def state(self) -> DeviceState:
        return self._state

    def _record_and_maybe_fail(
        self,
        operation: str,
    ) -> None:
        self.events.append(f"{self.info.device_id}:{operation}")

        if self.fail_operation == operation:
            raise RuntimeError(f"{self.info.device_id} failed {operation}")

    def connect(self) -> None:
        self._record_and_maybe_fail("connect")
        self._state = DeviceState.CONNECTED

    def disconnect(self) -> None:
        self._record_and_maybe_fail("disconnect")
        self._state = DeviceState.DISCONNECTED

    def start_stream(self) -> None:
        self._record_and_maybe_fail("start")
        self._state = DeviceState.STREAMING

    def stop_stream(self) -> None:
        self._record_and_maybe_fail("stop")
        self._state = DeviceState.CONNECTED


def test_coordinator_runs_simulated_devices_together() -> None:
    camera = SimulatedCameraDevice(
        width_px=64,
        height_px=48,
    )
    tracker = SimulatedEyeTrackerDevice()

    coordinator = DeviceCoordinator([camera, tracker])

    assert coordinator.all_disconnected is True

    coordinator.connect_and_start()

    assert coordinator.all_streaming is True
    assert camera.read_frame().frame_index == 0
    assert tracker.read_sample().gaze_valid is True

    coordinator.stop_and_disconnect()

    assert coordinator.all_disconnected is True


def test_coordinator_requires_unique_device_ids() -> None:
    with pytest.raises(
        ValueError,
        match="unique",
    ):
        DeviceCoordinator(
            [
                SimulatedCameraDevice(device_id="duplicate"),
                SimulatedEyeTrackerDevice(device_id="duplicate"),
            ]
        )


def test_connect_failure_rolls_back_prior_devices() -> None:
    events: list[str] = []
    first = ControllableDevice(
        "first",
        events,
    )
    second = ControllableDevice(
        "second",
        events,
        fail_operation="connect",
    )
    coordinator = DeviceCoordinator([first, second])

    with pytest.raises(
        DeviceCoordinationError,
        match="'connect'",
    ):
        coordinator.connect_all()

    assert first.state is DeviceState.DISCONNECTED
    assert second.state is DeviceState.DISCONNECTED
    assert events == [
        "first:connect",
        "second:connect",
        "first:disconnect",
    ]


def test_start_failure_stops_prior_streams() -> None:
    events: list[str] = []
    first = ControllableDevice(
        "first",
        events,
    )
    second = ControllableDevice(
        "second",
        events,
        fail_operation="start",
    )
    coordinator = DeviceCoordinator([first, second])

    coordinator.connect_all()

    with pytest.raises(
        DeviceCoordinationError,
        match="'start'",
    ):
        coordinator.start_all()

    assert first.state is DeviceState.CONNECTED
    assert second.state is DeviceState.CONNECTED
    assert events[-3:] == [
        "first:start",
        "second:start",
        "first:stop",
    ]


def test_connect_and_start_failure_returns_to_disconnected() -> None:
    events: list[str] = []
    first = ControllableDevice(
        "first",
        events,
    )
    second = ControllableDevice(
        "second",
        events,
        fail_operation="start",
    )
    coordinator = DeviceCoordinator([first, second])

    with pytest.raises(
        DeviceCoordinationError,
        match="connect_and_start",
    ):
        coordinator.connect_and_start()

    assert coordinator.all_disconnected is True
    assert events[-3:] == [
        "first:stop",
        "second:disconnect",
        "first:disconnect",
    ]


def test_shutdown_occurs_in_reverse_order() -> None:
    events: list[str] = []
    first = ControllableDevice("first", events)
    second = ControllableDevice("second", events)
    coordinator = DeviceCoordinator([first, second])

    coordinator.connect_and_start()
    events.clear()

    coordinator.stop_and_disconnect()

    assert events == [
        "second:stop",
        "second:disconnect",
        "first:stop",
        "first:disconnect",
    ]
    assert coordinator.all_disconnected is True


def test_coordinator_rejects_invalid_initial_state() -> None:
    camera = SimulatedCameraDevice()
    tracker = SimulatedEyeTrackerDevice()
    coordinator = DeviceCoordinator([camera, tracker])

    camera.connect()

    with pytest.raises(
        InvalidDeviceStateError,
        match="expected all devices",
    ):
        coordinator.connect_all()
