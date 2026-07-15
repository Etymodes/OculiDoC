"""Device lifecycle, streaming, and coordination errors."""

from collections.abc import Sequence


class DeviceError(RuntimeError):
    """Base class for device-related failures."""


class InvalidDeviceStateError(DeviceError):
    """Raised when an operation is invalid for the current state."""


class DeviceStreamEndedError(DeviceError):
    """Raised when a finite simulated stream has ended."""


class DeviceCoordinationError(DeviceError):
    """Raised when a coordinated multi-device operation fails."""

    def __init__(
        self,
        operation: str,
        primary_error: Exception,
        cleanup_errors: Sequence[Exception] = (),
    ) -> None:
        self.operation = operation
        self.primary_error = primary_error
        self.cleanup_errors = tuple(cleanup_errors)

        message = (
            f"Device operation '{operation}' failed: "
            f"{type(primary_error).__name__}: {primary_error}"
        )

        if self.cleanup_errors:
            message += f"; {len(self.cleanup_errors)} cleanup operation(s) also failed"

        super().__init__(message)
