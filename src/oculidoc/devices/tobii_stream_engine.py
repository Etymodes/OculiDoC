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


class TobiiVector3(ctypes.Structure):
    """Three-dimensional Stream Engine vector."""

    _fields_ = [
        ("x", ctypes.c_float),
        ("y", ctypes.c_float),
        ("z", ctypes.c_float),
    ]


class TobiiGazePoint(ctypes.Structure):
    """Combined gaze point from Stream Engine."""

    _fields_ = [
        ("timestamp_us", ctypes.c_int64),
        ("validity", ctypes.c_uint32),
        ("position", TobiiVector2),
    ]


class TobiiEyePositionNormalized(ctypes.Structure):
    """Left and right eye positions normalized within the Tobii track box."""

    _fields_ = [
        ("timestamp_us", ctypes.c_int64),
        ("left_validity", ctypes.c_uint32),
        ("left", TobiiVector3),
        ("right_validity", ctypes.c_uint32),
        ("right", TobiiVector3),
    ]


class TobiiGazeOrigin(ctypes.Structure):
    """Left and right eye origins in millimetres from Stream Engine."""

    _fields_ = [
        ("timestamp_us", ctypes.c_int64),
        ("left_validity", ctypes.c_uint32),
        ("left", TobiiVector3),
        ("right_validity", ctypes.c_uint32),
        ("right", TobiiVector3),
    ]


DeviceUrlCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_char_p,
    ctypes.c_void_p,
)

EyePositionNormalizedCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.POINTER(TobiiEyePositionNormalized),
    ctypes.c_void_p,
)

GazeOriginCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.POINTER(TobiiGazeOrigin),
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

        self.eye_position_normalized_supported = hasattr(
            self.dll,
            "tobii_eye_position_normalized_subscribe",
        ) and hasattr(
            self.dll,
            "tobii_eye_position_normalized_unsubscribe",
        )

        if self.eye_position_normalized_supported:
            self.dll.tobii_eye_position_normalized_subscribe.argtypes = [
                ctypes.c_void_p,
                EyePositionNormalizedCallback,
                ctypes.c_void_p,
            ]
            self.dll.tobii_eye_position_normalized_subscribe.restype = ctypes.c_uint32
            self.dll.tobii_eye_position_normalized_unsubscribe.argtypes = [
                ctypes.c_void_p,
            ]
            self.dll.tobii_eye_position_normalized_unsubscribe.restype = ctypes.c_uint32

        self.gaze_origin_supported = hasattr(
            self.dll,
            "tobii_gaze_origin_subscribe",
        ) and hasattr(
            self.dll,
            "tobii_gaze_origin_unsubscribe",
        )

        if self.gaze_origin_supported:
            self.dll.tobii_gaze_origin_subscribe.argtypes = [
                ctypes.c_void_p,
                GazeOriginCallback,
                ctypes.c_void_p,
            ]
            self.dll.tobii_gaze_origin_subscribe.restype = ctypes.c_uint32
            self.dll.tobii_gaze_origin_unsubscribe.argtypes = [
                ctypes.c_void_p,
            ]
            self.dll.tobii_gaze_origin_unsubscribe.restype = ctypes.c_uint32

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
    left_eye_position_normalized: tuple[float, float, float] | None = None,
    right_eye_position_normalized: tuple[float, float, float] | None = None,
    left_eye_position_mm: tuple[float, float, float] | None = None,
    right_eye_position_mm: tuple[float, float, float] | None = None,
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
        left_eye_position_normalized=left_eye_position_normalized,
        right_eye_position_normalized=right_eye_position_normalized,
        left_eye_position_mm=left_eye_position_mm,
        right_eye_position_mm=right_eye_position_mm,
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
        self._left_eye_position_normalized: tuple[float, float, float] | None = None
        self._right_eye_position_normalized: tuple[float, float, float] | None = None
        self._left_eye_position_mm: tuple[float, float, float] | None = None
        self._right_eye_position_mm: tuple[float, float, float] | None = None
        self._eye_position_subscribed = False
        self._gaze_origin_subscribed = False
        self._eye_position_stream_status = "not_started"
        self._eye_position_stream_detail = "尚未尝试订阅左右眼三维眼位。"
        self._gaze_origin_stream_detail = "尚未尝试订阅毫米眼位。"

        self._url_callback = DeviceUrlCallback(self._receive_device_url)
        self._gaze_callback = GazePointCallback(self._receive_gaze_point)
        self._eye_position_callback = EyePositionNormalizedCallback(
            self._receive_eye_position_normalized
        )
        self._gaze_origin_callback = GazeOriginCallback(self._receive_gaze_origin)
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
                "normalized_eye_position_optional",
                "eye_position_mm_optional",
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

    @property
    def eye_position_stream_status(self) -> str:
        """Return the machine-readable state of the optional binocular stream."""
        return self._eye_position_stream_status

    @property
    def eye_position_stream_detail(self) -> str:
        """Explain why normalized eye position is or is not available."""
        return self._eye_position_stream_detail

    def capability_diagnostics(self) -> tuple[str, ...]:
        """Return optional stream diagnostics without failing combined gaze."""
        return (
            f"左右眼三维眼位：{self._eye_position_stream_detail}"
            f"；毫米距离：{self._gaze_origin_stream_detail}",
        )

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
        gaze_point_pointer: ctypes._Pointer[TobiiGazePoint],
        user_data: int,
    ) -> None:
        del user_data

        if not gaze_point_pointer:
            return

        sample = gaze_point_to_sample(
            gaze_point_pointer.contents,
            sequence=self._sequence,
            left_eye_position_normalized=self._left_eye_position_normalized,
            right_eye_position_normalized=self._right_eye_position_normalized,
            left_eye_position_mm=self._left_eye_position_mm,
            right_eye_position_mm=self._right_eye_position_mm,
        )
        self._sequence += 1
        self._samples.append(sample)

    def _receive_eye_position_normalized(
        self,
        eye_position_pointer: ctypes._Pointer[TobiiEyePositionNormalized],
        user_data: int,
    ) -> None:
        del user_data

        if not eye_position_pointer:
            return

        eye_position = eye_position_pointer.contents
        self._left_eye_position_normalized = (
            (
                float(eye_position.left.x),
                float(eye_position.left.y),
                float(eye_position.left.z),
            )
            if eye_position.left_validity == TOBII_VALIDITY_VALID
            else None
        )
        self._right_eye_position_normalized = (
            (
                float(eye_position.right.x),
                float(eye_position.right.y),
                float(eye_position.right.z),
            )
            if eye_position.right_validity == TOBII_VALIDITY_VALID
            else None
        )
        if (
            self._left_eye_position_normalized is not None
            or self._right_eye_position_normalized is not None
        ):
            self._eye_position_stream_status = "receiving"
            self._eye_position_stream_detail = "已接收左右眼三维眼位样本。"

    def _receive_gaze_origin(
        self,
        gaze_origin_pointer: ctypes._Pointer[TobiiGazeOrigin],
        user_data: int,
    ) -> None:
        del user_data

        if not gaze_origin_pointer:
            return

        gaze_origin = gaze_origin_pointer.contents
        self._left_eye_position_mm = (
            (
                float(gaze_origin.left.x),
                float(gaze_origin.left.y),
                float(gaze_origin.left.z),
            )
            if gaze_origin.left_validity == TOBII_VALIDITY_VALID
            else None
        )
        self._right_eye_position_mm = (
            (
                float(gaze_origin.right.x),
                float(gaze_origin.right.y),
                float(gaze_origin.right.z),
            )
            if gaze_origin.right_validity == TOBII_VALIDITY_VALID
            else None
        )
        if self._left_eye_position_mm is not None or self._right_eye_position_mm is not None:
            self._gaze_origin_stream_detail = "已接收毫米眼位样本。"

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
        self._eye_position_subscribed = False
        self._gaze_origin_subscribed = False
        self._left_eye_position_normalized = None
        self._right_eye_position_normalized = None
        self._left_eye_position_mm = None
        self._right_eye_position_mm = None

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
        self._eye_position_stream_status = "not_started"
        self._eye_position_stream_detail = "尚未尝试订阅左右眼三维眼位。"
        self._gaze_origin_stream_detail = "尚未尝试订阅毫米眼位。"

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
        self._left_eye_position_normalized = None
        self._right_eye_position_normalized = None
        self._left_eye_position_mm = None
        self._right_eye_position_mm = None

        status = library.dll.tobii_gaze_point_subscribe(
            self._device,
            self._gaze_callback,
            None,
        )
        self._check(
            status,
            "订阅 Tobii 视线",
        )

        if library.eye_position_normalized_supported:
            # Eye position is optional. A driver without this stream must not
            # break the working gaze stream used by formal tasks.
            try:
                eye_position_status = library.dll.tobii_eye_position_normalized_subscribe(
                    self._device,
                    self._eye_position_callback,
                    None,
                )
                self._eye_position_subscribed = eye_position_status == TOBII_ERROR_NO_ERROR
                if self._eye_position_subscribed:
                    self._eye_position_stream_status = "subscribed"
                    self._eye_position_stream_detail = "订阅成功，等待左右眼三维眼位样本。"
                else:
                    self._eye_position_stream_status = f"rejected_{eye_position_status}"
                    reason = {
                        TOBII_ERROR_INSUFFICIENT_LICENSE: "当前 Interactive 许可不足",
                        TOBII_ERROR_NOT_SUPPORTED: "设备或当前 Stream Engine 不支持",
                        TOBII_ERROR_NOT_AVAILABLE: "当前不可用",
                    }.get(eye_position_status, "订阅被拒绝")
                    self._eye_position_stream_detail = (
                        f"{reason}（{library.error_message(eye_position_status)}，"
                        f"状态码 {eye_position_status}）；注视点采集继续运行。"
                    )
            except Exception as error:
                self._eye_position_stream_status = "subscribe_error"
                self._eye_position_stream_detail = (
                    f"订阅调用失败：{type(error).__name__}: {error}；注视点采集继续运行。"
                )
        else:
            self._eye_position_stream_status = "symbols_missing"
            self._eye_position_stream_detail = (
                "当前 DLL 不包含 eye_position_normalized 订阅接口；注视点采集继续运行。"
            )

        if getattr(library, "gaze_origin_supported", False):
            try:
                gaze_origin_status = library.dll.tobii_gaze_origin_subscribe(
                    self._device,
                    self._gaze_origin_callback,
                    None,
                )
                self._gaze_origin_subscribed = gaze_origin_status == TOBII_ERROR_NO_ERROR
                if self._gaze_origin_subscribed:
                    self._gaze_origin_stream_detail = "订阅成功，等待毫米眼位样本。"
                else:
                    reason = {
                        TOBII_ERROR_INSUFFICIENT_LICENSE: "当前 Interactive 许可不足",
                        TOBII_ERROR_NOT_SUPPORTED: "设备或当前 Stream Engine 不支持",
                        TOBII_ERROR_NOT_AVAILABLE: "当前不可用",
                    }.get(gaze_origin_status, "订阅被拒绝")
                    self._gaze_origin_stream_detail = (
                        f"{reason}（{library.error_message(gaze_origin_status)}，"
                        f"状态码 {gaze_origin_status}）；标准眼位显示继续运行。"
                    )
            except Exception as error:
                self._gaze_origin_stream_detail = (
                    f"订阅调用失败：{type(error).__name__}: {error}；标准眼位显示继续运行。"
                )
        else:
            self._gaze_origin_stream_detail = "当前 DLL 不包含 gaze_origin 订阅接口。"

        self._state = DeviceState.STREAMING

    def stop_stream(self) -> None:
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("The Tobii device is not streaming.")

        library = self._library

        if library is not None and self._device.value:
            if self._eye_position_subscribed:
                with suppress(Exception):
                    library.dll.tobii_eye_position_normalized_unsubscribe(self._device)
                self._eye_position_subscribed = False

            if self._gaze_origin_subscribed:
                with suppress(Exception):
                    library.dll.tobii_gaze_origin_unsubscribe(self._device)
                self._gaze_origin_subscribed = False

            status = library.dll.tobii_gaze_point_unsubscribe(self._device)
            self._check(
                status,
                "取消 Tobii 视线订阅",
            )

        self._samples.clear()
        self._left_eye_position_normalized = None
        self._right_eye_position_normalized = None
        self._left_eye_position_mm = None
        self._right_eye_position_mm = None
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
