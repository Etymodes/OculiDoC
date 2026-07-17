"""Server adapter for the hospital Tobii helper."""

import json
import os
import socket
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic_ns

from oculidoc.devices.contracts import (
    DeviceInfo,
    DeviceKind,
    DeviceState,
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.devices.errors import (
    DeviceConnectionError,
    DeviceReadError,
    InvalidDeviceStateError,
)


class TobiiHospitalBridgeDevice:
    """Receive gaze JSON from MCeyegazethesisNET461."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 9999,
        screen_width_px: int = 1920,
        screen_height_px: int = 1080,
        helper_executable: str | Path | None = None,
        accept_timeout_seconds: float = 0.25,
        read_timeout_seconds: float = 0.25,
        maximum_message_bytes: int = 1_048_576,
    ) -> None:
        normalized_host = host.strip()

        if not normalized_host:
            raise ValueError("host cannot be empty.")

        if not 1 <= port <= 65_535:
            raise ValueError("port must be between 1 and 65535.")

        if screen_width_px <= 0:
            raise ValueError("screen_width_px must be positive.")

        if screen_height_px <= 0:
            raise ValueError("screen_height_px must be positive.")

        if accept_timeout_seconds <= 0:
            raise ValueError("accept_timeout_seconds must be positive.")

        if read_timeout_seconds <= 0:
            raise ValueError("read_timeout_seconds must be positive.")

        if maximum_message_bytes <= 0:
            raise ValueError("maximum_message_bytes must be positive.")

        self.host = normalized_host
        self.port = port
        self.screen_width_px = screen_width_px
        self.screen_height_px = screen_height_px
        self.helper_executable = (
            Path(helper_executable).expanduser().resolve()
            if helper_executable is not None
            else None
        )
        self.accept_timeout_seconds = float(accept_timeout_seconds)
        self.read_timeout_seconds = float(read_timeout_seconds)
        self.maximum_message_bytes = maximum_message_bytes

        self._state = DeviceState.DISCONNECTED
        self._listener: socket.socket | None = None
        self._client: socket.socket | None = None
        self._buffer = bytearray()
        self._sequence = 0
        self._helper_process: subprocess.Popen[bytes] | None = None

        self._info = DeviceInfo(
            device_id=(f"hospital-tobii:{self.host}:{self.port}"),
            kind=DeviceKind.EYE_TRACKER,
            name="医院 Tobii 眼动仪",
            manufacturer="Tobii",
            model=("MCeyegazethesisNET461 TCP Bridge"),
            is_simulated=False,
            capabilities=(
                "screen_pixel_gaze",
                "tcp_server",
                "ndjson",
                "legacy_raw_xy",
            ),
        )

    @property
    def info(self) -> DeviceInfo:
        return self._info

    @property
    def state(self) -> DeviceState:
        return self._state

    def connect(self) -> None:
        if self._state is not DeviceState.DISCONNECTED:
            raise InvalidDeviceStateError("Only a disconnected bridge can connect.")

        listener = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
        )
        listener.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )

        try:
            listener.bind(
                (
                    self.host,
                    self.port,
                )
            )
            listener.listen(1)
            listener.settimeout(self.accept_timeout_seconds)
        except OSError as error:
            listener.close()
            raise DeviceConnectionError(
                f"无法监听医院眼动程序：{self.host}:{self.port}；{error}"
            ) from error

        self._listener = listener
        self._state = DeviceState.CONNECTED

    def _launch_helper(self) -> None:
        executable = self.helper_executable

        if executable is None:
            return

        if not executable.is_file():
            raise DeviceConnectionError(f"眼动仪桥接程序不存在：{executable}")

        startupinfo = None
        creationflags = 0

        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self._helper_process = subprocess.Popen(
                [str(executable)],
                cwd=str(executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except OSError as error:
            raise DeviceConnectionError(f"无法启动医院眼动仪桥接程序：{error}") from error

    def start_stream(self) -> None:
        if self._state is not DeviceState.CONNECTED:
            raise InvalidDeviceStateError("Connect the hospital bridge before streaming.")

        self._sequence = 0
        self._buffer.clear()
        self._state = DeviceState.STREAMING

        try:
            self._launch_helper()
        except Exception:
            self._state = DeviceState.CONNECTED
            raise

    def _close_client(self) -> None:
        client = self._client
        self._client = None
        self._buffer.clear()

        if client is None:
            return

        with suppress(OSError):
            client.shutdown(socket.SHUT_RDWR)

        client.close()

    def stop_stream(self) -> None:
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("The hospital bridge is not streaming.")

        self._close_client()
        self._state = DeviceState.CONNECTED

    def _stop_helper(self) -> None:
        process = self._helper_process
        self._helper_process = None

        if process is None or process.poll() is not None:
            return

        process.terminate()

        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)

    def disconnect(self) -> None:
        if self._state is DeviceState.STREAMING:
            self.stop_stream()

        self._close_client()

        listener = self._listener
        self._listener = None

        if listener is not None:
            listener.close()

        self._stop_helper()
        self._state = DeviceState.DISCONNECTED

    def interrupt(self) -> None:
        """Unblock accept or recv during worker shutdown."""
        self._close_client()

        listener = self._listener
        self._listener = None

        if listener is not None:
            listener.close()

    def _accept_client(self) -> socket.socket:
        if self._client is not None:
            return self._client

        listener = self._listener

        if self._state is not DeviceState.STREAMING or listener is None:
            raise InvalidDeviceStateError("The hospital bridge is not streaming.")

        try:
            client, _ = listener.accept()
        except TimeoutError as error:
            raise TimeoutError("正在等待医院眼动程序连接。") from error
        except OSError as error:
            raise TimeoutError("医院眼动监听已停止。") from error

        client.settimeout(self.read_timeout_seconds)
        self._client = client
        self._buffer.clear()

        return client

    def _read_line(self) -> str:
        client = self._accept_client()

        while b"\n" not in self._buffer:
            try:
                chunk = client.recv(4096)
            except TimeoutError as error:
                raise TimeoutError("暂时没有新的眼动样本。") from error
            except OSError as error:
                self._close_client()
                raise TimeoutError("医院眼动程序连接已中断。") from error

            if not chunk:
                self._close_client()
                raise TimeoutError("医院眼动程序已断开连接。")

            self._buffer.extend(chunk)

            if len(self._buffer) > self.maximum_message_bytes:
                self._close_client()
                raise DeviceReadError("医院眼动数据包超过大小限制。")

        raw_line, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)

        try:
            return raw_line.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise DeviceReadError("医院眼动数据不是 UTF-8。") from error

    @staticmethod
    def _coordinate(
        payload: dict[str, object],
        name: str,
    ) -> float:
        try:
            value = float(payload[name])
        except KeyError as error:
            raise DeviceReadError(f"医院眼动数据缺少 {name}。") from error
        except (TypeError, ValueError) as error:
            raise DeviceReadError(f"{name} 必须是数值。") from error

        if not isfinite(value):
            raise DeviceReadError(f"{name} 必须是有限数值。")

        return value

    def _parse_payload(
        self,
        payload: dict[str, object],
    ) -> EyeTrackerSample:
        screen_x = self._coordinate(
            payload,
            "ScreenX",
        )
        screen_y = self._coordinate(
            payload,
            "ScreenY",
        )

        valid = 0.0 <= screen_x <= self.screen_width_px and 0.0 <= screen_y <= self.screen_height_px

        gaze_x = screen_x / self.screen_width_px if valid else None
        gaze_y = screen_y / self.screen_height_px if valid else None

        sample = EyeTrackerSample(
            timestamp=DeviceTimestamp(
                sequence=self._sequence,
                monotonic_timestamp_ns=(monotonic_ns()),
                utc_timestamp=datetime.now(UTC),
                source_clock_id=("MCeyegazethesisNET461"),
            ),
            gaze_x_normalized=gaze_x,
            gaze_y_normalized=gaze_y,
            left_eye_valid=valid,
            right_eye_valid=valid,
        )

        self._sequence += 1
        return sample

    def read_sample(self) -> EyeTrackerSample:
        while True:
            line = self._read_line()

            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise DeviceReadError("医院眼动程序发送了无效 JSON。") from error

            if not isinstance(payload, dict):
                continue

            return self._parse_payload(payload)
