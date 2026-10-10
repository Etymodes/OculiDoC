"""Local engineering experiment: frozen mapping, hidden reticle, no model writes."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, monotonic_ns
from typing import Any

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from oculidoc.config import Settings
from oculidoc.devices.acies import ACIES_VERSION, GazeSmoother, ObservationMonitor
from oculidoc.devices.acies_experiments import (
    CONDITIONS,
    FILTER_BASELINE,
    experiment_plan,
    filter_sweep,
    load_experiment_model,
    load_paired_candidate,
    prediction_error,
    summarize_experiment,
    write_error_table,
)
from oculidoc.devices.webcam_gaze import (
    WebcamFeatureProcess,
    calibration_identity,
    predict_packet,
    read_webcam_config,
)
from oculidoc.ui.webcam_calibration import CalibrationCapture


class AciesExperimentDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(f"ACIES {ACIES_VERSION} · 对照测试")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._model: dict[str, Any] = {}
        self._candidate: dict[str, Any] | None = None
        self._candidate_smoother = GazeSmoother()
        self._worker: CalibrationCapture | None = None
        self._seed, self._round = secrets.randbits(31), 0
        self._active, self._closed, self._ready = False, False, False
        self._short = False
        self._trial, self._started, self._last_packet = 0, 0.0, 0.0
        self._frames: list[dict[str, Any]] = []
        self._flushed = 0
        self._directory: Path | None = None
        self._plan = experiment_plan(self._seed)
        self._smoother, self._monitor = GazeSmoother(), ObservationMonitor()
        self._message = "正在连接摄像头…"
        self._manifest: dict[str, Any] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        screen = next(
            (s for s in QApplication.screens() if s.name() == self.settings.patient_screen_name),
            None,
        )
        if not self.settings.patient_id or screen is None:
            QMessageBox.warning(self, "无法测试", "请先选择受试者和患者显示屏。")
            return
        try:
            config = read_webcam_config(self.settings.webcam_providers_path)
            identity = calibration_identity(
                config,
                "acies",
                self.settings.patient_id,
                (screen.name(), screen.geometry().width(), screen.geometry().height()),
            )
            self._model = load_experiment_model(
                self.settings.data_dir / "runtime/webcam",
                identity,
                self.settings.webcam_calibration_profile,
            )
            self._candidate = load_paired_candidate(
                self.settings.data_dir / "runtime/webcam", self._model
            )
        except Exception as error:
            QMessageBox.warning(self, "需要校准记录", str(error))
            return
        self._screen = (screen.name(), screen.geometry().width(), screen.geometry().height())
        self.setScreen(screen)
        self.move(screen.geometry().topLeft())
        self.showFullScreen()
        process = WebcamFeatureProcess(
            self.settings.webcam_providers_path, "acies", self.settings.data_dir / "runtime/webcam"
        )
        self._worker = CalibrationCapture(process, self)
        self._worker.packet.connect(self._collect)
        self._worker.failed.connect(self._fail)
        self._worker.start()
        self._timer.start()
        self.exec()

    def _begin(self, short: bool = False) -> None:
        if self._active or not self._ready or monotonic() - self._last_packet > 0.3:
            return
        self._short = short
        self._plan = experiment_plan(self._seed, self._round, short=short)
        self._trial, self._frames, self._flushed = 0, [], 0
        now = datetime.now(UTC)
        self._directory = (
            self.settings.data_dir / "runtime/webcam/experiments" / now.strftime("%Y%m%dT%H%M%S%f")
        )
        self._directory.mkdir(parents=True, exist_ok=False)
        model_text = json.dumps(self._model, sort_keys=True, allow_nan=False)
        # A private snapshot makes comparisons reproducible even after later calibration.
        (self._directory / "model.json").write_text(model_text, encoding="utf-8")
        if self._candidate:
            (self._directory / "candidate.json").write_text(
                json.dumps(self._candidate, sort_keys=True, allow_nan=False), encoding="utf-8"
            )
        self._manifest = {
            "schema_version": 2,
            "algorithm_version": ACIES_VERSION,
            "model_version": self._model.get("algorithm_version"),
            "model_source": self._model.get("experiment_source", "validated"),
            "calibration_passed": self._model.get("validated") is True,
            "model_sha256": hashlib.sha256(model_text.encode()).hexdigest(),
            "seed": self._seed,
            "round_index": self._round,
            "short": short,
            "plan": self._plan,
            "started_at": now.isoformat(),
            "screen": self._screen,
            "filter": FILTER_BASELINE,
            "reticle_visible": False,
            "calibration_changed": False,
            "paired_candidate": self._candidate["name"] if self._candidate else None,
            "candidate_auto_applied": False,
            "status": "in_progress",
        }
        self._save()
        self._smoother.reset()
        self._candidate_smoother.reset()
        self._monitor.reset()
        self._started = monotonic()
        self._active = True
        self.update()

    def _collect(self, packet: dict[str, Any]) -> None:
        if self._closed or not 0 <= monotonic_ns() - packet["timestamp_ns"] <= 1_000_000_000:
            return
        self._ready = packet["features"] is not None
        self._last_packet = monotonic()
        if not self._active:
            if not self._manifest:
                self._message = "摄像头已连接" if self._ready else "正在等待可见眼部信息"
            self.update()
            return
        elapsed = packet["timestamp_ns"] / 1e9 - self._started
        if elapsed < 0 or elapsed >= self._plan[self._trial]["duration_s"]:
            return  # Late frames belong to their captured phase, not the next target.
        state = self._monitor.update(packet, self._model)
        quality = packet["confidence"]
        if state in {
            "binocular_changed",
            "image_detail_low",
            "reflection",
            "pose_outside_calibration",
        }:
            quality *= 0.65
        raw = predict_packet(self._model, packet) if state != "pose_unavailable" else None
        filtered = None
        if raw is not None and all(np.isfinite(v) and 0 <= v <= 1 for v in raw):
            filtered = self._smoother.update(raw, packet["timestamp_ns"], quality)
        else:
            self._smoother.reset()
        candidate_raw, candidate_filtered = None, None
        if self._candidate:
            candidate_raw = predict_packet(self._candidate["model"], packet)
            if candidate_raw is not None and all(
                np.isfinite(v) and 0 <= v <= 1 for v in candidate_raw
            ):
                candidate_filtered = self._candidate_smoother.update(
                    candidate_raw, packet["timestamp_ns"], quality
                )
            else:
                self._candidate_smoother.reset()
        eyes = packet.get("eye_diagnostics", {})
        closure = packet["features"] is None and all(
            eyes.get(name, {}).get("reason") == "eye_closed_or_occluded"
            for name in ("left", "right")
        )
        self._frames.append(
            {
                "trial": self._trial,
                "elapsed_s": elapsed,
                "sequence": packet["sequence"],
                "timestamp_ns": packet["timestamp_ns"],
                "features": packet["features"],
                "eye_features": packet.get("eye_features", {}),
                "eye_diagnostics": eyes,
                "frame_size": packet.get("frame_size"),
                "mode": packet["mode"],
                "quality": quality,
                "observation_state": state,
                "closure_candidate": closure,
                "raw_prediction": raw,
                "filtered_prediction": filtered,
                "candidate_raw_prediction": candidate_raw,
                "candidate_filtered_prediction": candidate_filtered,
                "candidate_error": prediction_error(
                    candidate_raw, self._plan[self._trial]["target"], self._screen[1:]
                ),
                "intended_target": self._plan[self._trial]["target"],
                "raw_error": prediction_error(
                    raw, self._plan[self._trial]["target"], self._screen[1:]
                ),
                "filtered_error": prediction_error(
                    filtered, self._plan[self._trial]["target"], self._screen[1:]
                ),
            }
        )
        if len(self._frames) - self._flushed >= 100:
            self._save()

    def _save(self) -> None:
        if self._directory is None:
            return
        if self._flushed < len(self._frames):
            with (self._directory / "frames.jsonl").open("a", encoding="utf-8") as stream:
                for row in self._frames[self._flushed :]:
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
            self._flushed = len(self._frames)
        self._manifest.update(frames=len(self._frames), completed_trials=self._trial)
        temp = self._directory / "manifest.tmp"
        temp.write_text(
            json.dumps(self._manifest, ensure_ascii=False, allow_nan=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self._directory / "manifest.json")

    def _advance(self) -> None:
        if self._closed:
            return
        if self._active:
            current = next((s for s in QApplication.screens() if s.name() == self._screen[0]), None)
            if (
                current is None
                or (current.geometry().width(), current.geometry().height()) != self._screen[1:]
            ):
                self._fail("显示屏发生变化，测试已停止并保留已有记录。")
                return
            if monotonic() - self._last_packet > 3:
                self._fail("相机输出中断，测试已停止并保留已有记录。")
                return
            if monotonic() - self._started >= self._plan[self._trial]["duration_s"]:
                self._trial += 1
                self._save()
                if self._trial == len(self._plan):
                    self._finish("completed")
                else:
                    self._started = monotonic()
        self.update()

    def _finish(self, status: str) -> None:
        self._active = False
        self._manifest.update(status=status, finished_at=datetime.now(UTC).isoformat())
        self._save()
        if self._directory is None:
            return
        summary = summarize_experiment(self._plan, self._frames, self._screen[1:])
        write_error_table(
            self._directory / "errors.csv", self._plan, self._frames, self._screen[1:]
        )
        (self._directory / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
        )
        if self._candidate:
            candidate_frames = [
                {
                    **row,
                    "raw_prediction": row["candidate_raw_prediction"],
                    "filtered_prediction": row["candidate_filtered_prediction"],
                }
                for row in self._frames
            ]
            (self._directory / "candidate_summary.json").write_text(
                json.dumps(
                    summarize_experiment(self._plan, candidate_frames, self._screen[1:]),
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        # All candidates see exactly the same frames. No model fitting or parameter writes.
        sweep = filter_sweep(self._plan, self._frames)
        (self._directory / "filter_sweep.json").write_text(
            json.dumps(sweep, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
        )
        lines = [
            "本轮完成；校准和参数保持原样。"
            if status == "completed"
            else "本轮未完成，已有记录已保存。"
        ]
        for group in summary["groups"]:
            metric = group["filtered"]
            if metric["mean_error"] is not None:
                label = {"baseline": "正常", "head_motion": "头动", "blink": "眨眼"}[
                    group["condition"]
                ]
                region = "中心" if group["region"] == "center" else "边缘"
                lines.append(
                    f"{label}·{region}：误差 {metric['mean_error']:.1%} / "
                    f"有效帧 {metric['coverage']:.0%}"
                )
        lines.append("数值对照已保存；少量重复不能证明最佳参数。")
        if self._candidate:
            lines.append("候选映射已用相同帧同时记录，尚未应用到正式任务。")
        self._message = "\n".join(lines)
        self._round += 1
        self.update()

    def _fail(self, message: str) -> None:
        if self._active:
            self._finish("failed")
        self._message = message
        self._ready = False
        self.update()

    def done(self, result: int) -> None:
        if self._closed:
            return
        if self._active:
            self._finish("cancelled")
        self._closed = True
        self._timer.stop()
        if self._worker:
            self._worker.requestInterruption()
            self._worker.process.interrupt()
            self._worker.wait(2000)
            if self._worker.isRunning():
                self._worker.process.close()
                self._worker.wait(2000)
        super().done(result)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() in (Qt.Key.Key_Space, Qt.Key.Key_1) and not event.isAutoRepeat():
            self._begin(short=event.key() == Qt.Key.Key_1)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111b2c"))
        painter.setPen(QColor("#e9f0fc"))
        font = painter.font()
        font.setPointSize(19)
        painter.setFont(font)
        if not self._active:
            source_note = (
                "当前为历史诊断候选，仅用于对照；尚未保存为可复用模型。\n"
                if self._model.get("experiment_source") == "candidate"
                else (
                    "当前使用已保存的固定映射；精度仍待改进，本轮如实记录误差。\n"
                    if self._model.get("validated") is not True
                    else "当前使用已保存的固定映射；精度以本轮实际误差为准。\n"
                )
            )
            if self._candidate:
                source_note += "本轮同时记录原模型与去眼睑特征候选；两者均不使用蓝点修正预测。\n"
            message = (
                f"ACIES {ACIES_VERSION} 对照测试 · 第 {self._round + 1} 轮\n\n{self._message}\n\n"
                f"{source_note}"
                "空格：完整测试约 2 分钟（正常、头动、眨眼）\n1：仅中心与边缘，约 40 秒\n"
                "Esc：保存已有记录并退出\n\n只看蓝点，测试时隐藏准心。保持光照、眼镜与座位不变。\n"
                "全程固定同一份校准；记录数值，不保存图像。建议分三轮重复，可随时停止。"
            )
            painter.drawText(
                self.rect().adjusted(50, 40, -50, -40),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                message,
            )
            return
        trial = self._plan[self._trial]
        elapsed = monotonic() - self._started
        text = CONDITIONS[trial["condition"]]
        blink_cue = trial["condition"] == "blink" and 1.5 <= elapsed < 2.7
        if blink_cue:
            text = "现在自然眨眼一次，然后继续看蓝点"
        x, y = trial["target"]
        vertical = Qt.AlignmentFlag.AlignBottom if y < 0.25 else Qt.AlignmentFlag.AlignTop
        painter.drawText(
            self.rect().adjusted(80, 40, -80, -40),
            Qt.AlignmentFlag.AlignHCenter | vertical,
            f"{text}\n{self._trial + 1} / {len(self._plan)} · Esc 随时退出",
        )
        painter.setBrush(QColor("#ffbb56" if blink_cue else "#43b8ff"))
        painter.setPen(Qt.PenStyle.NoPen)
        from PySide6.QtCore import QPointF

        painter.drawEllipse(QPointF(x * self.width(), y * self.height()), 13, 13)
