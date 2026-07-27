"""OculiDoC-native live gaze connection and validity check."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic

from PySide6.QtGui import (
    QCloseEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from oculidoc.config import Settings
from oculidoc.devices.contracts import EyeTrackerSample
from oculidoc.devices.preflight import (
    GazePreflightResult,
    GazePreflightStore,
    observed_sample_capabilities,
)
from oculidoc.tasks.gaze_stream import GazeStreamWorker


class GazeSelfCheckDialog(QDialog):
    """Connect to the selected source and show live sampling validity."""

    def __init__(
        self,
        settings: Settings,
        parent: QWidget | None = None,
        *,
        preflight_store: GazePreflightStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("OculiDoC 视线自检")
        self.resize(720, 260)

        root = QVBoxLayout(self)
        explanation = QLabel(
            "检查当前眼动源能否持续输出实时样本，并显示采样率和有效率。"
            "主观眼位请单独打开 OpoinThesis。"
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        self.connection_label = QLabel("准备连接…")
        self.connection_label.setStyleSheet("font-size:18px; font-weight:700;")
        root.addWidget(self.connection_label)

        self.metrics_label = QLabel("采样率：— · 有效率：—")
        self.metrics_label.setStyleSheet("font-size:17px; color:#38566f;")
        root.addWidget(self.metrics_label)

        self.capability_label = QLabel("实际采集字段：等待自检…")
        self.capability_label.setWordWrap(True)
        self.capability_label.setStyleSheet("font-size:16px; color:#38566f;")
        root.addWidget(self.capability_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)

        self._sample_count = 0
        self._valid_count = 0
        self._observed_capabilities: set[str] = set()
        self._started_at = monotonic()
        self.preflight_result: GazePreflightResult | None = None
        self._worker = GazeStreamWorker(
            settings,
            self,
            preflight_seconds=float(settings.gaze_preflight_seconds),
            preflight_store=preflight_store,
        )
        self._worker.status_changed.connect(self._set_connection_status)
        self._worker.preflight_completed.connect(self._consume_preflight)
        self._worker.sample_received.connect(self._consume_sample)
        self._worker.stream_error.connect(self._show_error)
        self._started = False

    def _set_connection_status(self, text: str) -> None:
        self.connection_label.setText(text)

    def _consume_sample(self, sample: EyeTrackerSample) -> None:
        self._sample_count += 1
        self._valid_count += int(sample.gaze_valid)
        self._observed_capabilities.update(observed_sample_capabilities(sample))
        elapsed = max(monotonic() - self._started_at, 0.001)
        self.metrics_label.setText(
            f"采样率：{self._sample_count / elapsed:.0f} Hz"
            f" · 有效率：{self._valid_count / self._sample_count:.0%}"
        )
        self._refresh_capabilities()

    def _consume_preflight(self, result: GazePreflightResult) -> None:
        self.preflight_result = result
        self._sample_count = result.sample_count
        self._valid_count = result.valid_sample_count
        self._observed_capabilities.update(result.observed_capabilities)
        self._started_at = monotonic() - result.duration_seconds
        self.metrics_label.setText(
            f"采样率：{result.sample_rate_hz:.0f} Hz · 有效率：{result.valid_ratio:.0%}"
        )
        self._refresh_capabilities()
        if result.passed:
            self._worker.enable_sample_delivery()

    def _refresh_capabilities(self) -> None:
        if self.preflight_result is None:
            return

        live_result = replace(
            self.preflight_result,
            observed_capabilities=tuple(sorted(self._observed_capabilities)),
        )
        identity = " ".join(
            part
            for part in (
                self.preflight_result.device_manufacturer,
                self.preflight_result.device_model,
            )
            if part
        )
        device_text = self.preflight_result.device_name
        if identity:
            device_text += f"（{identity}）"
        self.capability_label.setText(
            f"实际设备：{device_text}\n{live_result.observed_capability_text()}"
        )

    def _show_error(self, message: str) -> None:
        self.connection_label.setText(f"自检失败：{message}")
        self.connection_label.setStyleSheet("font-size:18px; font-weight:700; color:#b42318;")

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._started:
            self._started = True
            self._started_at = monotonic()
            self._worker.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._worker.stop()
        event.accept()
