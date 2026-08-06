"""Patient-facing stimulus and cue window for independent signal tasks."""

from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QGridLayout, QLabel, QMainWindow, QVBoxLayout, QWidget

from oculidoc.bci.ssvep.config import SsvepStimulusConfig
from oculidoc.bci.ssvep.evaluation import DecoderResult
from oculidoc.bci.ssvep.stimulus import frame_luminances
from oculidoc.signal_tasks.config import SignalTaskConfig, SignalTaskKind
from oculidoc.signal_tasks.runner import SignalTaskCancelled, run_signal_task
from oculidoc.signals.quality import SignalQualityAssessment


class _TaskWorker(QObject):
    trial_started = Signal(int, int, str, object)
    trial_decoded = Signal(int, object, object)
    trial_quality = Signal(int, object, object)
    finished = Signal(int, str, str)

    def __init__(
        self,
        config: SignalTaskConfig,
        output_directory: Path,
        patient_id: str | None,
        cancel_event: Event,
    ) -> None:
        super().__init__()
        self.config = config
        self.output_directory = output_directory
        self.patient_id = patient_id
        self.cancel_event = cancel_event

    @Slot()
    def run(self) -> None:
        try:
            result_path = run_signal_task(
                self.config,
                self.output_directory,
                patient_id=self.patient_id,
                wait_for_trials=True,
                trial_started=self.trial_started.emit,
                trial_decoded=self.trial_decoded.emit,
                trial_quality=self.trial_quality.emit,
                cancel_event=self.cancel_event,
            )
        except SignalTaskCancelled as error:
            self.finished.emit(2, str(error), "")
        except Exception as error:  # noqa: BLE001 -- child-process boundary.
            self.finished.emit(1, str(error), "")
        else:
            self.finished.emit(0, "", str(result_path))


class SignalTaskWindow(QMainWindow):
    """Render SSVEP flicker or MI/passive cues while acquisition runs off-thread."""

    completed = Signal(int, str, str)

    def __init__(
        self,
        config: SignalTaskConfig,
        output_directory: str | Path,
        *,
        patient_id: str | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.output_directory = Path(output_directory).expanduser().resolve()
        self.patient_id = patient_id
        self._cancel_event = Event()
        self._closing_after_finish = False
        self._frame_index = 0
        self._active_frequency: float | None = None
        self._selected_frequency: float | None = None
        self._quality_text = ""
        self._stimulus = (
            SsvepStimulusConfig.for_frequencies(
                config.frequencies_hz,
                refresh_rate_hz=config.refresh_rate_hz,
                screen_index=config.screen_index,
                window_seconds=config.duration_seconds,
            )
            if config.capability.paradigm.value == "ssvep"
            else None
        )
        self._target_labels: list[QLabel] = []

        self.setWindowTitle("OculiDoC 神经信号任务")
        self.setStyleSheet("QMainWindow,QWidget{background:#07111c;color:white}")
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 28, 32, 28)
        self.status_label = QLabel("正在准备信号任务…")
        self.status_label.setStyleSheet("font-size:24px;font-weight:700;color:#d5e8f8")
        layout.addWidget(self.status_label)
        self.cue_label = QLabel("")
        self.cue_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cue_label.setStyleSheet("font-size:48px;font-weight:800;color:white")
        layout.addWidget(self.cue_label)
        target_grid = QGridLayout()
        if self._stimulus is not None:
            for index, target in enumerate(self._stimulus.targets):
                target_text = config.target_labels[index] if config.target_labels else target.label
                label = QLabel(target_text)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setMinimumSize(220, 160)
                self._target_labels.append(label)
                target_grid.addWidget(label, index // 2, index % 2)
        else:
            self.cue_label.setText(
                "请保持放松并注视屏幕中央"
                if config.task_kind is SignalTaskKind.EEG_QUALITY
                else "等待运动想象提示"
            )
        layout.addLayout(target_grid, 1)
        self.setCentralWidget(container)

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(max(1, round(1000.0 / config.refresh_rate_hz)))
        self._frame_timer.timeout.connect(self._advance_frame)
        if self._stimulus is not None:
            self._frame_timer.start()

        self._thread = QThread(self)
        self._worker = _TaskWorker(
            config,
            self.output_directory,
            patient_id,
            self._cancel_event,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.trial_started.connect(self._show_trial)
        self._worker.trial_decoded.connect(self._show_decoded)
        self._worker.trial_quality.connect(self._show_quality)
        self._worker.finished.connect(self._finish)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        QTimer.singleShot(0, self._thread.start)

    @Slot(int, int, str, object)
    def _show_trial(
        self,
        index: int,
        total: int,
        cue: str,
        frequency_hz: object,
    ) -> None:
        self._selected_frequency = None
        self._active_frequency = (
            float(frequency_hz)
            if isinstance(frequency_hz, (str, int, float)) and not isinstance(frequency_hz, bool)
            else None
        )
        self.status_label.setText(f"试次 {index}/{total} · 请保持头部稳定")
        if self.config.task_kind is SignalTaskKind.MI_PROTOCOL:
            direction = "左手" if cue == "left" else "右手"
            self.cue_label.setText(f"想象{direction}运动")
        elif self.config.task_kind is SignalTaskKind.SSVEP_BINARY_COMMUNICATION:
            labels = " / ".join(self.config.target_labels)
            self.cue_label.setText(f"请注视“{labels}”作出选择")
        elif self._active_frequency is not None:
            self.cue_label.setText(f"请注视：{self._active_frequency:g} Hz")

    @Slot(int, object, object)
    def _show_quality(self, index: int, assessment: object, telemetry: object) -> None:
        if not isinstance(assessment, SignalQualityAssessment):
            return
        self._quality_text = "信号质量通过" if assessment.usable else "信号质量未通过"
        battery = telemetry.get("battery_percent") if isinstance(telemetry, dict) else None
        battery_text = f" · 电量 {battery}%" if isinstance(battery, int) else ""
        self.status_label.setText(f"试次 {index} · {self._quality_text}{battery_text}")

    @Slot(int, object, object)
    def _show_decoded(self, index: int, result: object, selected_label: object) -> None:
        if not isinstance(result, DecoderResult):
            return
        self._active_frequency = None
        self._selected_frequency = result.target_frequency_hz
        if result.rejected:
            self.status_label.setText(f"试次 {index} · 未形成可靠选择 · {self._quality_text}")
            self.cue_label.setText(
                "信号质量未通过，请检查电极后重试"
                if result.reject_reason == "signal_quality_failed"
                else "未识别，请放松；系统将自动重试"
            )
        else:
            label = str(selected_label) if selected_label is not None else "已识别"
            self.status_label.setText(f"试次 {index} · 反馈已记录 · {self._quality_text}")
            self.cue_label.setText(f"已识别：{label}")

    def _advance_frame(self) -> None:
        assert self._stimulus is not None
        luminances = frame_luminances(self._stimulus, self._frame_index)
        self._frame_index += 1
        for target, label, luminance in zip(
            self._stimulus.targets,
            self._target_labels,
            luminances,
            strict=True,
        ):
            level = round(25 + luminance * 230)
            if target.frequency_hz == self._selected_frequency:
                border = "#42d392"
            elif target.frequency_hz == self._active_frequency:
                border = "#41d1ff"
            else:
                border = "#385064"
            label.setStyleSheet(
                f"font-size:28px;font-weight:800;color:rgb({level},{level},{level});"
                f"background:#102334;border:6px solid {border};border-radius:20px"
            )

    @Slot(int, str, str)
    def _finish(self, exit_code: int, message: str, result_path: str) -> None:
        self._frame_timer.stop()
        self._thread.quit()
        if not self._thread.wait(2_000):
            exit_code = 1
            message = "Signal task worker did not stop cleanly."
        self.status_label.setText("任务已完成" if exit_code == 0 else "任务未完成")
        self.cue_label.setText("请休息" if exit_code == 0 else message)
        self.completed.emit(exit_code, message, result_path)
        self._closing_after_finish = True
        QTimer.singleShot(900, self.close)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closing_after_finish and self._thread.isRunning():
            self._cancel_event.set()
            event.ignore()
            self.status_label.setText("正在安全停止任务…")
            return
        self._frame_timer.stop()
        super().closeEvent(event)
