"""Calibrated webcam gaze with isolated providers and sequential fail-closed probing."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from collections import Counter
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from queue import Empty, Full, Queue
from tempfile import NamedTemporaryFile
from time import monotonic, monotonic_ns
from typing import Any

import numpy as np

from oculidoc.devices.acies import (
    ACIES_VERSION,
    CALIBRATION_FEATURE_VERSION,
    COMPATIBLE_MODEL_VERSIONS,
    FEATURE_COUNT,
    MIN_SIGNAL_QUALITY,
    PROFILE_LABELS,
    GazeSmoother,
    MappingParameters,
    ObservationMonitor,
    fit_mapping,
    validate_observation_model,
    validation_passes,
)
from oculidoc.devices.auto_detect import AutoDetectEyeTrackerDevice
from oculidoc.devices.contracts import (
    DeviceInfo,
    DeviceKind,
    DeviceState,
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.devices.errors import DeviceConnectionError, InvalidDeviceStateError

PROVIDERS = ("acies",)
PROVIDER_LABELS = {"acies": "ACIES"}


def read_webcam_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or not isinstance(value.get("providers"), dict):
            raise ValueError("providers must be an object")
        camera = value.get("camera_index", 0)
        if type(camera) is not int or not 0 <= camera <= 16:
            raise ValueError("camera_index must be an integer from 0 to 16")
        for key, default, maximum in (("camera_width", 1280, 3840), ("camera_height", 720, 2160)):
            if (
                type(value.get(key, default)) is not int
                or not 160 <= value.get(key, default) <= maximum
            ):
                raise ValueError(f"{key} outside supported range")
        return value
    except (OSError, ValueError) as error:
        raise DeviceConnectionError(f"摄像头配置不可用：{path}；{error}") from error


def save_webcam_camera_index(path: Path, index: int) -> None:
    """Change only the selected camera; keep provider paths and other settings intact."""
    if type(index) is not int or not 0 <= index <= 16:
        raise ValueError("摄像头编号必须在 0 至 16 之间。")
    config = read_webcam_config(path)
    if config.get("camera_index", 0) == index:
        return
    config["camera_index"] = index
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def calibration_identity(
    config: dict[str, Any], provider: str, patient_id: str, screen: tuple[str, int, int]
) -> str:
    payload = [config, provider, patient_id, screen, CALIBRATION_FEATURE_VERSION]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class WebcamFeatureProcess:
    """Own one camera process; retain at most the latest feature packet."""

    def __init__(self, config_path: Path, provider: str, runtime_dir: Path) -> None:
        self.config_path = config_path.resolve()
        self.provider = provider
        self.runtime_dir = runtime_dir
        self.process: subprocess.Popen[str] | None = None
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=1)
        self._reader: threading.Thread | None = None
        self._lock_file: Any = None
        self._cancelled = threading.Event()
        self._sequence = -1
        self._timestamp = -1
        self._failure: str | None = None

    def start(self) -> None:
        config = read_webcam_config(self.config_path)
        provider = config["providers"].get(self.provider)
        if not isinstance(provider, dict) or not provider.get("python"):
            raise DeviceConnectionError(f"{PROVIDER_LABELS[self.provider]} 尚未安装或配置。")
        python = Path(provider["python"])
        if not python.is_absolute() or not python.is_file():
            raise DeviceConnectionError("摄像头算法需要已安装的独立 Python 绝对路径。")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.runtime_dir / f"camera-{config.get('camera_index', 0)}.lock"
        self._lock_file = lock_path.open("a+b")
        self._lock_file.seek(0)
        if lock_path.stat().st_size == 0:
            self._lock_file.write(b"0")
            self._lock_file.flush()
        self._lock_file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._lock_file.close()
            self._lock_file = None
            raise DeviceConnectionError("摄像头正在被另一个 OculiDoC 任务或校准占用。") from error
        try:
            if self._cancelled.is_set():
                raise DeviceConnectionError("摄像头探测已取消。")
            self.process = subprocess.Popen(
                [
                    str(python),
                    "-u",
                    str(Path(__file__).with_name("webcam_worker.py")),
                    "--provider",
                    self.provider,
                    "--config",
                    str(self.config_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self.runtime_dir,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
            self._reader = threading.Thread(target=self._read_stdout, daemon=True)
            self._reader.start()
        except Exception:
            self.close()
            raise

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while line := self.process.stdout.readline(131072):
                packet = json.loads(line)
                if not isinstance(packet, dict):
                    raise ValueError("Invalid webcam packet")
                if "error" in packet:
                    self._failure = str(packet["error"])
                    return
                try:
                    self._queue.put_nowait(packet)
                except Full:
                    try:
                        self._queue.get_nowait()
                    except Empty:
                        pass
                    self._queue.put_nowait(packet)
        except (OSError, ValueError, Full) as error:
            self._failure = f"摄像头协议错误：{error}"
        finally:
            if not self._cancelled.is_set() and self._failure is None:
                self._failure = "摄像头算法进程已退出。"

    def read(self) -> dict[str, Any]:
        if self._cancelled.is_set():
            raise DeviceConnectionError("摄像头采集已取消。")
        if self._failure:
            raise DeviceConnectionError(self._failure)
        try:
            packet = self._queue.get(timeout=0.15)
        except Empty as error:
            raise TimeoutError("等待摄像头帧") from error
        sequence, timestamp = packet.get("sequence"), packet.get("timestamp_ns")
        if type(sequence) is not int or type(timestamp) is not int:
            raise DeviceConnectionError("摄像头帧缺少有效序号或时间戳。")
        age = (monotonic_ns() - timestamp) / 1e9
        if sequence <= self._sequence or timestamp < self._timestamp or not 0 <= age <= 1.0:
            raise DeviceConnectionError("摄像头输出重复、过期或乱序。")
        self._sequence, self._timestamp = sequence, timestamp
        features = packet.get("features")
        if features is not None:
            array = np.asarray(features, dtype=float)
            if array.ndim != 1 or not 1 <= len(array) <= 2048 or not np.isfinite(array).all():
                raise DeviceConnectionError("摄像头特征格式无效。")
        confidence = packet.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise DeviceConnectionError("ACIES 质量分数缺失或无效。")
        if (
            not np.isfinite(confidence)
            or not 0 <= confidence <= 1
            or packet.get("algorithm_version") != ACIES_VERSION
        ):
            raise DeviceConnectionError("ACIES 质量分数或算法版本无效。")
        if features is not None and (
            len(features) != FEATURE_COUNT or confidence < MIN_SIGNAL_QUALITY
        ):
            raise DeviceConnectionError("ACIES 特征格式或图像信息不足。")
        if packet.get("mode") not in {"binocular", "left_eye", "right_eye", "unavailable"}:
            raise DeviceConnectionError("ACIES 眼部来源无效。")
        expected = {
            "binocular": (True, True),
            "left_eye": (True, False),
            "right_eye": (False, True),
            "unavailable": (False, False),
        }[packet["mode"]]
        if (packet.get("left_eye_valid"), packet.get("right_eye_valid")) != expected:
            raise DeviceConnectionError("ACIES 眼部来源不一致。")
        if (features is None) != (packet["mode"] == "unavailable"):
            raise DeviceConnectionError("ACIES 特征与可用状态不一致。")
        if "eye_features" in packet:
            eye_features = packet["eye_features"]
            if not isinstance(eye_features, dict) or set(eye_features) != {"left", "right"}:
                raise DeviceConnectionError("ACIES 分眼特征格式无效。")
            for name in ("left", "right"):
                row = eye_features[name]
                if (row is not None) != packet[f"{name}_eye_valid"]:
                    raise DeviceConnectionError("ACIES 分眼特征与可用状态不一致。")
                if row is not None:
                    array = np.asarray(row, dtype=float)
                    if array.shape != (FEATURE_COUNT,) or not np.isfinite(array).all():
                        raise DeviceConnectionError("ACIES 分眼特征数值无效。")
        preview = packet.get("preview")
        if features is not None and (
            not isinstance(preview, list)
            or len(preview) != 2
            or any(
                not isinstance(v, (int, float)) or not np.isfinite(v) or not 0 <= v <= 1
                for v in preview
            )
        ):
            raise DeviceConnectionError("ACIES 未校准预览格式无效。")
        if packet.get("screen_focus") != "unknown":
            raise DeviceConnectionError("ACIES 不提供已聚焦或失焦的诊断。")
        diagnostics = packet.get("eye_diagnostics")
        if not isinstance(diagnostics, dict) or set(diagnostics) - {"left", "right"}:
            raise DeviceConnectionError("ACIES 眼区质量信息无效。")
        for eye in diagnostics.values():
            if not isinstance(eye, dict):
                raise DeviceConnectionError("ACIES 单眼质量信息无效。")
            for key in (
                "width_px",
                "openness",
                "glare_ratio",
                "contrast",
                "image_detail",
                "quality",
            ):
                value = eye.get(key, 0)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (float, int))
                    or not np.isfinite(value)
                    or value < 0
                    or (key in {"glare_ratio", "quality"} and value > 1)
                ):
                    raise DeviceConnectionError("ACIES 眼区质量数值无效。")
        return packet

    def interrupt(self) -> None:
        self._cancelled.set()

    def close(self) -> None:
        self._cancelled.set()
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1.0)
            if self._reader is not None:
                self._reader.join(timeout=0.5)
            if self.process.stdout is not None:
                self.process.stdout.close()
            self.process = None
        # Never release the camera lease before the previous owner has exited.
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None


def fit_calibration(
    features: list[list[float]],
    targets: list[tuple[float, float]],
    qualities: list[float] | None = None,
    *,
    minimum_points: int = 9,
    eye_features: list[dict[str, Any]] | None = None,
    mapping_parameters: MappingParameters | None = None,
) -> dict[str, Any]:
    model = fit_mapping(
        features, targets, qualities, minimum_points=minimum_points, parameters=mapping_parameters
    )
    if eye_features is None:
        return model
    if len(eye_features) != len(features):
        raise ValueError("分眼校准样本与目标不匹配。")
    eyes = {}
    for name in ("left", "right"):
        indices = [i for i, item in enumerate(eye_features) if item.get(name) is not None]
        counts = Counter(targets[i] for i in indices)
        if set(counts) != set(targets) or min(counts.values(), default=0) < 5:
            continue
        child = fit_mapping(
            [eye_features[i][name] for i in indices],
            [targets[i] for i in indices],
            [qualities[i] for i in indices] if qualities is not None else None,
            minimum_points=minimum_points,
            parameters=mapping_parameters,
        )
        eyes[name] = child
    if eyes:
        model["eye_models"] = eyes
        model["fusion_method"] = "equal screen-space mean of available calibrated eyes"
        model["pose_required"] = all(child.get("pose_required", False) for child in eyes.values())
    return model


def predict_calibration(model: dict[str, Any], features: list[float]) -> tuple[float, float]:
    x = np.asarray(features, dtype=float)
    mean, scale, weights = (
        np.asarray(model[key], dtype=float) for key in ("mean", "scale", "weights")
    )
    if (
        x.ndim != 1
        or mean.shape != x.shape
        or scale.shape != x.shape
        or weights.shape != (len(x) + 1, 2)
        or np.any(scale <= 0)
        or not all(np.isfinite(a).all() for a in (x, mean, scale, weights))
    ):
        raise ValueError("摄像头特征与校准模型不匹配。")
    point = np.append((x - mean) / scale, 1.0) @ weights
    return float(point[0]), float(point[1])


def predict_packet(model: dict[str, Any], packet: dict[str, Any]) -> tuple[float, float] | None:
    """Map observed eyes separately; never substitute a missing eye or a target.

    Fixed binocular weights prevent glare-driven quality fluctuations from
    moving the origin. Quality still gates visibility upstream. Legacy models
    keep their original feature semantics and their original validation record.
    """
    if "eye_models" not in model:
        features = packet.get("features")
        return predict_calibration(model, features) if features is not None else None
    points = []
    monocular_allowed = []
    for name, child in model["eye_models"].items():
        features = packet.get("eye_features", {}).get(name)
        if features is not None and packet.get(f"{name}_eye_valid"):
            if child.get("pose_required") and features[15] != 1:
                continue
            points.append(predict_calibration(child, features))
            monocular_allowed.append(
                child.get("validated") is not False
                and (not model.get("validated") or child.get("validated") is True)
            )
    if not points:
        return None
    if len(points) == 1 and not monocular_allowed[0]:
        return None
    point = np.mean(points, axis=0)
    return float(point[0]), float(point[1])


def load_calibration(runtime_dir: Path, identity: str, profile: str) -> dict[str, Any]:
    """Share the same validity policy between saved preview and task acquisition."""
    path = runtime_dir / "calibration" / f"{identity[:24]}.json"
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
        age = (datetime.now(UTC) - datetime.fromisoformat(model["created_at"])).total_seconds()
        if (
            model["identity"] != identity
            or model["validated"] is not True
            or not 0 <= age < 8 * 3600
            or model.get("algorithm_version") not in COMPATIBLE_MODEL_VERSIONS
            or model.get("feature_version", CALIBRATION_FEATURE_VERSION)
            != CALIBRATION_FEATURE_VERSION
            or not validation_passes(
                model["validation_error"],
                model["validation_max_error"],
                model["calibration_profile"],
            )
            or not validation_passes(
                model["validation_error"], model["validation_max_error"], profile
            )
            or len(model["mean"]) != FEATURE_COUNT
        ):
            raise ValueError("校准已失效或验证误差过大")
        predict_calibration(model, model["mean"])
        validate_observation_model(model)
        if "eye_models" in model:
            eyes = model["eye_models"]
            if not isinstance(eyes, dict) or not eyes or set(eyes) - {"left", "right"}:
                raise ValueError("Invalid per-eye mappings")
            for child in eyes.values():
                if not isinstance(child, dict) or len(child["mean"]) != FEATURE_COUNT:
                    raise ValueError("Invalid per-eye mapping")
                predict_calibration(child, child["mean"])
                validate_observation_model(child)
                if type(child.get("validated")) is not bool:
                    raise ValueError("Missing per-eye validation")
                if child["validated"]:
                    if not validation_passes(
                        child["validation_error"],
                        child["validation_max_error"],
                        model["calibration_profile"],
                    ):
                        raise ValueError("Invalid per-eye validation")
                    if not validation_passes(
                        child["validation_error"],
                        child["validation_max_error"],
                        profile,
                    ):
                        child["validated"] = False
        return model
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise DeviceConnectionError("ACIES 需要为当前受试者、摄像头和屏幕重新校准。") from error


class WebcamEyeTrackerDevice:
    def __init__(
        self,
        config_path: Path,
        provider: str,
        runtime_dir: Path,
        patient_id: str,
        screen: tuple[str, int, int],
        calibration_profile: str = "balanced",
    ) -> None:
        self.config_path, self.provider, self.runtime_dir = config_path, provider, runtime_dir
        self.patient_id, self.screen = patient_id, screen
        self.calibration_profile = calibration_profile
        self._smoother = GazeSmoother()
        self._monitor = ObservationMonitor()
        self._state = DeviceState.DISCONNECTED
        self._process = WebcamFeatureProcess(config_path, provider, runtime_dir)
        self._model: dict[str, Any] = {}
        self._last_frame = 0.0
        self._last_valid = 0.0
        self._info = DeviceInfo(
            device_id=f"webcam-{provider}",
            kind=DeviceKind.EYE_TRACKER,
            name=f"摄像头 · {PROVIDER_LABELS[provider]}（研究估计）",
            manufacturer="OculiDoC",
            model=f"ACIES {ACIES_VERSION}",
            capabilities=(
                "gaze_point",
                "webcam_estimate",
                "calibration_required",
                "signal_quality",
            ),
        )

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def info(self) -> DeviceInfo:
        return self._info

    def connect(self) -> None:
        if self.state is not DeviceState.DISCONNECTED:
            raise InvalidDeviceStateError("Webcam is already connected.")
        if not self.patient_id or not self.screen[0]:
            raise DeviceConnectionError("请先选择受试者和患者屏幕，再进行摄像头校准。")
        config = read_webcam_config(self.config_path)
        identity = calibration_identity(config, self.provider, self.patient_id, self.screen)
        self._model = load_calibration(self.runtime_dir, identity, self.calibration_profile)
        self._process.start()
        self._state = DeviceState.CONNECTED

    def start_stream(self) -> None:
        if self.state is not DeviceState.CONNECTED:
            raise InvalidDeviceStateError("Connect webcam before streaming.")
        self._last_frame = self._last_valid = monotonic()
        self._smoother.reset()
        self._monitor.reset()
        self._state = DeviceState.STREAMING

    def read_sample(self) -> EyeTrackerSample:
        if self.state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("Webcam is not streaming.")
        try:
            packet = self._process.read()
        except TimeoutError:
            if monotonic() - self._last_frame > 20:
                raise DeviceConnectionError("摄像头算法长时间未输出帧。") from None
            raise
        self._last_frame = monotonic()
        point = None
        observation_status = self._monitor.update(packet, self._model)
        quality = packet["confidence"]
        if observation_status in {
            "binocular_changed",
            "image_detail_low",
            "reflection",
            "pose_outside_calibration",
        }:
            quality *= 0.65
        if packet["features"] is not None and observation_status != "pose_unavailable":
            candidate = predict_packet(self._model, packet)
            if candidate is None:
                observation_status = "eye_mapping_unavailable"
            if candidate is not None and all(np.isfinite(v) and 0 <= v <= 1 for v in candidate):
                point = self._smoother.update(candidate, packet["timestamp_ns"], quality)
                self._last_valid = monotonic()
        if point is None:
            self._smoother.reset()
        if monotonic() - self._last_valid > 10:
            raise DeviceConnectionError("摄像头连续十秒没有有效注视点，请检查姿势并重新校准。")
        return EyeTrackerSample(
            timestamp=DeviceTimestamp(
                packet["sequence"], packet["timestamp_ns"], datetime.now(UTC)
            ),
            gaze_x_normalized=point[0] if point else None,
            gaze_y_normalized=point[1] if point else None,
            left_eye_valid=point is not None and packet["left_eye_valid"],
            right_eye_valid=point is not None and packet["right_eye_valid"],
            gaze_confidence=quality,
            gaze_estimation_mode=packet["mode"],
            gaze_observation_status=observation_status,
            screen_focus_status="unknown",
        )

    def capability_diagnostics(self) -> tuple[str, ...]:
        return (
            f"ACIES {ACIES_VERSION} · {PROFILE_LABELS[self.calibration_profile]}",
            f"个人映射版本 {self._model.get('algorithm_version')} · 因果时序防抖",
            f"独立验证平均 {self._model.get('validation_error', 0):.1%} / "
            f"最大 {self._model.get('validation_max_error', 0):.1%}（屏幕归一化距离）",
            "质量分数是图像信息指标；单眼/低信息估计不等于精确眼动测量。",
            "双眼状态变化与图像模糊分别监测；普通摄像头无法判定真实屏幕聚焦或注意力。",
        )

    def stop_stream(self) -> None:
        self._state = DeviceState.CONNECTED

    def disconnect(self) -> None:
        self._process.close()
        self._state = DeviceState.DISCONNECTED

    def interrupt(self) -> None:
        self._process.interrupt()


def create_webcam_tracker(
    config_path: Path,
    runtime_dir: Path,
    patient_id: str,
    screen: tuple[str, int, int],
    calibration_profile: str = "balanced",
) -> AutoDetectEyeTrackerDevice:
    return AutoDetectEyeTrackerDevice(
        tuple(
            partial(
                WebcamEyeTrackerDevice,
                config_path,
                provider,
                runtime_dir,
                patient_id,
                screen,
                calibration_profile,
            )
            for provider in PROVIDERS
        ),
        probe_timeout_seconds=25,
        sample_predicate=lambda sample: sample.gaze_valid,
        required_sample_description="连续有效摄像头注视点",
        required_consecutive_samples=5,
        failure_message="ACIES 摄像头不可用，未使用其他算法或模拟数据。",
        strict_cleanup=True,
    )
