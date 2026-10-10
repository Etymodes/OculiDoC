"""ACIES saved/live preview, nine-point fitting and independent edge validation."""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, monotonic_ns
from typing import Any

import numpy as np
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from oculidoc.config import Settings
from oculidoc.devices.acies import (
    ACIES_VERSION,
    MIN_SIGNAL_QUALITY,
    OBSERVATION_LABELS,
    POSE_COLUMNS,
    PROFILE_LABELS,
    GazeSmoother,
    ObservationMonitor,
    validation_passes,
)
from oculidoc.devices.acies_learning import (
    choose_model,
    evaluate_models,
    fit_accumulated,
    save_training_round,
)
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.webcam_gaze import (
    PROVIDER_LABELS,
    WebcamFeatureProcess,
    calibration_identity,
    fit_calibration,
    load_calibration,
    predict_calibration,
    predict_packet,
    read_webcam_config,
)

# Cover both axes early so the provisional mapping is useful during calibration.
BASE_TRAIN_POINTS = (
    (0.5, 0.5),
    (0.05, 0.05),
    (0.95, 0.05),
    (0.95, 0.95),
    (0.05, 0.95),
    (0.05, 0.5),
    (0.5, 0.05),
    (0.95, 0.5),
    (0.5, 0.95),
)
POSE_TRAIN_POINTS = ((0.5, 0.5), (0.05, 0.5), (0.95, 0.5), (0.5, 0.05), (0.5, 0.95))
TRAIN_POINTS = BASE_TRAIN_POINTS + POSE_TRAIN_POINTS
POSE_PROMPTS = (
    "目光留在蓝点，舒适范围内轻微左右转头",
    "目光留在蓝点，舒适范围内轻微上下点头",
    "目光留在蓝点，自然调整一点左右位置",
    "目光留在蓝点，舒适范围内轻微点头",
    "目光留在蓝点，稍微调整一点前后距离",
)
# Different from training; cover edges so a small central range cannot pass easily.
VALIDATION_POINTS = (
    (0.08, 0.08),
    (0.92, 0.08),
    (0.08, 0.92),
    (0.92, 0.92),
    (0.06, 0.5),
    (0.94, 0.5),
    (0.5, 0.06),
    (0.5, 0.94),
    (0.5, 0.5),
)


class CalibrationCapture(QThread):
    packet = Signal(object)
    failed = Signal(str)

    def __init__(self, process: WebcamFeatureProcess, parent: QWidget) -> None:
        super().__init__(parent)
        self.process = process

    def run(self) -> None:
        try:
            self.process.start()
            last_frame = monotonic()
            while not self.isInterruptionRequested():
                try:
                    packet = self.process.read()
                except TimeoutError:
                    if monotonic() - last_frame > 25:
                        raise RuntimeError("摄像头算法未输出帧，请检查依赖和模型。") from None
                    continue
                last_frame = monotonic()
                self.packet.emit(packet)
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
        finally:
            self.process.close()


class WebcamCalibrationDialog(QDialog):
    def __init__(self, settings: Settings, provider: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings, self.provider = settings, provider
        self.setWindowTitle(f"{PROVIDER_LABELS[provider]} 摄像头校准")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._worker: CalibrationCapture | None = None
        self._index = 0
        self._started = 0.0
        self._features: list[list[float]] = []
        self._targets: list[tuple[float, float]] = []
        self._qualities: list[float] = []
        self._point_samples: list[list[float]] = []
        self._point_qualities: list[float] = []
        self._eye_features: list[dict[str, Any]] = []
        self._point_eye_features: list[dict[str, Any]] = []
        self._point_predictions: list[tuple[float, float]] = []
        self._point_eye_predictions: dict[str, list[tuple[float, float]]] = {}
        self._errors: list[float] = []
        self._validation_points: list[dict[str, Any]] = []
        self._training_points: list[dict[str, Any]] = []
        self._model: dict[str, Any] = {}
        self._preview_model: dict[str, Any] = {}
        self._message = "正在连接摄像头；自然坐姿即可。Esc 取消。"
        self._quality_text = "等待图像"
        self._ready = False
        self._armed = False
        self._closed = False
        self._reviewing = False
        self._validation_passed = False
        self._saved_preview = False
        self._attempt_path: Path | None = None
        self._attempt_started: str | None = None
        self._attempt_finished = False
        self._frame_size: list[int] = []
        self._preview: tuple[float, float] | None = None
        self._preview_calibrated = False
        self._last_packet = 0.0
        self._quality = 0.0
        self._eligible_count = 0
        self._smoother = GazeSmoother()
        self._monitor = ObservationMonitor()
        self._observation_state = "uncalibrated"
        self._point_states: Counter[str] = Counter()
        self._image_details: list[float] = []
        self._aggregates: list[dict[str, Any]] = []
        self._trace_buffer: list[dict[str, Any]] = []
        self._trace_frames = 0
        self._frozen_models: dict[str, dict[str, Any]] = {}
        self._incumbent_model: dict[str, Any] = {}
        self._validation_batches: list[dict[str, Any]] = []
        self._point_packets: list[dict[str, Any]] = []
        self._training_round_ids: list[str] = []
        self._learning_note = ""
        self.summary_text = ""
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        screen = next(
            (s for s in QApplication.screens() if s.name() == self.settings.patient_screen_name),
            None,
        )
        if not self.settings.patient_id or screen is None:
            QMessageBox.warning(self, "无法校准", "请先选择受试者，并连接已指定的患者显示屏。")
            return
        self._screen = (screen.name(), screen.geometry().width(), screen.geometry().height())
        self._config_path = self.settings.webcam_providers_path
        try:
            config = read_webcam_config(self._config_path)
            self._identity = calibration_identity(
                config, self.provider, self.settings.patient_id, self._screen
            )
            self._load_preview()
        except Exception as error:
            QMessageBox.warning(self, "无法校准", str(error))
            return
        self.setScreen(screen)
        self.move(screen.geometry().topLeft())
        self.showFullScreen()
        process = WebcamFeatureProcess(
            self._config_path, self.provider, self.settings.data_dir / "runtime" / "webcam"
        )
        self._worker = CalibrationCapture(process, self)
        self._worker.packet.connect(self._collect)
        self._worker.failed.connect(self._fail)
        self._worker.start()
        self._timer.start()
        self.exec()

    def _load_preview(self) -> None:
        try:
            self._preview_model = load_calibration(
                self.settings.data_dir / "runtime" / "webcam",
                self._identity,
                self.settings.webcam_calibration_profile,
            )
            self._saved_preview = True
        except DeviceConnectionError:
            self._preview_model = {}
            self._saved_preview = False

    def _begin(self) -> None:
        if self._armed or not self._ready or self._closed:
            return
        # A valid eye signal may predict off-screen with an old/misaligned map.
        # Still allow recalibration; requiring an in-screen reticle traps the user.
        if self._quality < MIN_SIGNAL_QUALITY or monotonic() - self._last_packet > 0.3:
            self._message = "还没有足够眼部信息；请调整光照、摄像头或坐姿，再按空格。"
            return
        try:
            # New attempts never remove the last usable model. Cancel/failure
            # must leave it available to the next launch and to this comparison.
            self._incumbent_model = deepcopy(self._model or self._preview_model)
            self._attempt_path = None
            self._attempt_started = None
            self._attempt_finished = False
            self._index = 0
            self._features, self._targets, self._qualities = [], [], []
            self._point_samples, self._point_qualities = [], []
            self._eye_features, self._point_eye_features, self._point_predictions = [], [], []
            self._point_eye_predictions = {}
            self._errors, self._validation_points, self._training_points = [], [], []
            self._preview_model = self._model or self._preview_model
            self._saved_preview = bool(self._preview_model)
            self._model = {}
            self._reviewing = False
            self._validation_passed = False
            self._eligible_count = 0
            self._smoother.reset()
            self._monitor.reset()
            self._point_states.clear()
            self._image_details, self._aggregates = [], []
            self._trace_buffer, self._trace_frames = [], 0
            self._frozen_models = {}
            self._validation_batches, self._point_packets = [], []
            self._training_round_ids = []
            self._learning_note = ""
            self._write_attempt("in_progress")
        except OSError as error:
            self._fail(f"无法建立本次采集记录：{error}；上次校准仍保留。")
            return
        self._armed = True
        self._started = monotonic()

    def _collect(self, packet: dict[str, Any]) -> None:
        if self._closed or not 0 <= monotonic_ns() - packet["timestamp_ns"] <= 1_000_000_000:
            return
        self._ready = True
        self._last_packet = monotonic()
        self._quality = float(packet["confidence"])
        mode = {
            "binocular": "双眼",
            "left_eye": "左眼",
            "right_eye": "右眼",
            "unavailable": "眼部信息不足",
        }[packet["mode"]]
        size = packet.get("frame_size", [0, 0])
        self._frame_size = list(size)
        self._quality_text = f"{mode} · 信号质量 {self._quality:.0%} · 图像 {size[0]}×{size[1]}"
        self._observation_state = self._monitor.update(packet, self._model or self._preview_model)
        self._quality_text += "\n" + OBSERVATION_LABELS[self._observation_state]
        if packet["mode"] == "unavailable":
            reason = str(packet.get("reason", ""))
            if reason == "no_face":
                hint = "未检出人脸，请正对摄像头并检查遮挡"
            elif "closed_or_occluded" in reason:
                hint = "可见眼部信息不足，请调整角度或眼睑遮挡"
            elif "small" in reason or "pixels" in reason:
                hint = "眼部像素过少，可适当靠近摄像头"
            else:
                hint = "眼部图像信息不足，请调整光照或镜片反光"
            self._quality_text += f" · {hint}"
        self._preview = None
        candidate = None
        if (
            packet["features"] is not None
            and self._quality >= MIN_SIGNAL_QUALITY
            and self._observation_state != "pose_unavailable"
        ):
            model = self._model or self._preview_model
            self._preview_calibrated = bool(model)
            if model:
                candidate = predict_packet(model, packet)
            else:
                candidate = tuple(packet["preview"])
            if candidate is not None and all(np.isfinite(v) and 0 <= v <= 1 for v in candidate):
                self._preview = self._smoother.update(
                    candidate, packet["timestamp_ns"], self._quality
                )
            elif candidate is None:
                self._observation_state = "eye_mapping_unavailable"
                self._quality_text += " · " + OBSERVATION_LABELS[self._observation_state]
            elif model:
                self._quality_text += " · 估计已到屏幕外，可按空格重新校准"
        if self._preview is None:
            self._smoother.reset()
        if self._armed and self._trace_frames < 10_000:
            # Private numeric evidence for replay. Never store camera images;
            # logging cannot feed the estimator or modify calibration labels.
            self._trace_buffer.append(
                {
                    "sequence": packet["sequence"],
                    "timestamp_ns": packet["timestamp_ns"],
                    "point_index": self._index,
                    "stage": "training" if self._index < len(TRAIN_POINTS) else "validation",
                    "features": packet["features"],
                    "eye_features": packet.get("eye_features", {}),
                    "eye_diagnostics": packet.get("eye_diagnostics", {}),
                    "raw_prediction": candidate,
                    "filtered_prediction": self._preview,
                    "reticle_visible": self.visible_reticle() is not None,
                    "quality": self._quality,
                    "mode": packet["mode"],
                    "observation_state": self._observation_state,
                }
            )
            self._trace_frames += 1
        if (
            not self._armed
            or monotonic() - self._started < 1.0
            or packet["timestamp_ns"] < int((self._started + 1.0) * 1e9)
        ):
            self.update()
            return
        self._eligible_count += 1
        self._point_states[self._observation_state] += 1
        if self._index >= len(TRAIN_POINTS) and len(self._point_packets) < 1000:
            # All settled frames, including missing eyes, are shared between
            # the frozen models. Missing output must count against coverage.
            self._point_packets.append(deepcopy(packet))
        if (
            packet["features"] is not None
            and self._quality >= MIN_SIGNAL_QUALITY
            and self._observation_state != "pose_unavailable"
            and (self._index < len(TRAIN_POINTS) or candidate is not None)
        ):
            if len(self._point_samples) < 300:
                self._point_samples.append(packet["features"])
                self._point_qualities.append(self._quality)
                self._point_eye_features.append(packet.get("eye_features", {}))
                if self._index >= len(TRAIN_POINTS) and candidate is not None:
                    self._point_predictions.append(candidate)
                    for name, child in self._model.get("eye_models", {}).items():
                        row = packet.get("eye_features", {}).get(name)
                        if row is not None and (not child.get("pose_required") or row[15] == 1):
                            self._point_eye_predictions.setdefault(name, []).append(
                                predict_calibration(child, row)
                            )
                if self._index < len(TRAIN_POINTS):
                    for eye in packet.get("eye_diagnostics", {}).values():
                        if eye.get("valid") and eye.get("image_detail", 0) > 0:
                            self._image_details.append(eye["image_detail"])
        self.update()

    def _advance(self) -> None:
        if self._closed:
            return
        if monotonic() - self._last_packet > 0.3:
            self._preview = None
            self._smoother.reset()
        self.update()
        duration = 4.0 if len(BASE_TRAIN_POINTS) <= self._index < len(TRAIN_POINTS) else 2.5
        if not self._armed or monotonic() - self._started < duration:
            return
        if len(self._point_samples) < 5:
            if monotonic() - self._started > 15:
                self._fail(
                    "当前点有效眼部样本不足。可调整光照、距离或眼镜角度后重试；不会补造眼动。"
                )
            return
        try:
            point_target = (TRAIN_POINTS + VALIDATION_POINTS)[self._index]
            samples = np.asarray(self._point_samples)
            median = np.median(samples, axis=0)
            self._aggregates.append(
                {
                    "stage": "training" if self._index < len(TRAIN_POINTS) else "validation",
                    "target": list(point_target),
                    "feature_median": median.tolist(),
                    "feature_mad": np.median(np.abs(samples - median), axis=0).tolist(),
                    "sample_count": len(samples),
                }
            )
            if self._index < len(TRAIN_POINTS):
                self._features.extend(self._point_samples)
                self._targets.extend([TRAIN_POINTS[self._index]] * len(self._point_samples))
                self._qualities.extend(self._point_qualities)
                self._eye_features.extend(self._point_eye_features)
                self._training_points.append(
                    {
                        "target": list(TRAIN_POINTS[self._index]),
                        "sample_count": len(self._point_samples),
                        "valid_ratio": len(self._point_samples)
                        / max(self._eligible_count, len(self._point_samples)),
                        "mean_signal_quality": float(np.mean(self._point_qualities)),
                        "head_feature_span": np.ptp(samples[:, POSE_COLUMNS], axis=0).tolist(),
                        "observation_counts": dict(self._point_states),
                    }
                )
                if self._index >= 4:
                    self._saved_preview = False
                    self._preview_model = fit_calibration(
                        self._features,
                        self._targets,
                        self._qualities,
                        minimum_points=len(set(TRAIN_POINTS[: self._index + 1])),
                        eye_features=self._eye_features or None,
                    )
                    self._smoother.reset()
                if self._index == len(TRAIN_POINTS) - 1:
                    # Freeze before independent validation: no validation targets enter fit.
                    self._model = fit_calibration(
                        self._features,
                        self._targets,
                        self._qualities,
                        eye_features=self._eye_features or None,
                    )
                    if self._image_details:
                        self._model["image_detail_reference"] = float(
                            np.median(self._image_details)
                        )
                    self._freeze_learning_models()
                    self._monitor.reset()
            else:
                if self._point_packets:
                    self._validation_batches.append(
                        {"target": list(point_target), "packets": self._point_packets}
                    )
                target = np.array(VALIDATION_POINTS[self._index - len(TRAIN_POINTS)])
                predictions = np.array(
                    self._point_predictions
                    if "eye_models" in self._model
                    else [predict_calibration(self._model, row) for row in self._point_samples]
                )
                residuals = predictions - target
                errors = np.linalg.norm(residuals, axis=1).tolist()
                self._errors.extend(errors)
                eye_errors = {}
                for name, eye_points in self._point_eye_predictions.items():
                    distances = np.linalg.norm(np.asarray(eye_points) - target, axis=1)
                    eye_errors[name] = {
                        "sample_count": len(eye_points),
                        "mean_error": float(np.mean(distances)),
                        "max_error": float(np.max(distances)),
                    }
                self._validation_points.append(
                    {
                        "target": target.tolist(),
                        "sample_count": len(errors),
                        "valid_ratio": len(errors) / max(self._eligible_count, len(errors)),
                        "mean_error": float(np.mean(errors)),
                        "max_error": float(np.max(errors)),
                        "mean_signal_quality": float(np.mean(self._point_qualities)),
                        "mean_prediction": np.mean(predictions, axis=0).tolist(),
                        "eye_errors": eye_errors,
                        "horizontal_mae": float(np.mean(np.abs(residuals[:, 0]))),
                        "vertical_mae": float(np.mean(np.abs(residuals[:, 1]))),
                        "horizontal_bias": float(np.mean(residuals[:, 0])),
                        "vertical_bias": float(np.mean(residuals[:, 1])),
                        "head_feature_span": np.ptp(samples[:, POSE_COLUMNS], axis=0).tolist(),
                        "observation_counts": dict(self._point_states),
                    }
                )
            self._index += 1
            self._point_samples, self._point_qualities = [], []
            self._point_eye_features, self._point_predictions = [], []
            self._point_eye_predictions = {}
            self._point_packets = []
            self._eligible_count = 0
            self._point_states.clear()
            self._started = monotonic()
            if self._index == len(TRAIN_POINTS) + len(VALIDATION_POINTS):
                self._finish_calibration()
            else:
                self._write_attempt("in_progress")
        except Exception as error:
            self._fail(str(error))

    def _freeze_learning_models(self) -> None:
        """Persist training and freeze every contender before seeing holdout labels."""
        runtime = self.settings.data_dir / "runtime" / "webcam"
        assert self._attempt_path is not None
        round_id = self._attempt_path.stem[len(self._identity[:24]) + 1 :]
        save_training_round(
            runtime,
            self._identity,
            round_id,
            self._features,
            self._targets,
            self._qualities,
            self._eye_features,
        )
        self._model["training_round_ids"] = [round_id]
        self._frozen_models = {"current": deepcopy(self._model)}
        if self._incumbent_model:
            self._frozen_models["previous"] = deepcopy(self._incumbent_model)
        # A pooled fit is a contender, never an unconditional replacement.
        # If it cannot be fitted, this round and the previous model remain usable.
        try:
            accumulated, self._training_round_ids = fit_accumulated(
                runtime, self._identity, round_id
            )
            if accumulated is not None:
                self._frozen_models["accumulated"] = accumulated
        except (ValueError, OSError, np.linalg.LinAlgError) as error:
            self._learning_note = f"累计候选暂未生成：{error}；本轮训练已保存。"
            self._training_round_ids = [round_id]

    def _finish_calibration(self) -> None:
        current = next((s for s in QApplication.screens() if s.name() == self._screen[0]), None)
        if (
            current is None
            or (current.geometry().width(), current.geometry().height()) != self._screen[1:]
        ):
            self._fail("患者屏幕已断开或分辨率改变，请重新校准。")
            return
        if len(self._validation_points) != len(VALIDATION_POINTS) or any(
            p["sample_count"] < 5 for p in self._validation_points
        ):
            self._fail("九点独立验证未完成。")
            return
        now = datetime.now(UTC)
        current_metrics = self._grade_model(
            self._model,
            self._validation_points,
            float(np.mean([p["mean_error"] for p in self._validation_points])),
            float(np.max(self._errors)),
            float(np.percentile(self._errors, 90)),
            now,
        )
        current_model = {**self._model, **current_metrics}
        selected = "current"
        evaluations: dict[str, Any] = {}
        if self._frozen_models and len(self._validation_batches) == len(VALIDATION_POINTS):
            evaluations = evaluate_models(
                self._frozen_models,
                self._validation_batches,
                self.settings.webcam_calibration_profile,
            )
            selected = choose_model(evaluations)
            score = evaluations[selected]
            if not score["eligible"]:
                self._fail("本轮训练已保存；验证可用信息不足，上次校准仍保留。")
                return
            selected_model = deepcopy(self._frozen_models[selected])
            selected_metrics = self._grade_model(
                selected_model,
                score["per_point"],
                score["mean_error"],
                score["max_error"],
                score["p90_error"],
                now,
            )
            selected_model.update(selected_metrics)
        else:
            selected_model, selected_metrics = current_model, current_metrics
        self._model = selected_model
        passed = selected_metrics["validated"]
        selection = {"selected": selected, "comparisons": evaluations}
        self._model.update(
            reusable=True,
            quality_status="meets_target" if passed else "needs_improvement",
            model_selection=selection,
            last_evaluated_at=now.isoformat(),
        )
        directory = self.settings.data_dir / "runtime" / "webcam"
        candidates = directory / "calibration_candidates"
        candidates.mkdir(parents=True, exist_ok=True)
        candidate_name = f"{self._identity[:24]}-{now.strftime('%Y%m%dT%H%M%S%f')}.json"
        # Keep the current-round model and its original trace metrics together.
        # Selection changes the active model, never rewrites recorded predictions.
        (candidates / candidate_name).write_text(
            json.dumps(
                {
                    "model": current_model,
                    "validation_metrics": current_metrics,
                    "point_aggregates": self._aggregates,
                    "task_eligible": False,
                    "model_selection": selection,
                    "contenders": self._frozen_models,
                },
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if passed:
            calibration = directory / "calibration"
            calibration.mkdir(parents=True, exist_ok=True)
            path = calibration / f"{self._identity[:24]}.json"
            versions = directory / "calibration_versions" / self._identity[:24]
            versions.mkdir(parents=True, exist_ok=True)
            version_name = now.strftime("%Y%m%dT%H%M%S%f") + ".json"
            model_text = json.dumps(self._model, allow_nan=False, ensure_ascii=False, indent=2)
            # Preserve rollback evidence before replacing the active file atomically.
            if path.exists():
                (versions / (now.strftime("%Y%m%dT%H%M%S%f") + "-previous.json")).write_bytes(
                    path.read_bytes()
                )
            (versions / version_name).write_text(model_text, encoding="utf-8")
            temporary = path.with_suffix(".tmp")
            temporary.write_text(model_text, encoding="utf-8")
            temporary.replace(path)
        current_metrics.update(
            candidate_file=candidate_name,
            reusable=True,
            active_model_metrics=deepcopy(selected_metrics),
            model_selection=selection,
            training_round_ids=self._training_round_ids,
        )
        self._write_attempt("completed", metrics=current_metrics)
        names = {"current": "本轮模型", "previous": "保留上次模型", "accumulated": "累计模型"}
        self.summary_text = (
            f"本轮采集已完成并保存；当前使用{names[selected]}。"
            + ("达到精度目标。" if passed else "精度仍待改进；候选已保存，原有合格校准不变。")
            + f"平均误差 {selected_metrics['validation_error']:.1%}，"
            f"最大误差 {selected_metrics['validation_max_error']:.1%}（屏幕归一化距离）；"
            f"横向 {selected_metrics['horizontal_mae']:.1%} 屏宽 / "
            f"纵向 {selected_metrics['vertical_mae']:.1%} 屏高。"
            f"边缘 {selected_metrics['edge_mean_error']:.1%} / "
            f"中心 {selected_metrics['center_mean_error']:.1%}。"
            + (
                f"累计训练档案 {len(self._training_round_ids)} 轮。"
                if self._training_round_ids
                else ""
            )
            + self._learning_note
        )
        self._validation_passed = passed
        self._saved_preview = True
        self._armed = False
        self._reviewing = True
        self._preview = None
        self._smoother.reset()
        self._timer.start()
        self.update()

    def _grade_model(
        self,
        model: dict[str, Any],
        points: list[dict[str, Any]],
        mean_error: float,
        max_error: float,
        p90_error: float,
        now: datetime,
    ) -> dict[str, Any]:
        """Describe measured precision without turning it into a participation gate."""
        profile = self.settings.webcam_calibration_profile
        passed = validation_passes(mean_error, max_error, profile)
        eye_validation = {}
        for name, child in model.get("eye_models", {}).items():
            results = [point.get("eye_errors", {}).get(name, {}) for point in points]
            complete = all(result.get("sample_count", 0) >= 5 for result in results)
            eye_mean = float(np.mean([r["mean_error"] for r in results])) if complete else None
            eye_max = float(np.max([r["max_error"] for r in results])) if complete else None
            child["validated"] = bool(
                eye_mean is not None
                and eye_max is not None
                and validation_passes(eye_mean, eye_max, profile)
            )
            child["validation_error"] = eye_mean
            child["validation_max_error"] = eye_max
            child["calibration_profile"] = profile
            child["reusable"] = complete
            eye_validation[name] = {
                "validated": child["validated"],
                "mean_error": eye_mean,
                "max_error": eye_max,
            }
        horizontal = float(np.mean([p["horizontal_mae"] for p in points]))
        vertical = float(np.mean([p["vertical_mae"] for p in points]))
        locations = [p.get("target", VALIDATION_POINTS[i]) for i, p in enumerate(points)]
        edge_error = float(
            np.mean(
                [
                    p["mean_error"]
                    for p, target in zip(points, locations, strict=True)
                    if tuple(target) != (0.5, 0.5)
                ]
            )
        )
        center_error = float(
            next(
                p["mean_error"]
                for p, target in zip(points, locations, strict=True)
                if tuple(target) == (0.5, 0.5)
            )
        )
        predictions = np.array([p["mean_prediction"] for p in points])
        # Regression slope against holdout targets describes response, not an
        # accuracy probability. Never use it to refit or amplify the model.
        response = {}
        for axis, name in enumerate(("horizontal", "vertical")):
            targets = np.array(locations)[:, axis]
            centered = targets - targets.mean()
            response[name] = float(centered @ predictions[:, axis] / (centered @ centered))
        return {
            "identity": self._identity,
            "algorithm_version": ACIES_VERSION,
            "validated": passed,
            "calibration_profile": profile,
            "validation_error": mean_error,
            "validation_max_error": max_error,
            "validation_p90_error": p90_error,
            "validation_points": points,
            "horizontal_mae": horizontal,
            "vertical_mae": vertical,
            "edge_mean_error": edge_error,
            "center_mean_error": center_error,
            "response_slope": response,
            "eye_validation": eye_validation,
            "created_at": model.get("created_at", now.isoformat()),
            "last_evaluated_at": now.isoformat(),
            "validation_cursor_visible": False,
            "screen_focus": "unknown",
        }

    def _write_attempt(
        self, status: str, *, reason: str = "", metrics: dict[str, Any] | None = None
    ) -> None:
        now = datetime.now(UTC)
        if self._attempt_path is None:
            directory = self.settings.data_dir / "runtime" / "webcam" / "calibration_attempts"
            directory.mkdir(parents=True, exist_ok=True)
            self._attempt_path = directory / (
                f"{self._identity[:24]}-{now.strftime('%Y%m%dT%H%M%S%f')}.json"
            )
            self._attempt_started = now.isoformat()
        trace_dir = self._attempt_path.parent.parent / "calibration_traces"
        trace_name = self._attempt_path.stem + ".jsonl"
        if self._trace_buffer:
            trace_dir.mkdir(parents=True, exist_ok=True)
            with (trace_dir / trace_name).open("a", encoding="utf-8") as stream:
                for frame in self._trace_buffer:
                    stream.write(json.dumps(frame, allow_nan=False, ensure_ascii=False) + "\n")
            self._trace_buffer.clear()
        summary = {
            "identity": self._identity,
            "algorithm_version": ACIES_VERSION,
            "calibration_profile": self.settings.webcam_calibration_profile,
            "created_at": self._attempt_started,
            "started_at": self._attempt_started,
            "updated_at": now.isoformat(),
            "status": status,
            "reason": reason,
            "validated": status == "passed",
            "validation_cursor_visible": False,
            "frame_size": self._frame_size,
            "training_points": self._training_points,
            "training_targets": TRAIN_POINTS,
            "validation_targets": VALIDATION_POINTS,
            "validation_points": self._validation_points,
            "current_point_samples": len(self._point_samples),
            "axis_models": self._model.get("axis_models", {}),
            "eye_axis_models": {
                name: child["axis_models"]
                for name, child in self._model.get("eye_models", {}).items()
            },
            "training_signal": self._model.get("training_signal", {}),
            "screen_focus": "unknown",
            "trace_file": trace_name,
            "trace_frames": self._trace_frames,
            "trace_limit": 10_000,
            **(metrics or {}),
        }
        temporary = self._attempt_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(summary, allow_nan=False, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self._attempt_path)
        self._attempt_finished = status != "in_progress"

    def _fail(self, message: str) -> None:
        if self._closed:
            return
        if self._reviewing:
            if self._validation_passed:
                self.accept()
            else:
                self.reject()
            QMessageBox.warning(self.parentWidget(), "校准记录已保存，试看中断", message)
            return
        if self._attempt_path is not None:
            try:
                if not self._attempt_finished:
                    self._write_attempt("failed", reason=message)
            except OSError as error:
                message += f"\n记录写入失败：{error}"
        self._attempt_finished = True
        self._reviewing = False
        self.reject()
        QMessageBox.warning(self.parentWidget(), "摄像头校准未完成", message)

    def done(self, result: int) -> None:
        if self._reviewing and self._validation_passed:
            result = QDialog.DialogCode.Accepted
        if self._armed and not self._attempt_finished:
            try:
                self._write_attempt("cancelled", reason="操作者退出校准")
            except OSError as error:
                QMessageBox.warning(self, "校准记录未写入", str(error))
        self._closed = True
        self._timer.stop()
        if self._worker is not None:
            self._worker.requestInterruption()
            self._worker.process.interrupt()
            if not self._worker.wait(3500):
                self._worker.process.close()
                self._worker.wait(1500)
        super().done(result)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() == Qt.Key.Key_Space:
            self._begin()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._reviewing:
            self.accept()
        else:
            super().keyPressEvent(event)

    def visible_reticle(self) -> tuple[float, float] | None:
        """Never expose feedback during the independent measurement phase."""
        if (
            self._index >= len(TRAIN_POINTS) and not self._reviewing
        ) or monotonic() - self._last_packet > 0.3:
            return None
        return self._preview

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f7f9fc"))
        training = self._index < len(TRAIN_POINTS)
        if self._armed:
            points = TRAIN_POINTS + VALIDATION_POINTS
            point = points[min(self._index, len(points) - 1)]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2563eb"))
            painter.drawEllipse(
                int(point[0] * self.width()) - 12, int(point[1] * self.height()) - 12, 24, 24
            )
        preview = self.visible_reticle()
        if preview is not None:
            good = self._preview_calibrated and self._quality >= 0.5
            if self._reviewing and not self._validation_passed:
                good = False
            color = "#059669" if good else "#d97706"
            painter.setPen(QPen(QColor(color), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            x, y = int(preview[0] * self.width()), int(preview[1] * self.height())
            painter.drawEllipse(x - 16, y - 16, 32, 32)
            painter.drawLine(x - 25, y, x + 25, y)
            painter.drawLine(x, y - 25, x, y + 25)
        painter.setPen(QColor("#1e293b"))
        font = painter.font()
        font.setPointSize(13)
        painter.setFont(font)
        if self._reviewing:
            result_note = (
                "采集完成，已保存 · 达到精度目标"
                if self._validation_passed
                else "采集完成，候选已保存 · 精度待改进"
            )
            text = (
                f"ACIES {ACIES_VERSION} · {result_note} · 自由看向四周检查准心\n"
                "Enter / Esc 完成 · 空格重新校准"
            )
        elif self._armed:
            if self._index < len(BASE_TRAIN_POINTS):
                stage = f"校准 {self._index + 1}/9 · 注视蓝点，允许自然头动"
            elif training:
                pose_index = self._index - len(BASE_TRAIN_POINTS)
                stage = f"头动补充 {pose_index + 1}/5 · {POSE_PROMPTS[pose_index]}"
            else:
                stage = f"独立验证 {self._index - len(TRAIN_POINTS) + 1}/9 · 自然看向蓝点"
            text = f"ACIES {ACIES_VERSION} · {stage}\nEsc 取消 · 无需僵直，也不必刻意大幅转头"
        else:
            text = (
                f"ACIES {ACIES_VERSION} · 空格开始：9 点校准 + 5 次自然头动补充 + 9 点验证\n"
                "约一分钟；注视蓝点，保持舒适坐姿即可 · Esc 取消"
                if self._ready
                else self._message
            )
        painter.drawText(
            self.rect().adjusted(30, 20, -30, -20),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
            text,
        )
        if self._reviewing:
            cursor_note = (
                self.summary_text
                + (
                    "\n重启后沿用当前保存模型。"
                    if self._validation_passed
                    else "\n本轮仅供试看，原有合格校准保持不变。"
                )
                + "自然看向四周检查准心。"
            )
        elif not training:
            cursor_note = "独立验证隐藏准心，避免视觉反馈改变测量；只注视蓝点。"
        elif self._saved_preview:
            cursor_note = "十字沿用已保存的校准；可直接使用，也可按空格增加一轮采集。"
        elif self._preview_calibrated:
            cursor_note = "十字为临时估计：绿色信息较足，橙色信息有限；请注视蓝点而非追随十字。"
        else:
            cursor_note = (
                "尚无本版本有效校准；橙色十字仅用于检查信号，活动范围不代表校准后范围。\n"
                "完成前五点后更新映射；完成验证后可自由试看。"
            )
        footer = (
            f"{self._quality_text}\n{cursor_note}\n"
            f"{PROFILE_LABELS[self.settings.webcam_calibration_profile]} · 屏幕聚焦状态无法判定"
        )
        if self._armed:
            # Keep the bottom-center target clear; detailed guidance is shown
            # before calibration and in review, where no targets are present.
            footer = self._quality_text
        painter.drawText(
            self.rect().adjusted(30, 20, -30, -20),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            footer,
        )
