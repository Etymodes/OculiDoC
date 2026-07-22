"""Administrator dialog for persisted gaze-device settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.config import GazeDeviceConfig, GazeDeviceConfigStore, Settings
from oculidoc.devices.preflight import GazePreflightResult
from oculidoc.devices.tobii_stream_engine import discover_tobii_stream_engine_dll

_SOURCE_ITEMS = (
    ("模拟模式（仅工程测试）", "mock"),
    ("Tobii Eye Tracker 5（原生 Stream Engine）", "tobii_stream_engine"),
    ("Tobii 兼容桥接", "tobii_legacy_bridge"),
)


def _find_tobii_shortcut(pattern: str) -> Path | None:
    roots = []

    for name in ("APPDATA", "ProgramData"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value) / "Microsoft" / "Windows" / "Start Menu" / "Programs")

    for root in roots:
        if not root.is_dir():
            continue

        try:
            matches = sorted(root.rglob(pattern))
        except OSError:
            continue

        if matches:
            return matches[0]

    return None


def find_tobii_experience_shortcut() -> Path | None:
    """Find an installed Start-menu shortcut without relying on a private app ID."""
    return _find_tobii_shortcut("*Tobii*Experience*.lnk")


def find_tobii_ghost_shortcut() -> Path | None:
    """Find Tobii Ghost in either the user or system Start menu."""
    return _find_tobii_shortcut("*Tobii*Ghost*.lnk")


class DeviceSettingsDialog(QDialog):
    """Edit the next-task device source and preflight policy."""

    def __init__(
        self,
        settings: Settings,
        store: GazeDeviceConfigStore,
        latest_preflight: GazePreflightResult | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.setWindowTitle("眼动设备设置")
        self.setMinimumWidth(720)

        current = store.load(GazeDeviceConfig.from_settings(settings))
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.source_combo = QComboBox()
        for label, value in _SOURCE_ITEMS:
            self.source_combo.addItem(label, value)
        source_index = self.source_combo.findData(current.gaze_source)
        self.source_combo.setCurrentIndex(max(0, source_index))
        self.source_combo.currentIndexChanged.connect(self._update_source_controls)

        self.dll_path_edit = QLineEdit(str(current.tobii_stream_engine_dll or ""))
        self.dll_path_edit.setPlaceholderText("留空时由 OculiDoC 自动发现")
        browse_button = QPushButton("浏览…")
        browse_button.clicked.connect(self._browse_dll)
        discover_button = QPushButton("自动发现")
        discover_button.clicked.connect(self._discover_dll)
        dll_row = QHBoxLayout()
        dll_row.addWidget(self.dll_path_edit, 1)
        dll_row.addWidget(browse_button)
        dll_row.addWidget(discover_button)
        self.dll_controls = (self.dll_path_edit, browse_button, discover_button)

        self.preflight_seconds_spin = QSpinBox()
        self.preflight_seconds_spin.setRange(3, 10)
        self.preflight_seconds_spin.setSuffix(" 秒")
        self.preflight_seconds_spin.setValue(current.gaze_preflight_seconds)

        self.minimum_validity_spin = QDoubleSpinBox()
        self.minimum_validity_spin.setRange(0, 100)
        self.minimum_validity_spin.setDecimals(0)
        self.minimum_validity_spin.setSuffix(" %")
        self.minimum_validity_spin.setValue(current.gaze_minimum_valid_ratio * 100)

        form.addRow("眼动源：", self.source_combo)
        form.addRow("Stream Engine DLL：", dll_row)
        form.addRow("任务前预检：", self.preflight_seconds_spin)
        form.addRow("最低有效率：", self.minimum_validity_spin)
        root.addLayout(form)

        self.preflight_label = QLabel(self._preflight_text(latest_preflight))
        self.preflight_label.setWordWrap(True)
        self.preflight_label.setStyleSheet(
            "background:#f3f6f8; border:1px solid #d9e3ec; border-radius:8px; "
            "padding:10px; font-size:14px;"
        )
        root.addWidget(self.preflight_label)

        calibration_row = QHBoxLayout()
        open_tobii_button = QPushButton("打开 Tobii Experience / 校准")
        open_tobii_button.clicked.connect(self._open_tobii_experience)
        calibration_tip = QLabel(
            "正式任务前请先完成 Display Setup，再在用户资料中执行校准或 Improve calibration。"
        )
        calibration_tip.setWordWrap(True)
        calibration_row.addWidget(open_tobii_button)
        calibration_row.addWidget(calibration_tip, 1)
        root.addLayout(calibration_row)

        ghost_row = QHBoxLayout()
        open_ghost_button = QPushButton("打开 Tobii Ghost / 视线检查")
        open_ghost_button.setObjectName("openTobiiGhostButton")
        open_ghost_button.clicked.connect(self._open_tobii_ghost)
        ghost_tip = QLabel("在正式任务前显示视线气泡，辅助管理员检查实时目光追踪。")
        ghost_tip.setWordWrap(True)
        ghost_row.addWidget(open_ghost_button)
        ghost_row.addWidget(ghost_tip, 1)
        root.addLayout(ghost_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_source_controls()

    @staticmethod
    def _preflight_text(result: GazePreflightResult | None) -> str:
        if result is None:
            return "尚无设备预检结果。每次正式任务开始前会自动执行。"

        details = result.status_text()
        if result.device_url:
            details += f"\n设备 URL：{result.device_url}"
        if result.library_path:
            details += f"\nDLL：{result.library_path}"
        return details

    def _update_source_controls(self) -> None:
        enabled = self.source_combo.currentData() == "tobii_stream_engine"
        for widget in self.dll_controls:
            widget.setEnabled(enabled)

    def _browse_dll(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Tobii Stream Engine DLL",
            self.dll_path_edit.text(),
            "Tobii Stream Engine (tobii_stream_engine.dll);;DLL (*.dll)",
        )
        if path:
            self.dll_path_edit.setText(path)

    def _discover_dll(self) -> None:
        discovered = discover_tobii_stream_engine_dll(self.dll_path_edit.text().strip() or None)
        if discovered is None:
            QMessageBox.warning(
                self,
                "未找到 Stream Engine",
                "未找到 tobii_stream_engine.dll。请确认 Tobii Experience 和驱动已安装。",
            )
            return
        self.dll_path_edit.setText(str(discovered))

    def _open_tobii_experience(self) -> None:
        if sys.platform != "win32":
            QMessageBox.information(
                self,
                "Tobii Experience",
                "Tobii Experience 需要在连接 Eye Tracker 5 的 Windows 电脑上打开。",
            )
            return

        shortcut = find_tobii_experience_shortcut()
        opened = (
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(shortcut)))
            if shortcut is not None
            else QDesktopServices.openUrl(QUrl("ms-search:query=Tobii%20Experience"))
        )
        if not opened:
            QMessageBox.warning(
                self,
                "无法打开 Tobii Experience",
                "请从 Windows 开始菜单手动搜索 Tobii Experience。",
            )

    def _open_tobii_ghost(self) -> None:
        if sys.platform != "win32":
            QMessageBox.information(
                self,
                "Tobii Ghost",
                "Tobii Ghost 需要在连接眼动仪的 Windows 电脑上打开。",
            )
            return

        shortcut = find_tobii_ghost_shortcut()
        opened = (
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(shortcut)))
            if shortcut is not None
            else QDesktopServices.openUrl(QUrl("ms-search:query=Tobii%20Ghost"))
        )

        if not opened:
            QMessageBox.warning(
                self,
                "无法打开 Tobii Ghost",
                "请从 Windows 开始菜单手动搜索 Tobii Ghost。",
            )

    def build_config(self) -> GazeDeviceConfig:
        dll_text = self.dll_path_edit.text().strip()
        return GazeDeviceConfig(
            gaze_source=self.source_combo.currentData(),
            tobii_stream_engine_dll=Path(dll_text) if dll_text else None,
            gaze_preflight_seconds=self.preflight_seconds_spin.value(),
            gaze_minimum_valid_ratio=self.minimum_validity_spin.value() / 100.0,
        )

    def _save(self) -> None:
        config = self.build_config()
        if config.gaze_source == "tobii_stream_engine" and config.tobii_stream_engine_dll:
            if not config.tobii_stream_engine_dll.is_file():
                QMessageBox.warning(
                    self,
                    "DLL 路径无效",
                    "所选 tobii_stream_engine.dll 不存在；可清空路径后使用自动发现。",
                )
                return

        self.store.save(config)
        self.accept()
