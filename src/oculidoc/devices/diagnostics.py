"""System and camera hardware diagnostic services."""

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

import cv2
import psutil

from oculidoc.devices.models import (
    CameraProbeResult,
    DeviceDiagnosticReport,
    ProbeStatus,
    SystemSnapshot,
)


class CaptureProtocol(Protocol):
    """Minimal OpenCV capture interface used by diagnostics."""

    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, Any]: ...

    def get(self, property_id: int) -> float: ...

    def getBackendName(self) -> str: ...

    def release(self) -> None: ...


_OPENCV_LOG_LOCK = RLock()


def _opencv_logging_namespace():
    """Return an available OpenCV logging API namespace."""
    top_level_setter = getattr(
        cv2,
        "setLogLevel",
        None,
    )

    if callable(top_level_setter):
        return cv2

    utils_namespace = getattr(
        cv2,
        "utils",
        None,
    )
    logging_namespace = getattr(
        utils_namespace,
        "logging",
        None,
    )

    nested_setter = getattr(
        logging_namespace,
        "setLogLevel",
        None,
    )

    if callable(nested_setter):
        return logging_namespace

    return None


@contextmanager
def _opencv_error_only():
    """Temporarily suppress expected OpenCV warnings."""
    logging_namespace = _opencv_logging_namespace()

    if logging_namespace is None:
        yield
        return

    set_log_level = logging_namespace.setLogLevel
    get_log_level = getattr(
        logging_namespace,
        "getLogLevel",
        None,
    )

    error_level = getattr(
        logging_namespace,
        "LOG_LEVEL_ERROR",
        2,
    )
    warning_level = getattr(
        logging_namespace,
        "LOG_LEVEL_WARNING",
        3,
    )

    with _OPENCV_LOG_LOCK:
        previous_level = get_log_level() if callable(get_log_level) else warning_level

        try:
            set_log_level(error_level)
            yield
        finally:
            set_log_level(previous_level)


CaptureFactory = Callable[
    [int, int],
    CaptureProtocol,
]


def _positive_integer(value: float) -> int | None:
    if not isfinite(value) or value <= 0:
        return None

    return int(round(value))


def _positive_float(value: float) -> float | None:
    if not isfinite(value) or value <= 0:
        return None

    return float(value)


def collect_nvidia_gpu_names() -> tuple[str, ...]:
    """Return NVIDIA GPU names when nvidia-smi is available."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name",
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (
        FileNotFoundError,
        OSError,
        subprocess.TimeoutExpired,
    ):
        return ()

    if result.returncode != 0:
        return ()

    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def collect_system_snapshot(
    disk_path: str | Path | None = None,
) -> SystemSnapshot:
    """Collect system information relevant to acquisition."""
    memory = psutil.virtual_memory()
    target_path = Path(disk_path if disk_path is not None else Path.home())
    disk = shutil.disk_usage(target_path)

    logical_cpu_count = psutil.cpu_count(logical=True)
    physical_cpu_count = psutil.cpu_count(logical=False)

    if logical_cpu_count is None:
        logical_cpu_count = os.cpu_count() or 1

    processor = (
        platform.processor()
        or os.environ.get(
            "PROCESSOR_IDENTIFIER",
            "",
        )
        or "unknown"
    )

    return SystemSnapshot(
        hostname=socket.gethostname(),
        operating_system=platform.system(),
        operating_system_version=platform.version(),
        machine=platform.machine(),
        processor=processor,
        python_version=platform.python_version(),
        logical_cpu_count=logical_cpu_count,
        physical_cpu_count=physical_cpu_count,
        memory_total_bytes=int(memory.total),
        memory_available_bytes=int(memory.available),
        disk_free_bytes=int(disk.free),
        nvidia_gpu_names=collect_nvidia_gpu_names(),
    )


def default_camera_backend() -> int:
    """Return the preferred camera backend for this platform."""
    if sys.platform == "win32":
        return cv2.CAP_DSHOW

    return cv2.CAP_ANY


def probe_camera(
    index: int,
    *,
    backend: int | None = None,
    capture_factory: CaptureFactory | None = None,
) -> CameraProbeResult:
    """Probe one camera index and attempt to read one frame."""
    if index < 0:
        raise ValueError("Camera index cannot be negative.")

    selected_backend = default_camera_backend() if backend is None else backend
    factory = capture_factory or cv2.VideoCapture
    with _opencv_error_only():
        capture = factory(index, selected_backend)

    try:
        if not capture.isOpened():
            return CameraProbeResult(
                index=index,
                status=ProbeStatus.UNAVAILABLE,
                message=(
                    "Camera did not open. It may be absent, "
                    "busy, disabled, or blocked by privacy settings."
                ),
            )

        try:
            backend_name = capture.getBackendName()
        except (cv2.error, AttributeError):
            backend_name = None

        read_ok, frame = capture.read()
        frame_size = getattr(frame, "size", 0)
        frame_received = bool(read_ok and frame is not None and frame_size > 0)

        width_px = _positive_integer(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height_px = _positive_integer(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = _positive_float(capture.get(cv2.CAP_PROP_FPS))

        if not frame_received:
            return CameraProbeResult(
                index=index,
                status=ProbeStatus.ERROR,
                backend=backend_name,
                width_px=width_px,
                height_px=height_px,
                fps=fps,
                frame_received=False,
                message=("Camera opened but no valid frame was received."),
            )

        return CameraProbeResult(
            index=index,
            status=ProbeStatus.AVAILABLE,
            backend=backend_name,
            width_px=width_px,
            height_px=height_px,
            fps=fps,
            frame_received=True,
        )
    except Exception as error:
        return CameraProbeResult(
            index=index,
            status=ProbeStatus.ERROR,
            message=(f"{type(error).__name__}: {error}"),
        )
    finally:
        capture.release()


def probe_cameras(
    max_index: int = 3,
    *,
    backend: int | None = None,
    capture_factory: CaptureFactory | None = None,
) -> tuple[CameraProbeResult, ...]:
    """Probe camera indices zero through max_index."""
    if max_index < 0:
        raise ValueError("max_index cannot be negative.")

    return tuple(
        probe_camera(
            index,
            backend=backend,
            capture_factory=capture_factory,
        )
        for index in range(max_index + 1)
    )


def build_diagnostic_report(
    *,
    max_camera_index: int = 3,
    scan_cameras: bool = True,
    disk_path: str | Path | None = None,
) -> DeviceDiagnosticReport:
    """Collect one complete device diagnostic report."""
    cameras = probe_cameras(max_camera_index) if scan_cameras else ()

    return DeviceDiagnosticReport(
        system=collect_system_snapshot(disk_path),
        cameras=cameras,
    )


def write_diagnostic_report(
    report: DeviceDiagnosticReport,
    output_path: str | Path,
) -> Path:
    """Atomically write a diagnostic report as UTF-8 JSON."""
    path = Path(output_path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")

    try:
        temporary_path.write_text(
            json.dumps(
                report.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    return path
