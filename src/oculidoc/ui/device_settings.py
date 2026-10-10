"""Administrator dialog for persisted gaze-device settings."""

from __future__ import annotations

import os
import subprocess
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
from oculidoc.devices.acies import ACIES_VERSION, PROFILE_LABELS
from oculidoc.devices.diagnostics import probe_cameras
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.preflight import GazePreflightResult, GazePreflightStore
from oculidoc.devices.tobii_stream_engine import discover_tobii_stream_engine_dll
from oculidoc.devices.webcam_gaze import read_webcam_config, save_webcam_camera_index
from oculidoc.ui.gaze_self_check import GazeSelfCheckDialog
from oculidoc.ui.opoin_thesis import OpoinThesisDialog

_SOURCE_ITEMS = (
    ("Tobii 原生 Stream", "tobii_stream_engine"),
    ("七鑫易维 aSee（本机 SDK 桥）", "seveninvensun_bridge"),
    ("ThinksTracker（120 / 300 Hz）", "thinks_tracker"),
    ("自动：眼动仪 → 摄像头", "auto_webcam"),
    ("ACIES 摄像头识别（需校准）", "webcam"),
    ("工程模拟测试", "mock"),
    ("原监听兼容", "tobii_hospital_bridge"),
    ("Tobii DLL兼容", "just_need_to_see_bundle"),
    ("第三方兼容", "tobii_legacy_bridge"),
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


def find_tobii_experience_app_id() -> str | None:
    """Resolve the registered Start-menu app id used by Store/MSIX installs."""
    if sys.platform != "win32":
        return None

    script = (
        "$app = Get-StartApps | "
        "Where-Object { $_.Name -like '*Tobii*Experience*' } | "
        "Select-Object -First 1; "
        "if ($app) { $app.AppID }"
    )
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return None

    app_id = completed.stdout.strip()
    return app_id if completed.returncode == 0 and app_id else None


def _open_windows_shell_target(target: str | Path) -> bool:
    try:
        subprocess.Popen(
            ["explorer.exe", str(target)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return False
    return True


def launch_tobii_experience() -> bool:
    """Open either a classic shortcut or the registered Store application."""
    shortcut = find_tobii_experience_shortcut()
    if shortcut is not None and _open_windows_shell_target(shortcut):
        return True

    app_id = find_tobii_experience_app_id()
    return bool(
        app_id
        and _open_windows_shell_target(
            rf"shell:AppsFolder\{app_id}",
        )
    )


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
        self._preflight_store = GazePreflightStore(
            settings.data_dir.expanduser() / "runtime" / "gaze_preflight.json"
        )

        current = store.load(GazeDeviceConfig.from_settings(settings))
        self.current_config = current
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.source_combo = QComboBox()
        for label, value in _SOURCE_ITEMS:
            self.source_combo.addItem(label, value)
        visible_source = {
            "auto": "tobii_stream_engine",
            "gaze_collect_legacy": "tobii_legacy_bridge",
        }.get(current.gaze_source, current.gaze_source)
        source_index = self.source_combo.findData(visible_source)
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

        self.third_party_json_edit = QLineEdit()
        self.third_party_json_edit.setObjectName("thirdPartyJsonRootEdit")
        self.third_party_json_edit.setPlaceholderText("留空时沿用已保存的兼容数据目录")
        third_party_json_browse = QPushButton("浏览…")
        third_party_json_browse.clicked.connect(self._browse_third_party_json)
        third_party_json_row = QHBoxLayout()
        third_party_json_row.addWidget(self.third_party_json_edit, 1)
        third_party_json_row.addWidget(third_party_json_browse)
        self.third_party_json_controls = (
            self.third_party_json_edit,
            third_party_json_browse,
        )

        self.compatibility_dll_edit = QLineEdit()
        self.compatibility_dll_edit.setObjectName("compatibilityDllEdit")
        self.compatibility_dll_edit.setPlaceholderText("留空时沿用已保存的 tobii_stream_engine.dll")
        compatibility_dll_browse = QPushButton("浏览…")
        compatibility_dll_browse.clicked.connect(self._browse_compatibility_dll)
        compatibility_dll_row = QHBoxLayout()
        compatibility_dll_row.addWidget(self.compatibility_dll_edit, 1)
        compatibility_dll_row.addWidget(compatibility_dll_browse)
        self.compatibility_dll_controls = (
            self.compatibility_dll_edit,
            compatibility_dll_browse,
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
        self.thinks_config_edit = QLineEdit(str(current.thinks_config_path or ""))
        self.thinks_config_edit.setPlaceholderText("留空使用工程中的 ThinksTracker 配置")
        self.thinks_setup_button = QPushButton("屏幕 / SDK 设置")
        self.thinks_setup_button.clicked.connect(self._setup_thinks)
        self.thinks_calibrate_button = QPushButton("九点校准")
        self.thinks_calibrate_button.clicked.connect(self._calibrate_thinks)
        thinks_row = QHBoxLayout()
        thinks_row.addWidget(self.thinks_config_edit, 1)
        thinks_row.addWidget(self.thinks_setup_button)
        thinks_row.addWidget(self.thinks_calibrate_button)
        form.addRow("ThinksTracker：", thinks_row)
        self.webcam_config_edit = QLineEdit(str(current.webcam_config_path or ""))
        self.webcam_config_edit.setPlaceholderText(
            "留空使用工程 data/runtime/webcam_providers.json"
        )
        form.addRow("摄像头配置：", self.webcam_config_edit)
        self.camera_combo = QComboBox()
        self.camera_combo.setObjectName("aciesCameraCombo")
        self._load_camera_selection()
        self.camera_scan_button = QPushButton("扫描摄像头")
        self.camera_scan_button.clicked.connect(self._scan_webcam_cameras)
        camera_row = QHBoxLayout()
        camera_row.addWidget(self.camera_combo, 1)
        camera_row.addWidget(self.camera_scan_button)
        form.addRow("ACIES 摄像头：", camera_row)
        self.webcam_provider_combo = QComboBox()
        self.webcam_provider_combo.addItem("ACIES · 独立虹膜估计", "acies")
        self.webcam_calibrate_button = QPushButton("校准当前受试者")
        self.webcam_calibrate_button.clicked.connect(self._calibrate_webcam)
        webcam_row = QHBoxLayout()
        webcam_row.addWidget(self.webcam_provider_combo)
        webcam_row.addWidget(self.webcam_calibrate_button)
        self.webcam_history_button = QPushButton("校准记录")
        self.webcam_history_button.clicked.connect(self._open_calibration_records)
        webcam_row.addWidget(self.webcam_history_button)
        self.webcam_experiment_button = QPushButton("对照测试")
        self.webcam_experiment_button.clicked.connect(self._experiment_webcam)
        webcam_row.addWidget(self.webcam_experiment_button)
        form.addRow("摄像头校准：", webcam_row)
        self.webcam_profile_combo = QComboBox()
        for value, label in PROFILE_LABELS.items():
            self.webcam_profile_combo.addItem(label, value)
        self.webcam_profile_combo.setCurrentIndex(
            self.webcam_profile_combo.findData(current.webcam_calibration_profile)
        )
        form.addRow("校准容忍度：", self.webcam_profile_combo)
        form.addRow("原生 Stream DLL：", dll_row)
        form.addRow("本机/兼容桥地址：", bridge_row)
        form.addRow("第三方数据目录：", third_party_json_row)
        form.addRow("Tobii 兼容 DLL：", compatibility_dll_row)
        form.addRow("任务前预检：", self.preflight_seconds_spin)
        form.addRow("最低有效率：", self.minimum_validity_spin)
        root.addLayout(form)

        source_tip = QLabel(
            "这里选择的是连接方式；实际传感器名称和可用数据字段以自检结果为准。"
            "摄像头使用 ACIES 独立估计；必须先为当前受试者在患者屏幕校准。"
            "宽容档允许较大误差，适合粗分区试验；完全闭眼或信息不足时暂停输出。"
            "Tobii、七鑫易维和 ThinksTracker 是并列的可选眼动源。"
            "七鑫易维 aSee 入口只连接本机 SDK 桥，"
            "不内置或猜测厂商 DLL；眼动眼镜必须先把场景坐标映射到当前屏幕。"
            "原监听兼容用于既有 TCP 发送程序；"
            "它由 OculiDoC 在 0.0.0.0 和所设端口（原版为 9999）建立服务端。"
            "Tobii DLL兼容使用管理员明确选择的 Stream Engine DLL；第三方兼容会先尝试"
            "通用 NDJSON 桥接，再尝试所选目录中的 *_gaze.json。兼容模式均不会回退到模拟数据。"
        )
        source_tip.setWordWrap(True)
        source_tip.setStyleSheet("color:#5a7184;")
        root.addWidget(source_tip)

        if latest_preflight is not None and latest_preflight.source != current.gaze_source:
            latest_preflight = None
        if (
            latest_preflight is not None
            and current.gaze_source in {"webcam", "auto_webcam"}
            and "webcam_estimate" in latest_preflight.advertised_capabilities
            and latest_preflight.device_model != f"ACIES {ACIES_VERSION}"
        ):
            latest_preflight = None
        self.preflight_label = QLabel(self._preflight_text(latest_preflight))
        self.preflight_label.setWordWrap(True)
        self.preflight_label.setStyleSheet(
            "background:#f3f6f8; border:1px solid #d9e3ec; border-radius:8px; "
            "padding:10px; font-size:14px;"
        )
        root.addWidget(self.preflight_label)

        self_check_row = QHBoxLayout()
        self.self_check_button = QPushButton("运行自检")
        self.self_check_button.setObjectName("runGazeSelfCheckButton")
        self.self_check_button.clicked.connect(self._open_self_check)
        self_check_tip = QLabel(
            "识别当前连接的实际设备、采样率、有效率及注视点、瞳孔和三维眼位能力。"
        )
        self_check_tip.setWordWrap(True)
        self_check_row.addWidget(self.self_check_button)
        self_check_row.addWidget(self_check_tip, 1)
        root.addLayout(self_check_row)

        opoin_thesis_row = QHBoxLayout()
        self.opoin_thesis_button = QPushButton("打开 OpoinThesis 眼位监测")
        self.opoin_thesis_button.setObjectName("openOpoinThesisButton")
        self.opoin_thesis_button.clicked.connect(self._open_opoin_thesis)
        self.opoin_thesis_tip = QLabel(
            "主观查看左右眼位置；不计算有效率，不保存，也不纳入自检或任务报告。"
        )
        self.opoin_thesis_tip.setWordWrap(True)
        opoin_thesis_row.addWidget(self.opoin_thesis_button)
        opoin_thesis_row.addWidget(self.opoin_thesis_tip, 1)
        root.addLayout(opoin_thesis_row)

        calibration_row = QHBoxLayout()
        self.open_tobii_button = QPushButton("打开 Tobii Experience / 校准")
        self.open_tobii_button.setObjectName("openTobiiExperienceButton")
        self.open_tobii_button.clicked.connect(self._open_tobii_experience)
        self.calibration_tip = QLabel(
            "正式任务前请先完成 Display Setup，再在用户资料中执行校准或 Improve calibration。"
        )
        self.calibration_tip.setWordWrap(True)
        calibration_row.addWidget(self.open_tobii_button)
        calibration_row.addWidget(self.calibration_tip, 1)
        root.addLayout(calibration_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_source_controls()
        self.source_combo.currentIndexChanged.connect(self._clear_preflight_display)
        self.webcam_profile_combo.currentIndexChanged.connect(self._clear_preflight_display)
        self.webcam_config_edit.textChanged.connect(self._clear_preflight_display)
        self.webcam_config_edit.editingFinished.connect(self._load_camera_selection)
        self.camera_combo.currentIndexChanged.connect(self._camera_selection_changed)
        self.thinks_config_edit.textChanged.connect(self._clear_preflight_display)

    def _clear_preflight_display(self, *_: object) -> None:
        self.preflight_label.setText("设置已变更，请完成校准后重新运行自检。")

    @staticmethod
    def _preflight_text(result: GazePreflightResult | None) -> str:
        if result is None:
            return "尚无设备预检结果。每次正式任务开始前会自动执行。"

        details = result.status_text()
        identity = " ".join(
            part for part in (result.device_manufacturer, result.device_model) if part
        )
        if identity:
            details += f"\n实际设备：{identity}"
        if result.device_url:
            details += f"\n设备 URL：{result.device_url}"
        if result.library_path:
            details += f"\nDLL：{result.library_path}"
        return details

    def _update_source_controls(self) -> None:
        source = self.source_combo.currentData()
        for thinks_control in (
            self.thinks_config_edit,
            self.thinks_setup_button,
            self.thinks_calibrate_button,
        ):
            thinks_control.setEnabled(source == "thinks_tracker")
        webcam_selected = source in {"webcam", "auto_webcam"}
        for control in (
            self.webcam_config_edit,
            self.camera_combo,
            self.camera_scan_button,
            self.webcam_provider_combo,
            self.webcam_calibrate_button,
            self.webcam_experiment_button,
            self.webcam_profile_combo,
        ):
            control.setEnabled(webcam_selected)
        enabled = source == "tobii_stream_engine"
        for dll_widget in self.dll_controls:
            dll_widget.setEnabled(enabled)
        self.bridge_host_edit.setEnabled(source in {"seveninvensun_bridge", "tobii_legacy_bridge"})
        self.bridge_port_spin.setEnabled(
            source
            in {
                "seveninvensun_bridge",
                "tobii_hospital_bridge",
                "tobii_legacy_bridge",
            }
        )
        third_party_enabled = source == "tobii_legacy_bridge"
        for widget in self.third_party_json_controls:
            widget.setEnabled(third_party_enabled)
        compatibility_enabled = source == "just_need_to_see_bundle"
        for widget in self.compatibility_dll_controls:
            widget.setEnabled(compatibility_enabled)
        seveninvensun_selected = source == "seveninvensun_bridge"
        self.opoin_thesis_button.setEnabled(not seveninvensun_selected)
        self.open_tobii_button.setEnabled(not seveninvensun_selected)
        self.opoin_thesis_tip.setText(
            "当前七鑫易维桥契约只接收屏幕注视点，不声明三维眼位能力。"
            if seveninvensun_selected
            else "主观查看左右眼位置；不计算有效率，不保存，也不纳入自检或任务报告。"
        )
        self.calibration_tip.setText(
            "请先在七鑫易维随设备提供的工具中完成当前用户和当前屏幕校准，再运行自检。"
            if seveninvensun_selected
            else "正式任务前请先完成 Display Setup，再在用户资料中执行校准或 Improve calibration。"
        )
        if webcam_selected:
            self.calibration_tip.setText(
                "摄像头估计仅供研究。更换受试者、摄像头、屏幕或姿势后请重新校准，再运行自检。"
            )
            self.opoin_thesis_button.setEnabled(False)
            self.open_tobii_button.setEnabled(False)
        if source == "thinks_tracker":
            self.open_tobii_button.setEnabled(False)
            self.opoin_thesis_button.setEnabled(False)
            self.opoin_thesis_tip.setText("ThinksTracker 当前接入屏幕注视点，不声明三维眼位能力。")
            self.calibration_tip.setText(
                "先关闭厂家测试程序，确认屏幕和安装参数，再为当前受试者完成九点校准与自检。"
            )

    def _webcam_config_path(self) -> Path:
        entered = self.webcam_config_edit.text().strip()
        return Path(entered) if entered else self.settings.webcam_providers_path

    def _load_camera_selection(self) -> None:
        try:
            index = int(read_webcam_config(self._webcam_config_path()).get("camera_index", 0))
            label = f"摄像头 {index}（当前配置，点击扫描确认）"
        except DeviceConnectionError:
            index = 0
            label = "摄像头 0（配置文件不可用）"
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        self.camera_combo.addItem(label, index)
        self.camera_combo.blockSignals(False)
        self._camera_choice_dirty = False

    def _camera_selection_changed(self, *_: object) -> None:
        self._camera_choice_dirty = True
        self._clear_preflight_display()

    def _scan_webcam_cameras(self) -> None:
        # The runtime worker uses the same DirectShow backend on Windows.
        try:
            results = probe_cameras(6)
        except Exception as error:
            QMessageBox.warning(self, "扫描摄像头失败", str(error))
            return
        selected = self.camera_combo.currentData()
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        for result in results:
            state = "可用" if result.available else "未找到或不可读取"
            size = (
                f" · {result.width_px}×{result.height_px}"
                if result.width_px and result.height_px
                else ""
            )
            self.camera_combo.addItem(f"摄像头 {result.index} · {state}{size}", result.index)
        if isinstance(selected, int) and self.camera_combo.findData(selected) < 0:
            self.camera_combo.addItem(f"摄像头 {selected}（当前配置，未扫描）", selected)
        self.camera_combo.setCurrentIndex(max(0, self.camera_combo.findData(selected)))
        self.camera_combo.blockSignals(False)

    def _persist_webcam_camera_selection(self) -> bool:
        if not self._camera_choice_dirty:
            return True
        index = self.camera_combo.currentData()
        if type(index) is not int:
            QMessageBox.warning(self, "摄像头选择无效", "请先扫描并选择摄像头。")
            return False
        path = self._webcam_config_path()
        try:
            current = read_webcam_config(path)
            if current.get("camera_index", 0) != index:
                save_webcam_camera_index(path, index)
        except (DeviceConnectionError, OSError, ValueError) as error:
            QMessageBox.warning(self, "无法保存摄像头", str(error))
            return False
        self._camera_choice_dirty = False
        return True

    def _setup_thinks(self) -> None:
        from oculidoc.ui.thinks_calibration import ThinksSetupDialog

        dialog = ThinksSetupDialog(self.build_config().apply(self.settings), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._clear_preflight_display()

    def _calibrate_thinks(self) -> None:
        from oculidoc.ui.thinks_calibration import ThinksCalibrationDialog

        dialog = ThinksCalibrationDialog(self.build_config().apply(self.settings), self)
        dialog.start()
        if dialog.result() == QDialog.DialogCode.Accepted:
            self.preflight_label.setText("ThinksTracker 九点校准已保存，请运行自检并保存设置。")

    def _calibrate_webcam(self) -> None:
        from oculidoc.ui.webcam_calibration import WebcamCalibrationDialog

        if not self._persist_webcam_camera_selection():
            return
        config = self.build_config()
        dialog = WebcamCalibrationDialog(
            config.apply(self.settings), self.webcam_provider_combo.currentData(), self
        )
        dialog.start()
        if dialog.result() == QDialog.DialogCode.Accepted:
            self.preflight_label.setText(dialog.summary_text + "\n请运行自检并保存设置。")

    def _open_calibration_records(self) -> None:
        directory = self.settings.data_dir / "runtime" / "webcam" / "calibration_attempts"
        if not directory.is_dir():
            QMessageBox.information(self, "校准记录", "尚无校准记录。按空格开始校准后自动记录。")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve()))):
            QMessageBox.information(self, "校准记录", f"记录保存在：\n{directory}")

    def _experiment_webcam(self) -> None:
        from oculidoc.ui.acies_experiment import AciesExperimentDialog

        if not self._persist_webcam_camera_selection():
            return
        dialog = AciesExperimentDialog(self.build_config().apply(self.settings), self)
        dialog.start()

    def _browse_third_party_json(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择第三方眼动 JSON 目录",
            self.third_party_json_edit.text(),
        )
        if path:
            self.third_party_json_edit.setText(path)

    def _browse_compatibility_dll(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Tobii 兼容 DLL",
            self.compatibility_dll_edit.text(),
            "Tobii Stream Engine (tobii_stream_engine.dll);;DLL (*.dll)",
        )
        if path:
            self.compatibility_dll_edit.setText(path)

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

        if not launch_tobii_experience():
            QMessageBox.warning(
                self,
                "无法打开 Tobii Experience",
                "Windows 未找到已注册的 Tobii Experience。请确认软件已安装并能从开始菜单打开。",
            )

    def _open_self_check(self, checked: bool = False) -> None:
        del checked
        config = self.build_config()
        if not self._validate_config(config):
            return
        if config.gaze_source in {"webcam", "auto_webcam"}:
            if not self._persist_webcam_camera_selection():
                return
        applied_settings = config.apply(self.settings)
        GazeSelfCheckDialog(
            applied_settings,
            self,
            preflight_store=self._preflight_store,
        ).exec()
        result = self._preflight_store.load()
        if result is not None and result.source == applied_settings.gaze_source:
            self.preflight_label.setText(self._preflight_text(result))

    def _open_opoin_thesis(self, checked: bool = False) -> None:
        del checked
        config = self.build_config()
        if not self._validate_config(config):
            return
        applied_settings = config.apply(self.settings)
        result = self._preflight_store.load()
        if result is not None and result.source != applied_settings.gaze_source:
            result = None
        OpoinThesisDialog(
            applied_settings,
            self,
            preflight_result=result,
        ).exec()

    def build_config(self) -> GazeDeviceConfig:
        dll_text = self.dll_path_edit.text().strip()
        third_party_json_text = self.third_party_json_edit.text().strip() or str(
            self.current_config.gaze_collect_json_root
        )
        compatibility_dll_text = self.compatibility_dll_edit.text().strip()
        compatibility_root = (
            Path(compatibility_dll_text).parent
            if compatibility_dll_text
            else self.current_config.just_need_to_see_root
        )
        return GazeDeviceConfig(
            gaze_source=self.source_combo.currentData(),
            thinks_config_path=Path(self.thinks_config_edit.text().strip())
            if self.thinks_config_edit.text().strip()
            else None,
            webcam_calibration_profile=self.webcam_profile_combo.currentData(),
            webcam_config_path=Path(self.webcam_config_edit.text().strip())
            if self.webcam_config_edit.text().strip()
            else None,
            tobii_stream_engine_dll=Path(dll_text) if dll_text else None,
            tobii_bridge_host=self.bridge_host_edit.text().strip(),
            tobii_bridge_port=self.bridge_port_spin.value(),
            gaze_collect_json_root=Path(third_party_json_text),
            gaze_collect_player_executable=(self.current_config.gaze_collect_player_executable),
            just_need_to_see_root=compatibility_root,
            gaze_preflight_seconds=self.preflight_seconds_spin.value(),
            gaze_minimum_valid_ratio=self.minimum_validity_spin.value() / 100.0,
        )

    def _validate_config(self, config: GazeDeviceConfig) -> bool:
        if config.gaze_source == "tobii_legacy_bridge" and not config.tobii_bridge_host.strip():
            QMessageBox.warning(
                self,
                "兼容地址无效",
                "请输入兼容程序的主机地址。",
            )
            return False
        if config.gaze_source == "seveninvensun_bridge":
            if config.tobii_bridge_host.strip().lower() not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                QMessageBox.warning(
                    self,
                    "七鑫易维桥地址无效",
                    "七鑫易维 SDK 桥只允许使用 localhost、127.0.0.1 或 ::1。",
                )
                return False
        if config.gaze_source == "tobii_stream_engine" and config.tobii_stream_engine_dll:
            if not config.tobii_stream_engine_dll.is_file():
                QMessageBox.warning(
                    self,
                    "DLL 路径无效",
                    "所选 tobii_stream_engine.dll 不存在；可清空路径后使用自动发现。",
                )
                return False
        if config.gaze_source == "just_need_to_see_bundle":
            bundled_dll = config.just_need_to_see_root / "tobii_stream_engine.dll"
            if not bundled_dll.is_file():
                QMessageBox.warning(
                    self,
                    "Tobii 兼容 DLL 无效",
                    "请选择存在的 tobii_stream_engine.dll。",
                )
                return False
        return True

    def _save(self) -> None:
        config = self.build_config()
        if not self._validate_config(config):
            return
        if config.gaze_source in {"webcam", "auto_webcam"}:
            if not self._persist_webcam_camera_selection():
                return
        self.store.save(config)
        self.accept()
