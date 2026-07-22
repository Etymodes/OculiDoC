"""Qt worker for simulated or bridged gaze input."""

from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic

from PySide6.QtCore import QObject, QThread, Signal

from oculidoc.config import Settings
from oculidoc.devices.auto_detect import AutoDetectEyeTrackerDevice, EyeTrackerFactory
from oculidoc.devices.contracts import (
    DeviceState,
    EyeTrackerDevice,
)
from oculidoc.devices.errors import (
    DeviceConnectionError,
    DeviceStreamEndedError,
)
from oculidoc.devices.preflight import (
    GazePreflightResult,
    GazePreflightStore,
    failed_gaze_preflight,
    run_gaze_preflight,
)
from oculidoc.devices.simulated import (
    SimulatedEyeTrackerDevice,
)
from oculidoc.devices.tobii_hospital_bridge import (
    TobiiHospitalBridgeDevice,
)
from oculidoc.devices.tobii_legacy_bridge import (
    TobiiLegacyBridgeDevice,
)
from oculidoc.devices.tobii_stream_engine import (
    TobiiStreamEngineDevice,
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

    if settings.gaze_source == "auto":
        candidates: list[EyeTrackerFactory] = [
            lambda: TobiiStreamEngineDevice(library_path=settings.tobii_stream_engine_dll),
            lambda: TobiiLegacyBridgeDevice(
                host=settings.tobii_bridge_host,
                port=settings.tobii_bridge_port,
                connect_timeout_seconds=0.75,
            ),
        ]
        if settings.tobii_helper_executable is not None:
            candidates.append(
                lambda: TobiiHospitalBridgeDevice(
                    host=settings.tobii_bridge_bind_host,
                    port=settings.tobii_bridge_port,
                    screen_width_px=settings.tobii_screen_width_px,
                    screen_height_px=settings.tobii_screen_height_px,
                    helper_executable=settings.tobii_helper_executable,
                )
            )
        return AutoDetectEyeTrackerDevice(tuple(candidates))

    if settings.gaze_source == "tobii_stream_engine":
        return TobiiStreamEngineDevice(library_path=(settings.tobii_stream_engine_dll))

    if settings.tobii_bridge_mode == "hospital_server":
        return TobiiHospitalBridgeDevice(
            host=(settings.tobii_bridge_bind_host),
            port=settings.tobii_bridge_port,
            screen_width_px=(settings.tobii_screen_width_px),
            screen_height_px=(settings.tobii_screen_height_px),
            helper_executable=(settings.tobii_helper_executable),
        )

    return TobiiLegacyBridgeDevice(
        host=settings.tobii_bridge_host,
        port=settings.tobii_bridge_port,
    )


class GazeStreamWorker(QThread):
    """Read eye-tracker samples away from the Qt UI thread."""

    sample_received = Signal(object)
    preflight_completed = Signal(object)
    status_changed = Signal(str)
    stream_error = Signal(str)

    def __init__(
        self,
        settings: Settings,
        parent: QObject | None = None,
        *,
        preflight_seconds: float | None = None,
        preflight_store: GazePreflightStore | None = None,
    ) -> None:
        super().__init__(parent)
        if preflight_seconds is not None and preflight_seconds < 0:
            raise ValueError("preflight_seconds cannot be negative.")

        self._settings = settings
        self._device = create_eye_tracker(settings)
        self._preflight_seconds = preflight_seconds
        self._preflight_store = preflight_store
        self._sample_delivery_enabled = preflight_seconds is None

    @property
    def device(self) -> EyeTrackerDevice:
        return self._device

    def enable_sample_delivery(self) -> None:
        """Forward subsequent live samples after preflight and countdown."""
        self._sample_delivery_enabled = True

    def run(self) -> None:
        preflight_result: GazePreflightResult | None = None

        try:
            self.status_changed.emit("正在连接眼动源")
            self._device.connect()
            self.status_changed.emit(f"已连接：{self._device.info.name}")

            self._device.start_stream()

            if self._preflight_seconds is not None:
                self.status_changed.emit(
                    f"设备预检中：{self._device.info.name} · {self._preflight_seconds:g} 秒"
                )
                preflight_result = run_gaze_preflight(
                    self._device,
                    source=self._settings.gaze_source,
                    duration_seconds=self._preflight_seconds,
                    minimum_valid_ratio=self._settings.gaze_minimum_valid_ratio,
                )

                if self._preflight_store is not None:
                    self._preflight_store.save(preflight_result)

                self.preflight_completed.emit(preflight_result)

                if not preflight_result.passed:
                    raise DeviceConnectionError(preflight_result.error or "眼动设备预检未通过。")

            self.status_changed.emit(f"采集中：{self._device.info.name}")
            live_started_at = monotonic()
            live_sample_count = 0
            live_valid_sample_count = 0

            while not self.isInterruptionRequested():
                try:
                    sample = self._device.read_sample()
                except TimeoutError:
                    continue
                except DeviceStreamEndedError:
                    if self.isInterruptionRequested():
                        break

                    raise

                if self._sample_delivery_enabled:
                    self.sample_received.emit(sample)
                live_sample_count += 1
                live_valid_sample_count += int(sample.gaze_valid)
                live_elapsed = monotonic() - live_started_at

                if (
                    preflight_result is not None
                    and self._preflight_store is not None
                    and live_elapsed >= 1.0
                ):
                    valid_ratio = live_valid_sample_count / live_sample_count
                    self._preflight_store.save(
                        replace(
                            preflight_result,
                            duration_seconds=live_elapsed,
                            sample_count=live_sample_count,
                            valid_sample_count=live_valid_sample_count,
                            sample_rate_hz=live_sample_count / live_elapsed,
                            valid_ratio=valid_ratio,
                            passed=(valid_ratio >= self._settings.gaze_minimum_valid_ratio),
                            error=None,
                            updated_at_utc=datetime.now(UTC).isoformat(),
                        )
                    )
                    live_started_at = monotonic()
                    live_sample_count = 0
                    live_valid_sample_count = 0
        except Exception as error:
            if not self.isInterruptionRequested():
                if preflight_result is None:
                    preflight_result = failed_gaze_preflight(
                        source=self._settings.gaze_source,
                        device_name=self._device.info.name,
                        minimum_valid_ratio=self._settings.gaze_minimum_valid_ratio,
                        error=str(error),
                    )

                    if self._preflight_store is not None:
                        self._preflight_store.save(preflight_result)

                    self.preflight_completed.emit(preflight_result)

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
