"""Isolated Windows C ABI host for the externally supplied ThinksTracker V2.3 SDK.

Only stdlib imports: the vendor OpenCV/Qt dependencies must never enter the UI process.
Run as a script; commands and responses use a private stdin/stdout pipe, not a network port.
"""

from __future__ import annotations

import base64
import ctypes as c
import hashlib
import json
import math
import os
import sys
import threading
import zlib
from contextlib import suppress
from pathlib import Path
from queue import Empty, Full, Queue
from time import monotonic, monotonic_ns
from typing import Any

PREFIX = "OCULIDOC_THINKS:"
PREVIEW_INTERVAL_SECONDS = 0.125
PREVIEW_MAX_WIDTH = 320
PREVIEW_MAX_HEIGHT = 180
EMIT_LOCK = threading.Lock()


class Device(c.Structure):
    _fields_ = [
        ("name", c.c_char * 128),
        ("vid", c.c_uint16),
        ("pid", c.c_uint16),
        ("uid", c.c_char * 128),
    ]


class Point(c.Structure):
    _fields_ = [("x", c.c_float), ("y", c.c_float)]


class Vec3(c.Structure):
    _fields_ = [("x", c.c_float), ("y", c.c_float), ("z", c.c_float)]


class Bbox(c.Structure):
    _fields_ = [(name, c.c_int) for name in ("left", "top", "width", "height")]


class Eye(c.Structure):
    _fields_ = [
        ("bbox", Bbox),
        ("pupil_center", Point),
        ("pupil_width", c.c_float),
        ("pupil_height", c.c_float),
        ("pupil_angle", c.c_float),
        ("purkinje", Point * 2),
    ]


class SightLine(c.Structure):
    _fields_ = [("cornea", Vec3), ("direction", Vec3)]


class TimeRange(c.Structure):
    _fields_ = [("start", c.c_int64), ("end", c.c_int64)]


class Output(c.Structure):
    # Default MSVC x64 alignment, including the 4-byte pad before TimeRange.
    _fields_ = [
        ("left_info", c.c_int),
        ("right_info", c.c_int),
        ("merge_valid", c.c_int),
        ("left_valid", c.c_int),
        ("right_valid", c.c_int),
        ("left_blink", c.c_int),
        ("right_blink", c.c_int),
        ("left_blink_time", TimeRange),
        ("right_blink_time", TimeRange),
        ("pts", c.c_int64),
        ("left_eye", Eye),
        ("right_eye", Eye),
        ("merge", Point),
        ("left", Point),
        ("right", Point),
        ("left_line", SightLine),
        ("right_line", SightLine),
        ("distance", c.c_float),
    ]


class Physical(c.Structure):
    _fields_ = [(name, c.c_int) for name in ("angle", "distance", "width", "height", "bottom")]


class Stream(c.Structure):
    _fields_ = [(name, c.c_uint32) for name in ("width", "height", "fps")]


TRACK_CB = c.CFUNCTYPE(None, c.c_void_p, c.POINTER(Output))
CAMERA_CB = c.CFUNCTYPE(None, c.c_void_p, c.c_void_p, c.c_uint32, c.c_uint32, c.c_uint32)
FINISH_CB = c.CFUNCTYPE(None, c.c_void_p, c.c_uint32)
EYE_DETECT_CB = c.CFUNCTYPE(None, c.c_void_p, c.POINTER(Bbox), c.POINTER(Bbox))


def downsample_gray(raw: bytes, width: int, height: int) -> tuple[bytes, int, int]:
    """Nearest-neighbour reduction of the SDK's packed, eight-bit gray frame."""
    step = max(
        1,
        (width + PREVIEW_MAX_WIDTH - 1) // PREVIEW_MAX_WIDTH,
        (height + PREVIEW_MAX_HEIGHT - 1) // PREVIEW_MAX_HEIGHT,
    )
    output_width = (width + step - 1) // step
    output_height = (height + step - 1) // step
    output = bytearray(output_width * output_height)
    for row in range(output_height):
        source = row * step * width
        target = row * output_width
        output[target : target + output_width] = raw[source : source + width : step]
    return bytes(output), output_width, output_height


def preview_bbox(
    bbox: tuple[int, int, int, int] | None,
    source_width: int,
    source_height: int,
    preview_width: int,
    preview_height: int,
) -> list[int] | None:
    """Map SDK eye boxes using the vendor demo's /8-on-a-half-size-image rule."""
    if bbox is None or bbox[2] <= 0 or bbox[3] <= 0:
        return None
    # The demo halves camera frames and divides boxes by eight. Therefore the
    # SDK box coordinates are four times camera-frame coordinates. This mapping
    # remains marked in the packet until confirmed on the attached device.
    scale_x = preview_width / (source_width * 4)
    scale_y = preview_height / (source_height * 4)
    left = round(bbox[0] * scale_x)
    top = round(bbox[1] * scale_y)
    right = round((bbox[0] + bbox[2]) * scale_x)
    bottom = round((bbox[1] + bbox[3]) * scale_y)
    if right <= 0 or bottom <= 0 or left >= preview_width or top >= preview_height:
        return None
    left, top = max(0, left), max(0, top)
    right, bottom = min(preview_width, right), min(preview_height, bottom)
    return [left, top, right - left, bottom - top] if right > left and bottom > top else None


def gaze_packet(value: Output) -> dict[str, Any]:
    """Use SDK validity; never infer gaze from a pupil ellipse or clamp offscreen gaze."""
    left, right = bool(value.left_valid), bool(value.right_valid)
    point = None
    if value.merge_valid and (left or right):
        point = (float(value.merge.x), float(value.merge.y))
    elif left and right:
        point = ((value.left.x + value.right.x) / 2, (value.left.y + value.right.y) / 2)
    elif left:
        point = (float(value.left.x), float(value.left.y))
    elif right:
        point = (float(value.right.x), float(value.right.y))
    valid = point is not None and all(math.isfinite(v) and 0 <= v <= 1 for v in point)
    return {
        "event": "sample",
        "received_ns": monotonic_ns(),
        "x": point[0] if valid and point is not None else None,
        "y": point[1] if valid and point is not None else None,
        "left_valid": left and valid,
        "right_valid": right and valid,
    }


def profile_key(config: dict[str, Any], identity: dict[str, Any], device_uid: str) -> str:
    # SDK paths, authorization files and process launch options do not change the
    # screen mapping. Keep a successful calibration usable after an app restart or
    # a renewed license, while invalidating it when the mounting geometry changes.
    mapping = {
        "resolution": config.get("resolution"),
        "screen_physical": config.get("screen_physical"),
    }
    value = ["thinks-v2.3-2", mapping, identity, device_uid]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class NativeTracker:
    def __init__(self, config: dict[str, Any], runtime: Path) -> None:
        self.config, self.runtime = config, runtime
        self.handle: int | None = None
        self.streaming = False
        self.frames = 0
        self.samples: Queue[dict[str, Any]] = Queue(maxsize=64)
        self.last_callback = monotonic()
        self.point_finished = threading.Event()
        self.finished_count = 0
        self.preview_enabled = False
        self.preview_stop = threading.Event()
        self.preview_frames: Queue[tuple[bytes, int, int]] = Queue(maxsize=1)
        self.preview_thread: threading.Thread | None = None
        self.last_preview_capture = 0.0
        self.eye_lock = threading.Lock()
        self.eye_detection_state = "off"
        self.eye_boxes: tuple[
            tuple[int, int, int, int] | None, tuple[int, int, int, int] | None
        ] = (
            None,
            None,
        )
        self.eye_boxes_at = 0.0
        self.eye_thread: threading.Thread | None = None
        self.eye_detection_status: int | None = None
        self.physical = Physical(*config["screen_physical"])
        dll_path = Path(config["dll_path"]).resolve()
        if sys.platform != "win32" or c.sizeof(c.c_void_p) != 8:
            raise RuntimeError("ThinksTracker SDK 需要 Windows 64 位 Python。")
        self.cookie = os.add_dll_directory(str(dll_path.parent))
        try:
            self.dll = c.CDLL(str(dll_path))
            self._bind()
        except (OSError, AttributeError) as error:
            self.cookie.close()
            raise RuntimeError(f"ThinksTracker DLL、依赖或必需接口不可用：{error}") from error
        self.track_cb = TRACK_CB(self._on_gaze)
        self.camera_cb = CAMERA_CB(self._on_camera)
        self.finish_cb = FINISH_CB(self._on_point_finished)
        self.eye_detect_cb = EYE_DETECT_CB(self._on_eye_detect)

    def _bind(self) -> None:
        signatures: dict[str, tuple[list[Any], Any]] = {
            "getSystemDevicesList": (
                [c.POINTER(Device), c.c_uint32, c.POINTER(c.c_uint32)],
                c.c_int,
            ),
            "createDevice": ([c.POINTER(Device)], c.c_void_p),
            "ThinksTracker_init": (
                [
                    c.c_void_p,
                    c.c_int,
                    c.c_char_p,
                    c.c_int,
                    Physical,
                    c.c_char_p,
                    c.c_int,
                    c.c_int,
                    c.c_int,
                ],
                c.c_int32,
            ),
            "ThinksTracker_startCamera": ([c.c_void_p], c.c_int32),
            "ThinksTracker_stopCamera": ([c.c_void_p], None),
            "ThinksTracker_startEyeDetect": ([c.c_void_p], c.c_int32),
            "ThinksTracker_stopEyeDetect": ([c.c_void_p], c.c_int32),
            "ThinksTracker_setIRLight": ([c.c_void_p, c.c_bool, c.c_int, c.c_int], None),
            "ThinksTracker_setVcmCode": ([c.c_void_p, c.c_int32], c.c_int32),
            "ThinksTracker_Deinit": ([c.c_void_p], None),
            "ThinksTracker_checkTrackerLicense": ([c.c_void_p, c.c_char_p], c.c_int32),
            "ThinksTracker_reConfig": (
                [c.c_void_p, c.c_int, c.c_int, c.c_int, Physical],
                c.c_int32,
            ),
            "ThinksTracker_getCurrStreamInfos": ([c.c_void_p, c.POINTER(Stream)], None),
            "ThinksTracker_registerTrackerOutputCb": ([c.c_void_p, TRACK_CB, c.c_void_p], None),
            "ThinksTracker_registerEyeCameraCb": ([c.c_void_p, CAMERA_CB, c.c_void_p], None),
            "ThinksTracker_registerEyeDetectCb": (
                [c.c_void_p, EYE_DETECT_CB, c.c_void_p],
                None,
            ),
            "ThinksTracker_registerCalibrationOnePointFinishCb": (
                [c.c_void_p, FINISH_CB, c.c_void_p],
                None,
            ),
            "ThinksTracker_startTracker": ([c.c_void_p, c.c_uint32], c.c_int32),
            "ThinksTracker_stopTracker": ([c.c_void_p], c.c_int32),
            "ThinksTracker_LoadCalibrationData": ([c.c_void_p, c.c_char_p], c.c_int32),
            "ThinksTracker_SaveCalibrationData": ([c.c_void_p, c.c_char_p], c.c_int32),
            "ThinksTracker_startCalibration": (
                [c.c_void_p, c.POINTER(Point), c.c_uint32, c.c_int, c.c_char_p],
                c.c_int32,
            ),
            "ThinksTracker_checkCalibrationFinished": ([c.c_void_p], c.c_int32),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, result

    @staticmethod
    def check(status: int, operation: str) -> None:
        if status != 0:
            detail = "；厂商授权未通过" if status == -9 or operation == "授权检查" else ""
            raise RuntimeError(f"ThinksTracker {operation}失败（{status}）{detail}。")

    @staticmethod
    def path_bytes(path: Path) -> bytes:
        return str(path).encode("mbcs")

    def open(self) -> dict[str, Any]:
        devices = (Device * 16)()
        count = c.c_uint32()
        self.check(self.dll.getSystemDevicesList(devices, 16, c.byref(count)), "设备枚举")
        if not 0 < count.value <= 16:
            raise RuntimeError(
                "未发现 ThinksTracker 设备，请检查 USB 连接及设备是否被其他程序占用。"
            )
        if count.value != 1:
            raise RuntimeError("发现多台 ThinksTracker，请只连接本次使用的一台。")
        device = devices[0]
        self.device_uid = bytes(device.uid).decode(errors="replace")
        self.handle = self.dll.createDevice(c.byref(device))
        if not self.handle:
            raise RuntimeError("ThinksTracker 创建设备失败，可能被其他程序占用。")
        self.check(
            self.dll.ThinksTracker_init(
                self.handle,
                0,
                self.config["resolution"].encode(),
                9,
                self.physical,
                self.path_bytes(self.runtime / "sdk.log"),
                1,
                640,
                480,
            ),
            "初始化",
        )
        license_path = Path(
            self.config.get("license_file")
            or Path(self.config["dll_path"]).parent / "license_ThinksTracker.lic"
        )
        # Some devices authorize from firmware. Always call the official check, even
        # if no external file exists. A failing check must prevent tracking.
        self.check(
            self.dll.ThinksTracker_checkTrackerLicense(self.handle, self.path_bytes(license_path)),
            "授权检查",
        )
        self.dll.ThinksTracker_registerEyeCameraCb(self.handle, self.camera_cb, None)
        self.dll.ThinksTracker_registerEyeDetectCb(self.handle, self.eye_detect_cb, None)
        self.dll.ThinksTracker_registerTrackerOutputCb(self.handle, self.track_cb, None)
        self.dll.ThinksTracker_registerCalibrationOnePointFinishCb(
            self.handle, self.finish_cb, None
        )
        # Match the vendor demo's automatic IR mode; brightness values are its
        # defaults and only used by the DLL if it chooses to apply them.
        self.dll.ThinksTracker_setIRLight(self.handle, False, 80, 80)
        self.check(self.dll.ThinksTracker_startCamera(self.handle), "开启相机")
        # The attached 120 Hz device returns -1 here although camera operation
        # succeeds. The vendor demo also treats this focus hint as optional.
        self.dll.ThinksTracker_setVcmCode(self.handle, 0)
        stream = Stream()
        self.dll.ThinksTracker_getCurrStreamInfos(self.handle, c.byref(stream))
        self.fps = int(stream.fps)
        return {
            "event": "ready",
            "name": device.name.decode(errors="replace"),
            "vid": device.vid,
            "pid": device.pid,
            "width": stream.width,
            "height": stream.height,
            "fps": stream.fps,
        }

    def _on_camera(self, _user: Any, data: Any, width: int, height: int, length: int) -> None:
        self.frames += 1
        now = monotonic()
        if not self.preview_enabled or now - self.last_preview_capture < PREVIEW_INTERVAL_SECONDS:
            return
        pixels = width * height
        if not data or not 0 < pixels <= 32 * 1024 * 1024 or length < pixels:
            return
        self.last_preview_capture = now
        # The SDK demo reads exactly w*h bytes as CV_8UC1. Copy only after the
        # rate gate: the DLL owns this pointer only during the callback. Frames
        # stay in volatile memory and the bounded queue always keeps the latest.
        frame = (c.string_at(data, pixels), width, height)
        try:
            self.preview_frames.put_nowait(frame)
        except Full:
            with suppress(Empty):
                self.preview_frames.get_nowait()
            with suppress(Full):
                self.preview_frames.put_nowait(frame)

    def _on_eye_detect(self, _user: Any, left: Any, right: Any) -> None:
        def values(pointer: Any) -> tuple[int, int, int, int] | None:
            if not pointer:
                return None
            box = pointer.contents
            if box.width <= 0 or box.height <= 0:
                return None
            return (box.left, box.top, box.width, box.height)

        with self.eye_lock:
            if self.eye_detection_state == "running":
                self.eye_boxes = (values(left), values(right))
                self.eye_boxes_at = monotonic()

    def _preview_loop(self) -> None:
        while not self.preview_stop.is_set():
            try:
                raw, width, height = self.preview_frames.get(timeout=0.25)
            except Empty:
                continue
            if not self.preview_enabled:
                continue
            pixels, preview_width, preview_height = downsample_gray(raw, width, height)
            with self.eye_lock:
                state = self.eye_detection_state
                fresh = state == "running" and monotonic() - self.eye_boxes_at < 0.5
                boxes = self.eye_boxes if fresh else (None, None)
            packet = {
                "event": "preview",
                "image_base64": base64.b64encode(zlib.compress(pixels, 1)).decode("ascii"),
                "width": preview_width,
                "height": preview_height,
                "left_bbox": preview_bbox(boxes[0], width, height, preview_width, preview_height),
                "right_bbox": preview_bbox(boxes[1], width, height, preview_width, preview_height),
                "eye_detection": state,
                "bbox_mapping": "vendor_demo_div4",
            }
            try:
                emit(packet)
            except (BrokenPipeError, OSError):
                self.preview_stop.set()
                return

    def _run_eye_detection(self) -> None:
        try:
            status = int(self.dll.ThinksTracker_startEyeDetect(self.handle))
        except Exception:
            status = -1
        with self.eye_lock:
            self.eye_detection_status = status
            failed_while_active = status != 0 and self.eye_detection_state == "running"
            if failed_while_active:
                self.eye_detection_state = "off"
                self.eye_boxes = (None, None)
        if failed_while_active:
            emit({"event": "eye_detection_unavailable", "status": status})

    def start_preview(self) -> None:
        self.preview_enabled = True
        self.preview_stop.clear()
        self.preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
        self.preview_thread.start()
        self.resume_eye_detection()

    def pause_eye_detection(self, *, notify: bool = True) -> None:
        with self.eye_lock:
            running = self.eye_detection_state == "running"
            self.eye_detection_state = "paused"
            self.eye_boxes = (None, None)
            self.eye_boxes_at = 0.0
        if notify:
            emit({"event": "eye_detection_paused"})
        if running:
            self.dll.ThinksTracker_stopEyeDetect(self.handle)
            if self.eye_thread is not None:
                self.eye_thread.join(timeout=2.0)
                if self.eye_thread.is_alive():
                    raise RuntimeError("SDK 眼睛检测未停止，暂不能安全开始校准点采集。")
        self.eye_thread = None

    def resume_eye_detection(self) -> None:
        if self.eye_thread is not None and self.eye_thread.is_alive():
            raise RuntimeError("SDK 眼睛检测线程仍在运行。")
        with self.eye_lock:
            self.eye_detection_state = "running"
            self.eye_boxes = (None, None)
            self.eye_boxes_at = 0.0
            self.eye_detection_status = None
        self.eye_thread = threading.Thread(target=self._run_eye_detection, daemon=True)
        emit({"event": "eye_detection_resumed"})
        self.eye_thread.start()

    def _on_point_finished(self, _user: Any, count: int) -> None:
        self.finished_count = count
        self.point_finished.set()

    def begin_calibration(self) -> None:
        # The vendor sample reconfigures the nine-point run after starting the
        # camera and before collecting its first point.
        self.check(
            self.dll.ThinksTracker_reConfig(self.handle, 0, 9, 9, self.physical),
            "配置校准",
        )

    def calibrate_point(self, x: float, y: float, expected_count: int) -> None:
        self.point_finished.clear()
        point = Point(x, y)
        self.check(
            # The vendor demo supplies a real path even with saveImg=0. Passing
            # NULL makes this DLL dereference address zero during calibration.
            self.dll.ThinksTracker_startCalibration(self.handle, c.byref(point), 5, 0, b"./"),
            "采集校准点",
        )
        if not self.point_finished.wait(6) or self.finished_count != expected_count:
            raise RuntimeError("SDK 未确认当前校准点完成，请重新校准。")

    def _on_gaze(self, _user: Any, output: Any) -> None:
        if not output or not self.streaming:
            return
        self.last_callback = monotonic()
        packet = gaze_packet(output.contents)
        try:
            self.samples.put_nowait(packet)
        except Full:
            with suppress(Empty):
                self.samples.get_nowait()
            with suppress(Full):
                self.samples.put_nowait(packet)

    def start(self, calibration: Path) -> None:
        if not calibration.is_file():
            raise RuntimeError("当前受试者、设备和屏幕尚未完成 ThinksTracker 校准。")
        self.check(
            self.dll.ThinksTracker_LoadCalibrationData(self.handle, self.path_bytes(calibration)),
            "加载校准",
        )
        self.streaming = True
        self.last_callback = monotonic()
        self.check(
            self.dll.ThinksTracker_startTracker(
                self.handle, 30 if self.fps > 120 else 12 if self.fps > 60 else 6
            ),
            "开始追踪",
        )

    def close(self) -> None:
        self.preview_enabled = False
        self.preview_stop.set()
        eye_stopped = True
        if self.handle:
            try:
                self.pause_eye_detection(notify=False)
            except RuntimeError:
                eye_stopped = False
            if self.streaming:
                self.streaming = False
                self.dll.ThinksTracker_stopTracker(self.handle)
            if eye_stopped:
                self.dll.ThinksTracker_stopCamera(self.handle)
                self.dll.ThinksTracker_Deinit(self.handle)
            self.handle = None
        if self.preview_thread is not None:
            self.preview_thread.join(timeout=1.0)
        self.cookie.close()


def emit(packet: dict[str, Any]) -> None:
    # A vendor printf may write to stdout; a newline and prefix distinguish our records.
    with EMIT_LOCK:
        print("\n" + PREFIX + json.dumps(packet, ensure_ascii=True, allow_nan=False), flush=True)


def main() -> int:
    tracker = None
    try:
        request = json.loads(sys.stdin.readline())
        runtime = Path(request["runtime"])
        runtime.mkdir(parents=True, exist_ok=True)
        tracker = NativeTracker(request["config"], runtime)
        ready = tracker.open()
        identity = request["identity"]
        key = profile_key(request["config"], identity, tracker.device_uid)
        calibration = runtime / "calibration" / f"{key}.dat"
        mode = request.get("mode", "track")
        if mode != "probe" and (not identity.get("patient_id") or not identity.get("screen")):
            raise RuntimeError("请先选择受试者和患者显示屏。")
        if mode == "calibrate":
            tracker.begin_calibration()
        if mode == "track":
            tracker.start(calibration)
        emit(ready)
        if mode == "calibrate":
            tracker.start_preview()
        commands: Queue[dict[str, Any]] = Queue()

        def read_commands() -> None:
            try:
                for line in sys.stdin:
                    commands.put(json.loads(line))
            finally:
                commands.put({"command": "close"})

        threading.Thread(target=read_commands, daemon=True).start()
        points = 0
        while True:
            try:
                command = commands.get(timeout=0.002)
            except Empty:
                command = {}
            action = command.get("command")
            if action == "close":
                break
            if action == "point" and mode == "calibrate":
                x, y = command["point"]
                if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)) or points >= 9:
                    raise RuntimeError("无效的校准点。")
                tracker.pause_eye_detection()
                tracker.calibrate_point(x, y, points + 1)
                tracker.resume_eye_detection()
                points += 1
                emit({"event": "point_done", "count": points})
            if action == "save" and mode == "calibrate":
                if points != 9:
                    raise RuntimeError("九点校准未完成。")
                tracker.check(
                    tracker.dll.ThinksTracker_checkCalibrationFinished(tracker.handle), "完成校准"
                )
                calibration.parent.mkdir(parents=True, exist_ok=True)
                temporary = calibration.with_suffix(".pending.dat")
                tracker.check(
                    tracker.dll.ThinksTracker_SaveCalibrationData(
                        tracker.handle, tracker.path_bytes(temporary)
                    ),
                    "保存校准",
                )
                if not temporary.is_file() or temporary.stat().st_size == 0:
                    raise RuntimeError("SDK 未生成校准文件。")
                temporary.replace(calibration)
                emit({"event": "saved"})
            if action == "status":
                emit({"event": "status", "frames": tracker.frames})
            for _ in range(64):
                try:
                    emit(tracker.samples.get_nowait())
                except Empty:
                    break
            if tracker.streaming and monotonic() - tracker.last_callback > 3:
                raise RuntimeError("ThinksTracker 连续 3 秒未返回追踪数据，请检查连接和眼部位置。")
        return 0
    except Exception as error:
        emit({"event": "error", "message": str(error)})
        return 1
    finally:
        if tracker is not None:
            tracker.close()


if __name__ == "__main__":
    raise SystemExit(main())
