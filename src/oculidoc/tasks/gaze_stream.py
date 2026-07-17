"""Qt worker for simulated or bridged gaze input."""

from contextlib import suppress

from PySide6.QtCore import QObject, QThread, Signal

from oculidoc.config import Settings
from oculidoc.devices.contracts import (
    DeviceState,
    EyeTrackerDevice,
)
from oculidoc.devices.errors import (
    DeviceStreamEndedError,
)
from oculidoc.devices.simulated import (
    SimulatedEyeTrackerDevice,
)
from oculidoc.devices.tobii_legacy_bridge import (
    TobiiLegacyBridgeDevice,
)


def create_eye_tracker(
    settings: Settings,
) -> EyeTrackerDevice:
    """Build the configured gaze source."""
    if settings.gaze_source == "mock":
        return SimulatedEyeTrackerDevice(
            sample_rate_hz=60.0,
            realtime=True,
        )

    return TobiiLegacyBridgeDevice(
        host=settings.tobii_bridge_host,
        port=settings.tobii_bridge_port,
    )


class GazeStreamWorker(QThread):
    """Read eye-tracker samples away from the Qt UI thread."""

    sample_received = Signal(object)
    status_changed = Signal(str)
    stream_error = Signal(str)

    def __init__(
        self,
        settings: Settings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._device = create_eye_tracker(settings)

    @property
    def device(self) -> EyeTrackerDevice:
        return self._device

    def run(self) -> None:
        try:
            self.status_changed.emit("正在连接眼动源")
            self._device.connect()
            self.status_changed.emit(f"已连接：{self._device.info.name}")

            self._device.start_stream()
            self.status_changed.emit(f"采集中：{self._device.info.name}")

            while not self.isInterruptionRequested():
                try:
                    sample = self._device.read_sample()
                except TimeoutError:
                    continue
                except DeviceStreamEndedError:
                    if self.isInterruptionRequested():
                        break

                    raise

                self.sample_received.emit(sample)
        except Exception as error:
            if not self.isInterruptionRequested():
                self.stream_error.emit(str(error))
        finally:
            if self._device.state is DeviceState.STREAMING:
                with suppress(Exception):
                    self._device.stop_stream()

            if self._device.state is DeviceState.CONNECTED:
                with suppress(Exception):
                    self._device.disconnect()

            self.status_changed.emit("眼动源已停止")

    def stop(
        self,
        timeout_ms: int = 2_000,
    ) -> None:
        """Request worker shutdown and unblock network reads."""
        self.requestInterruption()

        interrupt = getattr(
            self._device,
            "interrupt",
            None,
        )

        if callable(interrupt):
            interrupt()

        if self.isRunning():
            self.wait(timeout_ms)
