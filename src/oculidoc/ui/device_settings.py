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
from oculidoc.integrations.legacy_tobii_tools import (
    open_eye_position,
    open_gaze_collect_player,
)

_SOURCE_ITEMS = (
    ("自动检测传感器（推荐）", "auto"),
    ("模拟模式（仅工程测试）", "mock"),
    ("Tobii Eye Tracker 5（原生 Stream Engine）", "tobii_stream_engine"),
    ("兼容桥接（第三方/自制传感器）", "tobii_legacy_bridge"),
    ("GazeCollect / HPF 旧系统兼容", "gaze_collect_legacy"),
    ("JustNeedToSee 内置 Tobii DLL 兼容", "just_need_to_see_bundle"),
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

        self.bridge_host_edit = QLineEdit(current.tobii_bridge_host)
        self.bridge_host_edit.setPlaceholderText("通常为 127.0.0.1")
        self.bridge_port_spin = QSpinBox()
        self.bridge_port_spin.setRange(1, 65_535)
        self.bridge_port_spin.setValue(current.tobii_bridge_port)
        bridge_row = QHBoxLayout()
        bridge_row.addWidget(self.bridge_host_edit, 1)
        bridge_row.addWidget(QLabel("端口"))
        bridge_row.addWidget(self.bridge_port_spin)
        self.bridge_controls = (self.bridge_host_edit, self.bridge_port_spin)

        self.gaze_collect_json_edit = QLineEdit(str(current.gaze_collect_json_root))
        self.gaze_collect_json_edit.setObjectName("gazeCollectJsonRootEdit")
        gaze_collect_json_browse = QPushButton("浏览…")
        gaze_collect_json_browse.clicked.connect(self._browse_gaze_collect_json)
        gaze_collect_json_row = QHBoxLayout()
        gaze_collect_json_row.addWidget(self.gaze_collect_json_edit, 1)
        gaze_collect_json_row.addWidget(gaze_collect_json_browse)

        self.gaze_collect_player_edit = QLineEdit(
            str(current.gaze_collect_player_executable or "")
        )
        self.gaze_collect_player_edit.setObjectName("gazeCollectPlayerEdit")
        gaze_collect_player_browse = QPushButton("浏览…")
        gaze_collect_player_browse.clicked.connect(self._browse_gaze_collect_player)
        self.open_gaze_collect_button = QPushButton("打开 HPF")
        self.open_gaze_collect_button.setObjectName("openGazeCollectPlayerButton")
        self.open_gaze_collect_button.clicked.connect(self._open_gaze_collect_player)
        gaze_collect_player_row = QHBoxLayout()
        gaze_collect_player_row.addWidget(self.gaze_collect_player_edit, 1)
        gaze_collect_player_row.addWidget(gaze_collect_player_browse)
        gaze_collect_player_row.addWidget(self.open_gaze_collect_button)
        self.gaze_collect_controls = (
            self.gaze_collect_json_edit,
            gaze_collect_json_browse,
            self.gaze_collect_player_edit,
            gaze_collect_player_browse,
            self.open_gaze_collect_button,
        )

        self.eye_position_path_edit = QLineEdit(str(current.eye_position_executable or ""))
        self.eye_position_path_edit.setObjectName("eyePositionPathEdit")
        eye_position_browse = QPushButton("浏览…")
        eye_position_browse.clicked.connect(self._browse_eye_position)
        open_eye_position_button = QPushButton("打开眼位检查")
        open_eye_position_button.setObjectName("openEyePositionButton")
        open_eye_position_button.clicked.connect(self._open_legacy_eye_position)
        eye_position_row = QHBoxLayout()
        eye_position_row.addWidget(self.eye_position_path_edit, 1)
        eye_position_row.addWidget(eye_position_browse)
        eye_position_row.addWidget(open_eye_position_button)

        self.just_need_to_see_root_edit = QLineEdit(str(current.just_need_to_see_root))
        self.just_need_to_see_root_edit.setObjectName("justNeedToSeeRootEdit")
        just_need_to_see_browse = QPushButton("浏览…")
        just_need_to_see_browse.clicked.connect(self._browse_just_need_to_see_root)
        just_need_to_see_row = QHBoxLayout()
        just_need_to_see_row.addWidget(self.just_need_to_see_root_edit, 1)
        just_need_to_see_row.addWidget(just_need_to_see_browse)
        self.just_need_to_see_controls = (
            self.just_need_to_see_root_edit,
            just_need_to_see_browse,
        )

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
        form.addRow("兼容桥接地址：", bridge_row)
        form.addRow("GazeCollect JSON：", gaze_collect_json_row)
        form.addRow("HPFMediaPlayer：", gaze_collect_player_row)
        form.addRow("EyePosition：", eye_position_row)
        form.addRow("JustNeedToSee 目录：", just_need_to_see_row)
        form.addRow("任务前预检：", self.preflight_seconds_spin)
        form.addRow("最低有效率：", self.minimum_validity_spin)
        root.addLayout(form)

        source_tip = QLabel(
            "自动模式会依次检测 Tobii 原生驱动和兼容桥接，绝不回退到模拟数据。"
            "第三方或自制传感器需要其程序在上述地址输出换行分隔 JSON，"
            "至少包含归一化 x、y 和 valid；仅插入 USB/串口设备无法推断视线坐标。\n"
            "GazeCollect 模式只读取 HPF 新写入的 JSON，HPF 需由管理员手动打开；"
            "EyePosition 仅辅助摆位。JustNeedToSee DLL 模式使用前必须关闭 JustNeedToSee.exe。"
        )
        source_tip.setWordWrap(True)
        source_tip.setStyleSheet("color:#5a7184;")
        root.addWidget(source_tip)

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
        source = self.source_combo.currentData()
        enabled = source in {"auto", "tobii_stream_engine"}
        for dll_widget in self.dll_controls:
            dll_widget.setEnabled(enabled)
        bridge_enabled = source in {"auto", "tobii_legacy_bridge"}
        for bridge_widget in self.bridge_controls:
            bridge_widget.setEnabled(bridge_enabled)
        gaze_collect_enabled = source == "gaze_collect_legacy"
        for gaze_collect_widget in self.gaze_collect_controls:
            gaze_collect_widget.setEnabled(gaze_collect_enabled)
        just_need_to_see_enabled = source == "just_need_to_see_bundle"
        for just_need_to_see_widget in self.just_need_to_see_controls:
            just_need_to_see_widget.setEnabled(just_need_to_see_enabled)

    def _browse_gaze_collect_json(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择 GazeCollect JSON 目录",
            self.gaze_collect_json_edit.text(),
        )
        if path:
            self.gaze_collect_json_edit.setText(path)

    def _browse_gaze_collect_player(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 HPFMediaPlayer.exe",
            self.gaze_collect_player_edit.text(),
            "HPFMediaPlayer (HPFMediaPlayer.exe);;Windows 程序 (*.exe)",
        )
        if path:
            self.gaze_collect_player_edit.setText(path)

    def _browse_eye_position(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 EyePosition.exe",
            self.eye_position_path_edit.text(),
            "Windows 程序 (*.exe)",
        )
        if path:
            self.eye_position_path_edit.setText(path)

    def _browse_just_need_to_see_root(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择 JustNeedToSee 目录",
            self.just_need_to_see_root_edit.text(),
        )
        if path:
            self.just_need_to_see_root_edit.setText(path)

    def _open_gaze_collect_player(self) -> None:
        try:
            open_gaze_collect_player(self.gaze_collect_player_edit.text())
        except (OSError, RuntimeError) as error:
            QMessageBox.warning(self, "无法打开 HPFMediaPlayer", str(error))

    def _open_legacy_eye_position(self) -> None:
        try:
            open_eye_position(self.eye_position_path_edit.text())
        except (OSError, RuntimeError) as error:
            QMessageBox.warning(self, "无法打开旧版眼位检查", str(error))

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
        player_text = self.gaze_collect_player_edit.text().strip()
        eye_position_text = self.eye_position_path_edit.text().strip()
        gaze_collect_json_text = (
            self.gaze_collect_json_edit.text().strip() or str(self.settings.gaze_collect_json_root)
        )
        just_need_to_see_root_text = (
            self.just_need_to_see_root_edit.text().strip()
            or str(self.settings.just_need_to_see_root)
        )
        return GazeDeviceConfig(
            gaze_source=self.source_combo.currentData(),
            tobii_stream_engine_dll=Path(dll_text) if dll_text else None,
            tobii_bridge_host=self.bridge_host_edit.text().strip(),
            tobii_bridge_port=self.bridge_port_spin.value(),
            gaze_collect_json_root=Path(gaze_collect_json_text),
            gaze_collect_player_executable=Path(player_text) if player_text else None,
            eye_position_executable=(Path(eye_position_text) if eye_position_text else None),
            just_need_to_see_root=Path(just_need_to_see_root_text),
            gaze_preflight_seconds=self.preflight_seconds_spin.value(),
            gaze_minimum_valid_ratio=self.minimum_validity_spin.value() / 100.0,
        )

    def _save(self) -> None:
        config = self.build_config()
        if config.gaze_source in {"auto", "tobii_legacy_bridge"}:
            if not config.tobii_bridge_host.strip():
                QMessageBox.warning(
                    self,
                    "兼容桥接地址无效",
                    "请输入第三方/自制传感器桥接程序的主机地址。",
                )
                return
        if config.gaze_source == "tobii_stream_engine" and config.tobii_stream_engine_dll:
            if not config.tobii_stream_engine_dll.is_file():
                QMessageBox.warning(
                    self,
                    "DLL 路径无效",
                    "所选 tobii_stream_engine.dll 不存在；可清空路径后使用自动发现。",
                )
                return
        if config.gaze_source == "gaze_collect_legacy":
            if not config.gaze_collect_json_root.is_dir():
                QMessageBox.warning(
                    self,
                    "GazeCollect JSON 目录无效",
                    "请选择 HPF 正在写入 *_gaze.json 的目录。",
                )
                return
        if config.gaze_source == "just_need_to_see_bundle":
            bundled_dll = config.just_need_to_see_root / "tobii_stream_engine.dll"
            if not bundled_dll.is_file():
                QMessageBox.warning(
                    self,
                    "JustNeedToSee 目录无效",
                    "所选目录中没有 tobii_stream_engine.dll。",
                )
                return

        self.store.save(config)
        self.accept()
