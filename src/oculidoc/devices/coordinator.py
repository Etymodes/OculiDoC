"""Coordinated lifecycle management for acquisition devices."""

from collections.abc import Iterable

from oculidoc.devices.contracts import (
    AcquisitionDevice,
    DeviceState,
)
from oculidoc.devices.errors import (
    DeviceCoordinationError,
    InvalidDeviceStateError,
)


class DeviceCoordinator:
    """Coordinate connection and streaming across multiple devices."""

    def __init__(
        self,
        devices: Iterable[AcquisitionDevice],
    ) -> None:
        self._devices = tuple(devices)

        if not self._devices:
            raise ValueError("At least one acquisition device is required.")

        device_ids = [device.info.device_id for device in self._devices]

        if len(device_ids) != len(set(device_ids)):
            raise ValueError("Device identifiers must be unique.")

    @property
    def devices(self) -> tuple[AcquisitionDevice, ...]:
        """Return devices in deterministic operation order."""
        return self._devices

    @property
    def all_connected(self) -> bool:
        """Return whether every device is connected but not streaming."""
        return all(device.state is DeviceState.CONNECTED for device in self._devices)

    @property
    def all_streaming(self) -> bool:
        """Return whether every device is streaming."""
        return all(device.state is DeviceState.STREAMING for device in self._devices)

    @property
    def all_disconnected(self) -> bool:
        """Return whether every device is disconnected."""
        return all(device.state is DeviceState.DISCONNECTED for device in self._devices)

    def _require_states(
        self,
        expected_state: DeviceState,
        *,
        operation: str,
    ) -> None:
        invalid_devices = [
            device.info.device_id for device in self._devices if device.state is not expected_state
        ]

        if invalid_devices:
            joined_ids = ", ".join(invalid_devices)

            raise InvalidDeviceStateError(
                f"Cannot {operation}; expected all devices "
                f"to be {expected_state.value}. Invalid: "
                f"{joined_ids}"
            )

    def connect_all(self) -> None:
        """Connect every device or roll back connected devices."""
        self._require_states(
            DeviceState.DISCONNECTED,
            operation="connect devices",
        )

        connected_devices: list[AcquisitionDevice] = []

        try:
            for device in self._devices:
                device.connect()
                connected_devices.append(device)
        except Exception as error:
            cleanup_errors: list[Exception] = []

            for device in reversed(connected_devices):
                try:
                    device.disconnect()
                except Exception as cleanup_error:
                    cleanup_errors.append(cleanup_error)

            raise DeviceCoordinationError(
                "connect",
                error,
                cleanup_errors,
            ) from error

    def start_all(self) -> None:
        """Start every stream or stop streams already started."""
        self._require_states(
            DeviceState.CONNECTED,
            operation="start streams",
        )

        started_devices: list[AcquisitionDevice] = []

        try:
            for device in self._devices:
                device.start_stream()
                started_devices.append(device)
        except Exception as error:
            cleanup_errors: list[Exception] = []

            for device in reversed(started_devices):
                try:
                    device.stop_stream()
                except Exception as cleanup_error:
                    cleanup_errors.append(cleanup_error)

            raise DeviceCoordinationError(
                "start",
                error,
                cleanup_errors,
            ) from error

    def stop_all(self) -> None:
        """Stop every stream in reverse device order."""
        self._require_states(
            DeviceState.STREAMING,
            operation="stop streams",
        )

        errors: list[Exception] = []

        for device in reversed(self._devices):
            try:
                device.stop_stream()
            except Exception as error:
                errors.append(error)

        if errors:
            raise DeviceCoordinationError(
                "stop",
                errors[0],
                errors[1:],
            )

    def disconnect_all(self) -> None:
        """Disconnect every device in reverse order."""
        self._require_states(
            DeviceState.CONNECTED,
            operation="disconnect devices",
        )

        errors: list[Exception] = []

        for device in reversed(self._devices):
            try:
                device.disconnect()
            except Exception as error:
                errors.append(error)

        if errors:
            raise DeviceCoordinationError(
                "disconnect",
                errors[0],
                errors[1:],
            )

    def _cleanup_to_disconnected(
        self,
    ) -> list[Exception]:
        """Best-effort cleanup to a disconnected state."""
        errors: list[Exception] = []

        for device in reversed(self._devices):
            if device.state is DeviceState.STREAMING:
                try:
                    device.stop_stream()
                except Exception as error:
                    errors.append(error)

            if device.state is DeviceState.CONNECTED:
                try:
                    device.disconnect()
                except Exception as error:
                    errors.append(error)

        return errors

    def connect_and_start(self) -> None:
        """Connect and start all devices as one logical operation."""
        try:
            self.connect_all()
            self.start_all()
        except Exception as error:
            cleanup_errors = self._cleanup_to_disconnected()

            if isinstance(
                error,
                DeviceCoordinationError,
            ):
                cleanup_errors = [
                    *error.cleanup_errors,
                    *cleanup_errors,
                ]

            raise DeviceCoordinationError(
                "connect_and_start",
                error,
                cleanup_errors,
            ) from error

    def stop_and_disconnect(self) -> None:
        """Best-effort shutdown of all active devices."""
        errors = self._cleanup_to_disconnected()

        if errors:
            raise DeviceCoordinationError(
                "stop_and_disconnect",
                errors[0],
                errors[1:],
            )
