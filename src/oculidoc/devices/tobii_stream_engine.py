"""Native Tobii Stream Engine eye-tracker adapter."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns
from typing import Final

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

TOBII_ERROR_NO_ERROR: Final = 0
TOBII_ERROR_INSUFFICIENT_LICENSE: Final = 2
TOBII_ERROR_NOT_SUPPORTED: Final = 3
TOBII_ERROR_NOT_AVAILABLE: Final = 4
TOBII_ERROR_CONNECTION_FAILED: Final = 5
TOBII_ERROR_TIMED_OUT: Final = 6

TOBII_VALIDITY_VALID: Final = 1
TOBII_FIELD_OF_USE_INTERACTIVE: Final = 1


class TobiiVector2(ctypes.Structure):
    """Two-dimensional Stream Engine vector."""

    _fields_ = [
        ("x", ctypes.c_float),
        ("y", ctypes.c_float),
    ]


class TobiiGazePoint(ctypes.Structure):
    """Combined gaze point from Stream Engine."""

    _fields_ = [
        ("timestamp_us", ctypes.c_int64),
        ("validity", ctypes.c_uint32),
        ("position", TobiiVector2),
    ]


DeviceUrlCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_char_p,
    ctypes.c_void_p,
)

GazePointCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.POINTER(TobiiGazePoint),
    ctypes.c_void_p,
)


def _candidate_roots() -> tuple[Path, ...]:
    roots: list[Path] = []

    for environment_name in (
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramData",
        "LOCALAPPDATA",
    ):
        raw_value = os.environ.get(environment_name)

        if raw_value:
            roots.append(Path(raw_value))

    return tuple(roots)


def discover_tobii_stream_engine_dll(
    explicit_path: str | Path | None = None,
) -> Path | None:
    """Find a system-installed Stream Engine library."""
    candidates: list[Path] = []

    if explicit_path is not None:
        candidates.append(Path(explicit_path).expanduser().resolve())

    environment_path = os.environ.get("OCULIDOC_TOBII_STREAM_ENGINE_DLL")

    if environment_path:
        candidates.append(Path(environment_path).expanduser().resolve())

    discovered_name = ctypes.util.find_library("tobii_stream_engine")

    if discovered_name:
        discovered_path = Path(discovered_name)

        if discovered_path.is_absolute():
            candidates.append(discovered_path)

    relative_candidates = (
        Path(
            "Tobii",
            "Tobii.EyeTracker5",
            "tobii_stream_engine.dll",
        ),
        Path(
            "Tobii",
            "Tobii Runtime",
            "tobii_stream_engine.dll",
        ),
        Path(
            "Tobii",
            "Tobii Eye Tracking",
            "tobii_stream_engine.dll",
        ),
        Path(
            "Tobii",
            "Tobii Experience",
            "tobii_stream_engine.dll",
        ),
    )

    roots = _candidate_roots()

    for directory_root in roots:
        for relative_path in relative_candidates:
            candidates.append(directory_root / relative_path)

    checked: set[Path] = set()

    for candidate in candidates:
        resolved = candidate.resolve()

        if resolved in checked:
            continue

        checked.add(resolved)

        if resolved.is_file():
            return resolved

    for directory_root in roots:
        tobii_root = directory_root / "Tobii"

        if not tobii_root.is_dir():
            continue

        try:
            matches = sorted(tobii_root.rglob("tobii_stream_engine.dll"))
        except OSError:
            continue

        for match in matches:
            if match.is_file():
                return match.resolve()

    return None


class TobiiStreamEngineLibrary:
    """Bound functions from tobii_stream_engine.dll."""

    def __init__(
        self,
        library_path: Path,
    ) -> None:
        self.library_path = library_path
        self._dll_directory = None

        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(library_path.parent))

        try:
            self.dll = ctypes.CDLL(str(library_path))
        except OSError as error:
            if self._dll_directory is not None:
                self._dll_directory.close()
                self._dll_directory = None

            raise DeviceConnectionError(
                f"无法加载 Tobii Stream Engine：{library_path}\n{error}"
            ) from error

        self._bind_functions()

    def close(self) -> None:
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None

    def _bind_functions(self) -> None:
        self.dll.tobii_error_message.argtypes = [
            ctypes.c_uint32,
        ]
        self.dll.tobii_error_message.restype = ctypes.c_char_p

        self.dll.tobii_api_create.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self.dll.tobii_api_create.restype = ctypes.c_uint32

        self.dll.tobii_api_destroy.argtypes = [
            ctypes.c_void_p,
        ]
        self.dll.tobii_api_destroy.restype = ctypes.c_uint32

        self.dll.tobii_enumerate_local_device_urls.argtypes = [
            ctypes.c_void_p,
            DeviceUrlCallback,
            ctypes.c_void_p,
        ]
        self.dll.tobii_enumerate_local_device_urls.restype = ctypes.c_uint32

        self.dll.tobii_device_create.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.dll.tobii_device_create.restype = ctypes.c_uint32

        self.dll.tobii_device_destroy.argtypes = [
            ctypes.c_void_p,
        ]
        self.dll.tobii_device_destroy.restype = ctypes.c_uint32

        self.dll.tobii_gaze_point_subscribe.argtypes = [
            ctypes.c_void_p,
            GazePointCallback,
            ctypes.c_void_p,
        ]
        self.dll.tobii_gaze_point_subscribe.restype = ctypes.c_uint32

        self.dll.tobii_gaze_point_unsubscribe.argtypes = [
            ctypes.c_void_p,
        ]
        self.dll.tobii_gaze_point_unsubscribe.restype = ctypes.c_uint32

        self.dll.tobii_device_process_callbacks.argtypes = [
            ctypes.c_void_p,
        ]
        self.dll.tobii_device_process_callbacks.restype = ctypes.c_uint32

    def error_message(
        self,
        status: int,
    ) -> str:
        raw_message = self.dll.tobii_error_message(status)

        if not raw_message:
            return f"Tobii error {status}"

        return raw_message.decode(
            "utf-8",
            errors="replace",
        )


def gaze_point_to_sample(
    gaze_point: TobiiGazePoint,
    *,
    sequence: int,
) -> EyeTrackerSample:
    """Convert a native combined gaze point."""
    valid = gaze_point.validity == TOBII_VALIDITY_VALID

    gaze_x = float(gaze_point.position.x) if valid else None
    gaze_y = float(gaze_point.position.y) if valid else None

    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=monotonic_ns(),
            utc_timestamp=datetime.now(UTC),
            source_timestamp_ns=(
                int(gaze_point.timestamp_us) * 1_000 if gaze_point.timestamp_us >= 0 else None
            ),
            source_clock_id=("tobii-stream-engine"),
        ),
        gaze_x_normalized=gaze_x,
        gaze_y_normalized=gaze_y,
        left_eye_valid=valid,
        right_eye_valid=valid,
    )


class TobiiStreamEngineDevice:
    """Direct Eye Tracker 5 Stream Engine device."""

    def __init__(
        self,
        *,
        library_path: str | Path | None = None,
    ) -> None:
        self.requested_library_path = (
            Path(library_path).expanduser().resolve() if library_path is not None else None
        )

        self._state = DeviceState.DISCONNECTED
        self._library: TobiiStreamEngineLibrary | None = None
        self._api = ctypes.c_void_p()
        self._device = ctypes.c_void_p()
        self._device_url: str | None = None
        self._sequence = 0
        self._samples: deque[EyeTrackerSample] = deque(maxlen=256)

        self._url_callback = DeviceUrlCallback(self._receive_device_url)
        self._gaze_callback = GazePointCallback(self._receive_gaze_point)
        self._enumerated_urls: list[str] = []

        self._info = DeviceInfo(
            device_id="tobii-stream-engine-0",
            kind=DeviceKind.EYE_TRACKER,
            name="Tobii Eye Tracker 5",
            manufacturer="Tobii",
            model="Stream Engine",
            is_simulated=False,
            capabilities=(
                "combined_gaze_point",
                "normalized_gaze",
                "interactive_input",
                "native_stream_engine",
            ),
        )

    @property
    def info(self) -> DeviceInfo:
        return self._info

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def library_path(self) -> Path | None:
        if self._library is None:
            return None

        return self._library.library_path

    @property
    def device_url(self) -> str | None:
        return self._device_url

    def _check(
        self,
        status: int,
        operation: str,
    ) -> None:
        if status == TOBII_ERROR_NO_ERROR:
            return

        library = self._library
        message = library.error_message(status) if library is not None else f"Tobii error {status}"

        if status == TOBII_ERROR_TIMED_OUT:
            raise TimeoutError(message)

        if status in {
            TOBII_ERROR_INSUFFICIENT_LICENSE,
            TOBII_ERROR_NOT_SUPPORTED,
        }:
            raise DeviceConnectionError(
                f"{operation}失败：{message}。当前 Tobii 许可可能不允许该数据流。"
            )

        raise DeviceReadError(f"{operation}失败：{message}")

    def _receive_device_url(
        self,
        raw_url: bytes | None,
        user_data: int,
    ) -> None:
        del user_data

        if not raw_url:
            return

        self._enumerated_urls.append(
            raw_url.decode(
                "utf-8",
                errors="replace",
            )
        )

    def _receive_gaze_point(
        self,
        gaze_point_pointer: (ctypes.POINTER(TobiiGazePoint)),
        user_data: int,
    ) -> None:
        del user_data

        if not gaze_point_pointer:
            return

        sample = gaze_point_to_sample(
            gaze_point_pointer.contents,
            sequence=self._sequence,
        )
        self._sequence += 1
        self._samples.append(sample)

    def _cleanup_native_handles(self) -> None:
        library = self._library

        if library is None:
            return

        if self._device.value:
            with suppress(Exception):
                library.dll.tobii_device_destroy(self._device)

            self._device = ctypes.c_void_p()

        if self._api.value:
            with suppress(Exception):
                library.dll.tobii_api_destroy(self._api)

            self._api = ctypes.c_void_p()

        library.close()
        self._library = None
        self._device_url = None

    def connect(self) -> None:
        if self._state is not DeviceState.DISCONNECTED:
            raise InvalidDeviceStateError("Only a disconnected Tobii device can connect.")

        library_path = discover_tobii_stream_engine_dll(self.requested_library_path)

        if library_path is None:
            raise DeviceConnectionError(
                "未找到系统安装的 "
                "tobii_stream_engine.dll。"
                "请先确认 Tobii Experience 和 "
                "Eye Tracker 5 驱动已安装。"
            )

        self._library = TobiiStreamEngineLibrary(library_path)
        library = self._library

        try:
            status = library.dll.tobii_api_create(
                ctypes.byref(self._api),
                None,
                None,
            )
            self._check(
                status,
                "创建 Tobii API",
            )

            self._enumerated_urls.clear()

            status = library.dll.tobii_enumerate_local_device_urls(
                self._api,
                self._url_callback,
                None,
            )
            self._check(
                status,
                "枚举 Tobii 设备",
            )

            if not self._enumerated_urls:
                raise DeviceConnectionError(
                    "Tobii Stream Engine 已加载，但没有发现 Eye Tracker 5。"
                )

            self._device_url = self._enumerated_urls[0]

            status = library.dll.tobii_device_create(
                self._api,
                self._device_url.encode("utf-8"),
                TOBII_FIELD_OF_USE_INTERACTIVE,
                ctypes.byref(self._device),
            )
            self._check(
                status,
                "连接 Tobii Eye Tracker 5",
            )
        except Exception:
            self._cleanup_native_handles()
            raise

        self._state = DeviceState.CONNECTED

    def disconnect(self) -> None:
        if self._state is DeviceState.STREAMING:
            self.stop_stream()

        if self._state is DeviceState.DISCONNECTED:
            return

        self._cleanup_native_handles()
        self._samples.clear()
        self._state = DeviceState.DISCONNECTED

    def start_stream(self) -> None:
        if self._state is not DeviceState.CONNECTED:
            raise InvalidDeviceStateError(
                "Connect the Tobii device before starting gaze streaming."
            )

        library = self._library

        if library is None or not self._device.value:
            raise DeviceConnectionError("Tobii native device is unavailable.")

        self._sequence = 0
        self._samples.clear()

        status = library.dll.tobii_gaze_point_subscribe(
            self._device,
            self._gaze_callback,
            None,
        )
        self._check(
            status,
            "订阅 Tobii 视线",
        )

        self._state = DeviceState.STREAMING

    def stop_stream(self) -> None:
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("The Tobii device is not streaming.")

        library = self._library

        if library is not None and self._device.value:
            status = library.dll.tobii_gaze_point_unsubscribe(self._device)
            self._check(
                status,
                "取消 Tobii 视线订阅",
            )

        self._samples.clear()
        self._state = DeviceState.CONNECTED

    def read_sample(self) -> EyeTrackerSample:
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("The Tobii device is not streaming.")

        if self._samples:
            return self._samples.popleft()

        library = self._library

        if library is None or not self._device.value:
            raise DeviceConnectionError("Tobii native device is unavailable.")

        status = library.dll.tobii_device_process_callbacks(self._device)

        if status == TOBII_ERROR_TIMED_OUT:
            raise TimeoutError("No Tobii gaze sample is available.")

        self._check(
            status,
            "处理 Tobii 视线回调",
        )

        if not self._samples:
            raise TimeoutError("No Tobii gaze sample is available.")

        return self._samples.popleft()

    def interrupt(self) -> None:
        """Native callback processing is non-blocking."""
