"""Responsive installation of the optional ACIES vision runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from oculidoc.devices.webcam_setup import (
    acies_installer_command,
    acies_runtime_configured,
    ensure_webcam_config,
)


class AciesSetupDialog(QDialog):
    """Run downloads and native-module verification outside the GUI thread."""

    def __init__(
        self,
        data_dir: Path,
        config_path: Path,
        camera_index: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_dir = data_dir
        self.config_path = config_path
        self.camera_index = camera_index
        self.succeeded = False
        self._running = False
        self._log_path: Path | None = None
        self.setWindowTitle("安装 / 修复 ACIES")
        self.resize(700, 420)
        layout = QVBoxLayout(self)
        self.status_label = QLabel(
            "将从 Python 官方、PyPI 和 Google 模型地址下载 ACIES 所需组件。"
            "依赖保存在当前 OculiDoC 数据目录，不要求系统预装 Python。"
            "下载和模型验证可能需要数分钟；现有校准数据会保留。"
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(2000)
        layout.addWidget(self.output, 1)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("开始安装 / 修复")
        self.start_button.clicked.connect(self._start)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self._close_result)
        buttons.addWidget(self.start_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)

    def _append(self, text: str) -> None:
        self.output.appendPlainText(text.rstrip())
        if self._log_path is not None:
            try:
                with self._log_path.open("a", encoding="utf-8") as stream:
                    stream.write(text + ("" if text.endswith("\n") else "\n"))
            except OSError:
                # A log failure must not hide the installation result shown in the dialog.
                self._log_path = None

    def _start(self) -> None:
        if self._running:
            return
        self.succeeded = False
        try:
            ensure_webcam_config(self.config_path, self.camera_index)
            command = acies_installer_command(self.data_dir, self.config_path, self.camera_index)
            log_dir = self.data_dir / "runtime" / "webcam"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            self._log_path = log_dir / f"setup-{stamp}.log"
        except Exception as error:
            self.status_label.setText(f"安装尚未开始：{error}")
            return
        self._running = True
        self.start_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.status_label.setText("正在下载、安装并验证 ACIES。请保持联网，等待下方结果。")
        self.process.start(command[0], command[1:])

    def _read_output(self) -> None:
        text = bytes(self.process.readAllStandardOutput().data()).decode("utf-8", errors="replace")
        if text:
            self._append(text)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(False, f"无法启动安装程序：{self.process.errorString()}")

    def _finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._read_output()
        success = (
            exit_status == QProcess.ExitStatus.NormalExit
            and exit_code == 0
            and acies_runtime_configured(self.config_path)
        )
        self._complete(
            success,
            "ACIES 已安装并通过依赖与模型初始化检查。可以校准当前受试者。"
            if success
            else "安装未完成。请查看下方具体错误后再次安装 / 修复。配置与既有校准记录已保留。",
        )

    def _complete(self, success: bool, message: str) -> None:
        self._running = False
        self.succeeded = success
        self.status_label.setText(message)
        self.start_button.setEnabled(not success)
        self.close_button.setEnabled(True)
        if self._log_path is not None:
            self._append(f"安装记录：{self._log_path}")

    def _close_result(self) -> None:
        if self.succeeded:
            self.accept()
        else:
            self.reject()

    def reject(self) -> None:
        if not self._running:
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._running:
            event.ignore()
        else:
            super().closeEvent(event)
