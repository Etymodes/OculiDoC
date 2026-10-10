"""ThinksTracker gaze source, with bounded isolation of the external native SDK."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from time import monotonic, monotonic_ns
from typing import Any

from pydantic import BaseModel, field_validator

from oculidoc.devices.contracts import (
    DeviceInfo,
    DeviceKind,
    DeviceState,
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.devices.errors import DeviceConnectionError, DeviceReadError, InvalidDeviceStateError
from oculidoc.vendor_helpers import thinks_tracker_worker


class ThinksConfig(BaseModel):
    dll_path: Path
    license_file: Path | None = None
    resolution: str = "1920x1080x120"
    screen_physical: tuple[int, int, int, int, int]
    geometry_confirmed: bool = False
    python_executable: Path | None = None

    @field_validator("resolution")
    @classmethod
    def valid_resolution(cls, value: str) -> str:
        if value not in {"1920x1080x60", "1920x1080x120", "1920x608x300"}:
            raise ValueError("SDK 不支持此采集模式。")
        return value

    @field_validator("screen_physical")
    @classmethod
    def valid_geometry(
        cls, value: tuple[int, int, int, int, int]
    ) -> tuple[int, int, int, int, int]:
        angle, distance, width, height, bottom = value
        if not (
            0 <= angle <= 90
            and 0 <= distance <= 500
            and 50 <= width <= 3000
            and 50 <= height <= 3000
            and 0 <= bottom <= 500
        ):
            raise ValueError("屏幕及安装几何参数超出合理范围。")
        return value


def read_thinks_config(path: Path) -> ThinksConfig:
    try:
        config = ThinksConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
        if not config.dll_path.is_file():
            raise ValueError("找不到 ThinksTracker.dll")
        return config
    except (OSError, ValueError) as error:
        raise DeviceConnectionError(f"ThinksTracker 配置不可用：{path}；{error}") from error


def _worker_command(config: ThinksConfig) -> list[str]:
    if getattr(sys, "frozen", False) and config.python_executable is None:
        return [sys.executable, "--thinks-worker"]
    executable = config.python_executable or Path(sys.executable)
    return [str(executable), "-u", str(Path(thinks_tracker_worker.__file__).resolve())]


class ThinksProcess:
    def __init__(self, config: ThinksConfig, runtime: Path, identity: dict[str, Any]) -> None:
        self.config, self.runtime, self.identity = config, runtime, identity
        self.process: subprocess.Popen[str] | None = None
        self.queue: Queue[dict[str, Any]] = Queue(maxsize=256)
        # Video is diagnostic only. Keep the newest frame so a slow UI never
        # builds up a backlog or delays calibration completion messages.
        self.preview_queue: Queue[dict[str, Any]] = Queue(maxsize=1)
        self.control_queue: Queue[dict[str, Any]] = Queue()
        self._failure: str | None = None
        self._reader: threading.Thread | None = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    def start(self, mode: str = "track") -> dict[str, Any]:
        if self._cancelled.is_set():
            raise DeviceConnectionError("设备连接已取消。")
        if mode != "probe" and not self.config.geometry_confirmed:
            raise DeviceConnectionError("请先在 ThinksTracker 屏幕设置中确认安装参数，再校准。")
        self.runtime.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock:
                if self._cancelled.is_set():
                    raise DeviceConnectionError("设备连接已取消。")
                self.process = subprocess.Popen(
                    _worker_command(self.config),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=self.runtime,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            self._reader = threading.Thread(target=self._read, args=(self.process,), daemon=True)
            self._reader.start()
            self.send(
                {
                    "config": self.config.model_dump(mode="json"),
                    "runtime": str(self.runtime),
                    "identity": self.identity,
                    "mode": mode,
                }
            )
            return self.receive(timeout=20)
        except Exception:
            self.close()
            raise

    def _read(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                if not line.startswith(thinks_tracker_worker.PREFIX):
                    continue
                packet = json.loads(line[len(thinks_tracker_worker.PREFIX) :])
                if packet.get("event") == "error":
                    self._failure = str(packet.get("message", "SDK error"))
                    return
                if packet.get("event") == "preview":
                    with suppress(Empty):
                        self.preview_queue.get_nowait()
                    self.preview_queue.put_nowait(packet)
                    continue
                if packet.get("event") != "sample":
                    self.control_queue.put_nowait(packet)
                    continue
                try:
                    self.queue.put_nowait(packet)
                except Full:
                    with suppress(Empty):
                        self.queue.get_nowait()
                    with suppress(Full):
                        self.queue.put_nowait(packet)
        except (OSError, ValueError) as error:
            self._failure = str(error)
        finally:
            if not self._cancelled.is_set() and self._failure is None:
                self._failure = "ThinksTracker SDK 进程已退出。"

    def send(self, packet: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise DeviceConnectionError("ThinksTracker 未连接。")
        try:
            process.stdin.write(json.dumps(packet) + "\n")
            process.stdin.flush()
        except OSError as error:
            raise DeviceConnectionError("ThinksTracker SDK 进程已断开。") from error

    def receive(self, timeout: float = 0.1) -> dict[str, Any]:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            if self._cancelled.is_set():
                raise DeviceReadError("ThinksTracker 采集已取消。")
            if self._failure:
                raise DeviceConnectionError(self._failure)
            with suppress(Empty):
                return self.control_queue.get_nowait()
            with suppress(Empty):
                return self.preview_queue.get_nowait()
            try:
                return self.queue.get(timeout=min(0.05, max(0.001, deadline - monotonic())))
            except Empty:
                pass
        raise TimeoutError("等待 ThinksTracker 数据超时。")

    def close(self) -> None:
        self._cancelled.set()
        with self._lock:
            process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write('{"command":"close"}\n')
                    process.stdin.flush()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=3)
        if self._reader:
            self._reader.join(timeout=1)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()


class ThinksTrackerDevice:
    def __init__(
        self, config_path: Path, runtime: Path, patient_id: str, screen: tuple[str, int, int]
    ) -> None:
        self.config_path, self.runtime = config_path, runtime
        self.identity = {"patient_id": patient_id, "screen": list(screen) if screen[0] else []}
        self._process: ThinksProcess | None = None
        self._state = DeviceState.DISCONNECTED
        self._sequence = 0
        self.library_path: Path | None = None
        self._info = DeviceInfo(
            device_id="thinks-tracker",
            kind=DeviceKind.EYE_TRACKER,
            name="ThinksTracker 眼动仪",
            manufacturer="ThinksTracker",
            model="SDK V2.3",
            capabilities=("normalized_gaze", "binocular_validity", "isolated_native_sdk"),
        )

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def info(self) -> DeviceInfo:
        return self._info

    def capability_diagnostics(self) -> tuple[str, ...]:
        return (
            "使用主机接收时间；SDK 时间戳单位未说明，不用于设备时钟同步。",
            "瞳孔宽高为图像测量，未转换为毫米；未声明眨眼或三维眼位能力。",
        )

    def connect(self) -> None:
        if self._state != DeviceState.DISCONNECTED:
            raise InvalidDeviceStateError("ThinksTracker 已连接。")
        config = read_thinks_config(self.config_path)
        self.library_path = config.dll_path
        self._process = ThinksProcess(config, self.runtime, self.identity)
        try:
            ready = self._process.start()
            if ready.get("event") != "ready":
                raise DeviceConnectionError("SDK 未返回就绪状态。")
            self._info = DeviceInfo(
                device_id=f"thinks:{ready['vid']:04x}:{ready['pid']:04x}",
                kind=DeviceKind.EYE_TRACKER,
                name=f"ThinksTracker {ready['name']}",
                manufacturer="ThinksTracker",
                model=f"{ready['fps']} Hz · SDK V2.3",
                capabilities=self._info.capabilities,
            )
            self._state = DeviceState.CONNECTED
        except Exception:
            self.disconnect()
            raise

    def start_stream(self) -> None:
        if self._state != DeviceState.CONNECTED:
            raise InvalidDeviceStateError("请先连接 ThinksTracker。")
        if self._process is None:
            self._state = DeviceState.DISCONNECTED
            self.connect()
        self._state = DeviceState.STREAMING

    def stop_stream(self) -> None:
        # SDK handles are process-owned; stopping releases the hardware completely.
        if self._state != DeviceState.STREAMING:
            raise InvalidDeviceStateError("ThinksTracker 未开始采集。")
        self.disconnect()
        self._state = DeviceState.CONNECTED

    def disconnect(self) -> None:
        if self._process:
            self._process.close()
            self._process = None
        self._state = DeviceState.DISCONNECTED

    def interrupt(self) -> None:
        self.disconnect()

    def read_sample(self) -> EyeTrackerSample:
        process = self._process
        if self._state != DeviceState.STREAMING or process is None:
            raise InvalidDeviceStateError("ThinksTracker 未开始采集。")
        packet = process.receive()
        if packet.get("event") != "sample":
            raise DeviceReadError("SDK 返回了非采样响应。")
        received_ns = int(packet["received_ns"])
        if not 0 <= monotonic_ns() - received_ns <= 500_000_000:
            raise TimeoutError("丢弃过期的 ThinksTracker 样本。")
        sample = EyeTrackerSample(
            timestamp=DeviceTimestamp(
                sequence=self._sequence,
                monotonic_timestamp_ns=received_ns,
                utc_timestamp=datetime.now(UTC),
            ),
            gaze_x_normalized=packet["x"],
            gaze_y_normalized=packet["y"],
            left_eye_valid=packet["left_valid"],
            right_eye_valid=packet["right_valid"],
        )
        self._sequence += 1
        return sample
