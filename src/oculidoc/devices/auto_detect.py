"""Auto-detect a usable eye tracker without ever falling back to simulation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from time import monotonic, sleep

from oculidoc.devices.contracts import (
    DeviceInfo,
    DeviceKind,
    DeviceState,
    EyeTrackerDevice,
    EyeTrackerSample,
)
from oculidoc.devices.errors import DeviceConnectionError, InvalidDeviceStateError

EyeTrackerFactory = Callable[[], EyeTrackerDevice]


class AutoDetectEyeTrackerDevice:
    """Select the first configured hardware adapter that emits a real sample."""

    def __init__(
        self,
        candidate_factories: tuple[EyeTrackerFactory, ...],
        *,
        probe_timeout_seconds: float = 1.25,
    ) -> None:
        if not candidate_factories:
            raise ValueError("Auto-detection requires at least one hardware candidate.")
        if probe_timeout_seconds <= 0:
            raise ValueError("probe_timeout_seconds must be positive.")

        self.candidate_factories = candidate_factories
        self.probe_timeout_seconds = float(probe_timeout_seconds)
        self._device: EyeTrackerDevice | None = None
        self._prefetched_sample: EyeTrackerSample | None = None
        self._state = DeviceState.DISCONNECTED
        self._undetected_info = DeviceInfo(
            device_id="auto-eye-tracker",
            kind=DeviceKind.EYE_TRACKER,
            name="自动检测眼动传感器",
            manufacturer="OculiDoC",
            model="Hardware auto-detection",
            capabilities=("hardware_only", "no_simulation_fallback"),
        )

    @property
    def info(self) -> DeviceInfo:
        return self._device.info if self._device is not None else self._undetected_info

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def active_device(self) -> EyeTrackerDevice | None:
        return self._device

    @property
    def device_url(self) -> object | None:
        return getattr(self._device, "device_url", None)

    @property
    def library_path(self) -> object | None:
        return getattr(self._device, "library_path", None)

    @staticmethod
    def _cleanup_candidate(candidate: EyeTrackerDevice) -> None:
        if candidate.state is DeviceState.STREAMING:
            with suppress(Exception):
                candidate.stop_stream()
        if candidate.state is DeviceState.CONNECTED:
            with suppress(Exception):
                candidate.disconnect()

    def _probe_sample(self, candidate: EyeTrackerDevice) -> EyeTrackerSample:
        deadline = monotonic() + self.probe_timeout_seconds
        while monotonic() < deadline:
            try:
                return candidate.read_sample()
            except TimeoutError:
                sleep(0.005)
        raise DeviceConnectionError("已连接接口，但检测时限内没有收到眼动样本。")

    def connect(self) -> None:
        if self._state is not DeviceState.DISCONNECTED:
            raise InvalidDeviceStateError("Only a disconnected auto source can connect.")

        failures: list[str] = []
        for factory in self.candidate_factories:
            candidate: EyeTrackerDevice | None = None
            name = "候选眼动接口"
            try:
                candidate = factory()
                name = candidate.info.name
                if candidate.info.is_simulated:
                    raise DeviceConnectionError("自动检测禁止使用模拟眼动源。")
                candidate.connect()
                candidate.start_stream()
                sample = self._probe_sample(candidate)
            except Exception as error:
                failures.append(f"{name}：{error}")
                if candidate is not None:
                    self._cleanup_candidate(candidate)
                continue

            self._device = candidate
            self._prefetched_sample = sample
            self._state = DeviceState.CONNECTED
            return

        detail = "；".join(failures) if failures else "没有可探测接口"
        raise DeviceConnectionError(
            "自动检测未发现可读取的眼动传感器。"
            "已尝试 Tobii Stream Engine 与 OculiDoC 通用 NDJSON 桥接；"
            "第三方或自制传感器需由厂商程序/桥接程序输出 x、y、valid 眼动数据，"
            f"仅出现 USB 或 COM 设备名不能直接生成视线坐标。详情：{detail}"
        )

    def disconnect(self) -> None:
        if self._state is DeviceState.DISCONNECTED:
            return
        if self._state is DeviceState.STREAMING:
            self.stop_stream()
        if self._device is not None:
            if self._device.state is DeviceState.STREAMING:
                self._device.stop_stream()
            if self._device.state is DeviceState.CONNECTED:
                self._device.disconnect()
        self._device = None
        self._prefetched_sample = None
        self._state = DeviceState.DISCONNECTED

    def start_stream(self) -> None:
        if self._state is not DeviceState.CONNECTED or self._device is None:
            raise InvalidDeviceStateError("Connect the auto source before streaming.")
        # Detection deliberately leaves the selected hardware stream open so the
        # verified first sample and connection are not lost between probe and task.
        self._state = DeviceState.STREAMING

    def stop_stream(self) -> None:
        if self._state is not DeviceState.STREAMING or self._device is None:
            raise InvalidDeviceStateError("The auto source is not streaming.")
        if self._device.state is DeviceState.STREAMING:
            self._device.stop_stream()
        self._prefetched_sample = None
        self._state = DeviceState.CONNECTED

    def read_sample(self) -> EyeTrackerSample:
        if self._state is not DeviceState.STREAMING or self._device is None:
            raise InvalidDeviceStateError("The auto source is not streaming.")
        if self._prefetched_sample is not None:
            sample = self._prefetched_sample
            self._prefetched_sample = None
            return sample
        return self._device.read_sample()

    def interrupt(self) -> None:
        interrupt = getattr(self._device, "interrupt", None)
        if callable(interrupt):
            interrupt()
