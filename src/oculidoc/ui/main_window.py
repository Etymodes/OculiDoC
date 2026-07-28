"""Administrator desktop dashboard."""

import json
import mimetypes
import os
import sys
from contextlib import suppress
from functools import partial
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import (
    QProcess,
    QProcessEnvironment,
    Qt,
    QTimer,
)
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from oculidoc.application import PatientService
from oculidoc.application.experiment_session_service import (
    CreateExperimentSessionRequest,
    DuplicateSessionArtifactError,
    ExperimentSessionService,
    RegisterSessionArtifactRequest,
)
from oculidoc.application.gaze_task_session import (
    GazeTaskLaunch,
    create_gaze_task_launch,
    finalize_gaze_task_launch,
)
from oculidoc.branding import (
    brand_mark_pixmap,
)
from oculidoc.config import (
    AdminUiMode,
    AdminUiPreferences,
    AdminUiPreferencesStore,
    GazeDeviceConfig,
    GazeDeviceConfigStore,
    Settings,
)
from oculidoc.devices.preflight import GazePreflightResult, GazePreflightStore
from oculidoc.domain import Patient
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
    SessionArtifactKind,
)
from oculidoc.lan_commands import (
    REMOTE_GAZE_MODULE_IDS,
    LanCommand,
    LanCommandRejected,
    LanCommandStatus,
    LanCommandStore,
    LanCommandType,
)
from oculidoc.lan_control import (
    LanControlState,
    LanControlStateStore,
    LanControlTransitionError,
    PatientDisplayMode,
    build_control_url,
    generate_pairing_token,
    preferred_private_ipv4,
)
from oculidoc.modules.registry import DEFAULT_MODULES, ModuleDefinition
from oculidoc.process_launch import (
    gaze_task_process_command,
    is_frozen_application,
    local_api_process_command,
)
from oculidoc.speech_replay import SpeechReplayStore
from oculidoc.task_configs import TaskConfigStore
from oculidoc.ui.device_settings import DeviceSettingsDialog
from oculidoc.ui.lan_pairing import (
    HoverPairingButton,
    LanPairingDialog,
)
from oculidoc.ui.patient_management import (
    PatientManagementDialog,
    diagnosis_display_name,
)
from oculidoc.ui.patient_window import PatientDisplayWindow
from oculidoc.ui.session_history import PatientSessionHistoryDialog
from oculidoc.ui.test_plan import (
    CLINICAL_TASK_ORDER,
    TestPlan,
    TestPlanConflict,
    TestPlanDialog,
    TestPlanStep,
    TestPlanStepStatus,
    TestPlanStore,
)
from oculidoc.updater import find_repository_root
from oculidoc.vision.camera_preview_window import (
    CameraPreviewWindow,
)

RESULT_DISPLAY_MILLISECONDS = 6_000


class AdminMainWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        patient_service: PatientService | None = None,
        experiment_session_service: (ExperimentSessionService | None) = None,
    ) -> None:
        super().__init__()

        self.settings = settings
        self.patient_service = patient_service
        self.experiment_session_service = experiment_session_service
        self.current_patient: Patient | None = None
        self.module_buttons: dict[str, QPushButton] = {}
        self._admin_ui_preferences_store = AdminUiPreferencesStore.for_settings(self.settings)
        self.ui_mode = self._admin_ui_preferences_store.load().mode
        self._test_plan_store = TestPlanStore.for_settings(self.settings)
        self._eye_windows: dict[
            UUID,
            CameraPreviewWindow,
        ] = {}
        self._gaze_processes: dict[
            UUID,
            QProcess,
        ] = {}
        self._gaze_launches: dict[
            UUID,
            GazeTaskLaunch,
        ] = {}
        self._active_gaze_module_ids: set[str] = set()
        self._backend_process: QProcess | None = None
        self._update_process: QProcess | None = None
        self._pairing_dialog: LanPairingDialog | None = None
        self._pairing_pinned = False
        self._backend_status_name = "准备启动"
        self._pairing_hide_timer = QTimer(self)
        self._pairing_hide_timer.setSingleShot(True)
        self._pairing_hide_timer.setInterval(450)
        self._pairing_hide_timer.timeout.connect(self._hide_lan_pairing_if_unpinned)
        self._lan_host = preferred_private_ipv4()
        self._lan_token = generate_pairing_token()
        self._lan_state_path = (
            self.settings.data_dir.expanduser() / "runtime" / "lan_control_state.json"
        ).resolve()
        self._lan_state_store = LanControlStateStore(self._lan_state_path)
        self._lan_command_directory = (
            self.settings.data_dir.expanduser() / "runtime" / "lan_commands"
        ).resolve()
        self._lan_command_store = LanCommandStore(self._lan_command_directory)
        self._speech_replay_store = SpeechReplayStore(
            self.settings.data_dir.expanduser() / "runtime" / "speech_replay.json"
        )
        self._task_config_store = TaskConfigStore(
            self.settings.data_dir.expanduser() / "runtime" / "task_configs.json"
        )
        self._task_config_store.set_active_patient(None)
        self._gaze_device_config_store = GazeDeviceConfigStore.for_settings(self.settings)
        self._gaze_preflight_store = GazePreflightStore(
            self.settings.data_dir.expanduser() / "runtime" / "gaze_preflight.json"
        )
        self._lan_control_url = build_control_url(
            self._lan_host,
            self.settings.admin_port,
            self._lan_token,
        )
        self._lan_poll_timer = QTimer(self)
        self._lan_poll_timer.setInterval(300)
        self._lan_poll_timer.timeout.connect(self._poll_lan_control_state)
        self._lan_command_timer = QTimer(self)
        self._lan_command_timer.setInterval(250)
        self._lan_command_timer.timeout.connect(self._poll_lan_commands)
        self._patient_window = PatientDisplayWindow()
        self._patient_window.exit_requested.connect(self._handle_patient_display_exit)
        initial_display_state = self._lan_state_store.reset_idle()
        self._last_lan_revision = initial_display_state.revision
        self._patient_window.apply_state(initial_display_state)

        self.setWindowTitle("OculiDoC 管理员端")
        self.resize(1280, 820)
        self.setMinimumSize(1080, 700)
        self.setStyleSheet(
            """
            QMainWindow { background: #eef3f8; }
            QLabel { font-family: "Microsoft YaHei UI"; }
            QLabel#appTitle { color: #14324a; font-size: 32px; font-weight: 800; }
            QLabel#subtitle { color: #5a7184; font-size: 14px; }
            QLabel#sectionTitle { color: #17324d; font-size: 21px; font-weight: 700; }
            QLabel#moduleTitle { color: #18344c; font-size: 19px; font-weight: 700; }
            QLabel#moduleDescription { color: #577083; font-size: 14px; }
            QFrame#panel, QFrame#moduleCard, QFrame#workbenchTaskStrip {
                background: white;
                border: 1px solid #d9e3ec;
                border-radius: 14px;
            }
            QListWidget#workbenchPatientList, QListWidget#workbenchRecentList {
                background: #f8fbfe;
                border: 1px solid #d9e3ec;
                border-radius: 9px;
                padding: 4px;
                font-size: 14px;
            }
            QPushButton {
                min-height: 38px;
                background: #f8fbfe;
                color: #17324d;
                border: 1px solid #bfd3e4;
                border-radius: 9px;
                padding: 4px 16px;
                font-family: "Microsoft YaHei UI";
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #edf5fc;
                border-color: #76a9cf;
            }
            QPushButton:pressed {
                background: #d8eaf8;
                border-color: #4b89b7;
                padding-top: 6px;
                padding-left: 17px;
            }
            QPushButton:disabled {
                background: #f1f4f7;
                color: #98a6b3;
                border-color: #d9e3ec;
            }
            QPushButton#primaryButton, QPushButton#startNextPlanStepButton {
                background: #1565c0;
                color: white;
                border: none;
            }
            QPushButton#primaryButton:hover, QPushButton#startNextPlanStepButton:hover {
                background: #0f5ab0;
            }
            QPushButton#primaryButton:pressed, QPushButton#startNextPlanStepButton:pressed {
                background: #0b4c96;
            }
            QPushButton#secondaryButton {
                background: #edf4fb;
                color: #184e77;
                border: 1px solid #bfd3e4;
            }
            QPushButton#secondaryButton:hover { background: #dcecf9; }
            QPushButton#secondaryButton:pressed { background: #c8e0f3; }
            QPushButton#backendStatusButton {
                background: transparent;
                color: #184e77;
                border: 1px solid transparent;
                min-height: 28px;
                padding: 2px 8px;
            }
            QPushButton#backendStatusButton:hover {
                background: #edf4fb;
                border: 1px solid #bfd3e4;
            }
            QPushButton#dangerButton { background: #b42318; color: white; border: none; }
            QPushButton#dangerButton:hover { background: #971d14; }
            QPushButton#dangerButton:pressed { background: #7d180f; }
            """
        )

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        root.addLayout(self._build_header())
        if self.ui_mode is AdminUiMode.CLASSIC:
            root.addWidget(self._build_patient_panel())
            root.addWidget(self._build_module_area(), 1)
        else:
            root.addWidget(self._build_workbench_area(), 1)
        root.addWidget(self._build_status_panel())

        self.setCentralWidget(central)
        self._refresh_patient_summary()
        self._lan_poll_timer.start()
        self._lan_command_timer.start()
        self._poll_lan_control_state()
        self._poll_lan_commands()

        if self._should_auto_start_backend():
            QTimer.singleShot(
                0,
                self._start_local_backend,
            )
            QTimer.singleShot(
                0,
                self._show_patient_display_behind_admin,
            )

    def _patient_counts(self) -> tuple[int, int]:
        """Return total and active patient counts."""
        if self.patient_service is None:
            return 0, 0

        patients = self.patient_service.list_patients()
        active_count = sum(patient.is_active for patient in patients)

        return len(patients), active_count

    def _patient_panel_text(self) -> str:
        """Return patient summary text."""
        if self.patient_service is None:
            return "患者数据库未连接。"

        if self.current_patient is not None:
            return (
                f"当前患者：{self.current_patient.display_label}"
                f" · 诊断：{diagnosis_display_name(self.current_patient.clinical_diagnosis)}"
            )

        total_count, active_count = self._patient_counts()

        if total_count == 0:
            return "患者数据库已连接，尚未登记患者。"

        return f"已登记 {total_count} 名患者，其中 {active_count} 名启用；尚未选择当前患者。"

    def _patient_status_text(self) -> str:
        """Return compact database status text."""
        if self.patient_service is None:
            return "患者数据：未连接"

        total_count, active_count = self._patient_counts()

        return f"患者数据：已初始化 · 总计 {total_count} · 启用 {active_count}"

    def _gaze_source_status_text(self) -> str:
        """Return configured source plus the latest measured live quality."""
        preflight = self._current_gaze_preflight()

        if self.settings.gaze_source == "mock":
            if preflight is None:
                return "眼动源：工程模拟测试"

            return (
                "眼动源：工程模拟测试"
                f" · {preflight.sample_rate_hz:.0f} Hz"
                f" · 有效率 {preflight.valid_ratio:.0%}"
            )

        labels = {
            "auto": "硬件自动检测",
            "gaze_collect_legacy": "第三方兼容",
            "just_need_to_see_bundle": "Tobii DLL兼容",
            "tobii_hospital_bridge": "原监听兼容",
            "tobii_stream_engine": "Tobii 原生 Stream",
            "tobii_legacy_bridge": "第三方兼容",
        }
        source_name = labels.get(self.settings.gaze_source, self.settings.gaze_source)

        if preflight is None:
            return f"眼动源：{source_name} · 尚未预检"

        if self.settings.gaze_source == "auto":
            source_name = f"硬件自动检测 → {preflight.device_name}"

        connection = "已连接" if self._active_gaze_module_ids else "最近预检"
        suffix = (
            f"{connection} · {preflight.sample_rate_hz:.0f} Hz · 有效率 {preflight.valid_ratio:.0%}"
        )
        if preflight.error and preflight.sample_count == 0:
            suffix = f"预检失败 · {preflight.error}"
        elif not preflight.passed:
            suffix = (
                f"有效率不足 · {preflight.sample_rate_hz:.0f} Hz"
                f" · 有效率 {preflight.valid_ratio:.0%}"
            )
        return f"眼动源：{source_name} · {suffix}"

    def _current_gaze_preflight(self) -> GazePreflightResult | None:
        result = self._gaze_preflight_store.load()
        if result is None or result.source != self.settings.gaze_source:
            return None
        return result

    def _refresh_gaze_status(self) -> None:
        if not hasattr(self, "gaze_status_label"):
            return

        result = self._current_gaze_preflight()
        color = "#6b7280"
        if self.settings.gaze_source != "mock":
            if result is None or (result.error and result.sample_count == 0):
                color = "#b42318"
            elif result.passed:
                color = "#176b36"
            else:
                color = "#8a5a00"
        self.gaze_status_label.setText(self._gaze_source_status_text())
        self.gaze_status_label.setStyleSheet(f"color:{color}; font-weight:700;")

    def _show_timed_task_message(
        self,
        icon: QMessageBox.Icon,
        title: str,
        message: str,
    ) -> None:
        """Show a non-blocking task result message that cannot cover the next setup dialog."""
        box = QMessageBox(icon, title, message, QMessageBox.StandardButton.NoButton, self)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.show()
        QTimer.singleShot(RESULT_DISPLAY_MILLISECONDS, box.close)

    def _build_header(
        self,
    ) -> QHBoxLayout:
        header = QHBoxLayout()
        titles = QVBoxLayout()

        logo_label = QLabel()
        logo_label.setObjectName("brandMark")
        logo_pixmap = brand_mark_pixmap(
            variant="blue",
            max_width=150,
            max_height=90,
        )

        if logo_pixmap.isNull():
            logo_label.hide()
        else:
            logo_label.setPixmap(logo_pixmap)
            logo_label.setFixedSize(logo_pixmap.size())

        app_title = QLabel("OculiDoC")
        app_title.setObjectName("appTitle")
        subtitle = QLabel(
            f"意识障碍眼动评估、交互与训练平台 · 联合开发：{self.settings.collaborator_name}"
        )
        subtitle.setObjectName("subtitle")

        titles.addWidget(app_title)
        titles.addWidget(subtitle)

        emergency_button = QPushButton("紧急退出程序")
        emergency_button.setObjectName("dangerButton")
        emergency_button.clicked.connect(self._request_application_exit)

        self.update_button = QPushButton("检查更新")
        self.update_button.setObjectName("secondaryButton")
        self.update_button.clicked.connect(self._check_for_updates)

        self.admin_settings_button = QPushButton("总设置")
        self.admin_settings_button.setObjectName("adminSettingsButton")
        self.admin_settings_button.clicked.connect(self._open_admin_settings)

        self.stop_task_button = QPushButton("停止任务")
        self.stop_task_button.setObjectName("stopTaskButton")
        self.stop_task_button.setEnabled(False)
        self.stop_task_button.clicked.connect(self._stop_active_tasks)

        header.addWidget(logo_label)
        header.addLayout(titles)
        header.addStretch(1)
        header.addWidget(self.stop_task_button)
        header.addWidget(self.admin_settings_button)
        header.addWidget(self.update_button)
        header.addWidget(emergency_button)
        return header

    def _open_admin_settings(self, checked: bool = False) -> None:
        """Persist the administrator shell to apply after the next restart."""
        del checked
        labels = {
            AdminUiMode.CLINICAL_WORKBENCH: "患者工作台（默认）",
            AdminUiMode.CLASSIC: "经典皮肤（原有界面）",
        }
        modes = tuple(labels)
        current = modes.index(self._admin_ui_preferences_store.load().mode)
        selected, accepted = QInputDialog.getItem(
            self,
            "总设置",
            "管理端界面（保存后重启 OculiDoC 生效）：",
            [labels[mode] for mode in modes],
            current,
            False,
        )
        if not accepted:
            return
        if self._task_in_progress():
            QMessageBox.information(
                self,
                "任务进行中",
                "当前可以查看界面设置，但请先结束任务后再保存切换。",
            )
            return
        mode = next(mode for mode in modes if labels[mode] == selected)
        self._admin_ui_preferences_store.save(AdminUiPreferences(mode=mode))
        QMessageBox.information(
            self,
            "总设置已保存",
            f"下次启动将使用“{labels[mode]}”。本次运行中的界面不会重建。",
        )

    def _check_for_updates(self, checked: bool = False) -> None:
        """Run the clean fast-forward updater without blocking the administrator UI."""
        del checked

        if self._update_process is not None:
            return

        if self._task_in_progress():
            QMessageBox.information(
                self,
                "任务进行中",
                "请先结束当前眼动任务，再检查软件更新。",
            )
            return

        frozen = is_frozen_application()
        root_hint = os.environ.get("OCULIDOC_REPOSITORY_ROOT", "").strip() or __file__
        repository_root = None if frozen else find_repository_root(root_hint)

        if not frozen and repository_root is None:
            QMessageBox.information(self, "无法更新", "未找到 OculiDoC 源码仓库。")
            return

        confirmation = QMessageBox.question(
            self,
            "检查并更新 OculiDoC",
            (
                "将检查 GitHub 最新正式版本；如有新版，会自动下载、校验"
                "并启动安装器。"
                "安装器启动后 OculiDoC 将退出。继续吗？"
                if frozen
                else (
                    "将从官方仓库检查当前分支，并且只在工作区干净且可快进时"
                    "更新。继续吗？"
                )
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if confirmation != QMessageBox.StandardButton.Yes:
            return

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setProgram(sys.executable)
        process.setArguments(
            ["--install-latest-release"]
            if frozen
            else ["-m", "oculidoc.updater", "--repo", str(repository_root)]
        )
        process.finished.connect(self._finish_update_check)
        self._update_process = process
        self.update_button.setEnabled(False)
        self.update_button.setText("正在检查更新…")
        process.start()

        if not process.waitForStarted(5_000):
            self._update_process = None
            self.update_button.setEnabled(True)
            self.update_button.setText("检查更新")
            QMessageBox.warning(self, "无法检查更新", process.errorString())
            process.deleteLater()

    def _finish_update_check(self, exit_code: int, exit_status: object) -> None:
        del exit_status
        process = self._update_process
        self._update_process = None
        self.update_button.setEnabled(True)
        self.update_button.setText("检查更新")

        if process is None:
            return

        output = bytes(process.readAllStandardOutput())  # type: ignore[call-overload]
        text_output = output.decode("utf-8", errors="replace").strip()
        process.deleteLater()

        try:
            payload = json.loads(text_output.splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            payload = {"status": "error", "message": text_output or f"退出码 {exit_code}"}

        status = payload.get("status") if isinstance(payload, dict) else "error"

        if exit_code != 0 or status == "error":
            message = (
                str(payload.get("message", text_output))
                if isinstance(payload, dict)
                else text_output
            )
            QMessageBox.warning(self, "OculiDoC 更新未完成", message)
            return

        if status == "up_to_date":
            QMessageBox.information(
                self,
                "OculiDoC 已是最新版本",
                "当前版本无需更新。",
            )
            return

        if status == "installer_started":
            QMessageBox.information(
                self,
                "已启动 OculiDoC 更新",
                f"最新版 {payload.get('latest_version', '')} 已通过校验并启动安装。"
                "OculiDoC 现在将退出。",
            )
            QApplication.quit()
            return

        restart = QMessageBox.question(
            self,
            "OculiDoC 更新完成",
            "更新已安全快进完成。需要退出并重新启动后生效。现在退出吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if restart == QMessageBox.StandardButton.Yes:
            QApplication.quit()

    def _build_patient_panel(
        self,
    ) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(
            20,
            16,
            20,
            16,
        )

        text = QVBoxLayout()
        title = QLabel("当前患者")
        title.setObjectName("sectionTitle")
        self.patient_label = QLabel(self._patient_panel_text())
        self.patient_label.setObjectName("subtitle")
        text.addWidget(title)
        text.addWidget(self.patient_label)

        manage_button = QPushButton("患者管理")
        manage_button.setObjectName("secondaryButton")
        manage_button.clicked.connect(self._show_patient_placeholder)

        self.history_button = QPushButton("实验记录")
        self.history_button.setObjectName("patientSessionHistoryButton")
        self.history_button.clicked.connect(self._open_session_history)

        display_button = QPushButton("打开患者显示端")
        display_button.setObjectName("primaryButton")
        display_button.clicked.connect(self._open_patient_display)

        project_text_button = QPushButton("投送文字")
        project_text_button.setObjectName("secondaryButton")
        project_text_button.clicked.connect(self._project_patient_text)

        layout.addLayout(text, 1)
        layout.addWidget(manage_button)
        layout.addWidget(self.history_button)
        layout.addWidget(project_text_button)
        layout.addWidget(display_button)
        return panel

    def _build_workbench_area(self) -> QWidget:
        """Build the default patient-first administrator home page."""
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        panels = QHBoxLayout()
        panels.setSpacing(14)

        patient_panel = QFrame()
        patient_panel.setObjectName("panel")
        patient_panel.setMinimumWidth(220)
        patient_panel.setMaximumWidth(270)
        patient_layout = QVBoxLayout(patient_panel)
        patient_layout.setContentsMargins(16, 16, 16, 16)
        patient_layout.addWidget(self._section_label("患者"))

        self.workbench_patient_list = QListWidget()
        self.workbench_patient_list.setObjectName("workbenchPatientList")
        self.workbench_patient_list.currentItemChanged.connect(self._select_workbench_patient)
        patient_layout.addWidget(self.workbench_patient_list, 1)

        manage_button = QPushButton("患者管理")
        manage_button.setObjectName("workbenchPatientManagementButton")
        manage_button.clicked.connect(self._show_patient_placeholder)
        patient_layout.addWidget(manage_button)

        current_panel = QFrame()
        current_panel.setObjectName("panel")
        current_panel.setMinimumWidth(390)
        current_layout = QVBoxLayout(current_panel)
        current_layout.setContentsMargins(18, 16, 18, 16)
        current_layout.addWidget(self._section_label("当前患者工作区"))

        self.patient_label = QLabel(self._patient_panel_text())
        self.patient_label.setObjectName("workbenchCurrentPatient")
        self.patient_label.setWordWrap(True)
        self.patient_label.setStyleSheet("font-size:34px;font-weight:800;color:#123d63;")
        current_layout.addWidget(self.patient_label)

        self.plan_progress_label = QLabel("尚未选择患者")
        self.plan_progress_label.setObjectName("workbenchPlanProgress")
        self.plan_progress_label.setStyleSheet("font-size:18px;font-weight:700;color:#17324d;")
        self.plan_progress_label.setWordWrap(True)
        current_layout.addWidget(self.plan_progress_label)

        self.next_task_label = QLabel("选择患者后可编排本次测试。")
        self.next_task_label.setObjectName("workbenchNextTask")
        self.next_task_label.setWordWrap(True)
        self.next_task_label.setStyleSheet("font-size:26px;font-weight:800;color:#17324d;")
        current_layout.addWidget(self.next_task_label)

        self.recent_result_label = QLabel("最近结果：暂无")
        self.recent_result_label.setObjectName("workbenchRecentResult")
        self.recent_result_label.setWordWrap(True)
        self.recent_result_label.setStyleSheet("color:#5a7184;")
        current_layout.addWidget(self.recent_result_label)
        current_layout.addStretch(1)

        self.plan_button = QPushButton("编排本次测试")
        self.plan_button.setObjectName("openTestPlanButton")
        self.plan_button.clicked.connect(self._open_test_plan_dialog)
        self.start_next_button = QPushButton("开始下一项")
        self.start_next_button.setObjectName("startNextPlanStepButton")
        self.start_next_button.clicked.connect(self._start_next_plan_step)
        self.retry_plan_button = QPushButton("重试当前项")
        self.retry_plan_button.setObjectName("retryPlanStepButton")
        self.retry_plan_button.clicked.connect(self._retry_plan_step)
        self.finish_plan_button = QPushButton("结束本次测试")
        self.finish_plan_button.setObjectName("finishTestPlanButton")
        self.finish_plan_button.clicked.connect(self._finish_current_test_plan)

        plan_actions = QGridLayout()
        plan_actions.addWidget(self.plan_button, 0, 0)
        plan_actions.addWidget(self.start_next_button, 0, 1)
        plan_actions.addWidget(self.retry_plan_button, 1, 0)
        plan_actions.addWidget(self.finish_plan_button, 1, 1)
        current_layout.addLayout(plan_actions)

        self.history_button = QPushButton("查看完整记录")
        self.history_button.setObjectName("patientSessionHistoryButton")
        self.history_button.clicked.connect(self._open_session_history)
        current_layout.addWidget(self.history_button)

        tools_panel = QFrame()
        tools_panel.setObjectName("panel")
        tools_panel.setMinimumWidth(255)
        tools_layout = QVBoxLayout(tools_panel)
        tools_layout.setContentsMargins(16, 16, 16, 16)
        tools_layout.addWidget(self._section_label("记录与工具"))

        self.workbench_recent_list = QListWidget()
        self.workbench_recent_list.setObjectName("workbenchRecentList")
        self.workbench_recent_list.itemDoubleClicked.connect(
            lambda _item: self._open_session_history()
        )
        tools_layout.addWidget(self.workbench_recent_list, 1)

        project_text_button = QPushButton("投送文字")
        project_text_button.clicked.connect(self._project_patient_text)
        display_button = QPushButton("打开患者显示端")
        display_button.clicked.connect(self._open_patient_display)
        device_button = QPushButton("设备设置")
        device_button.clicked.connect(self._open_device_settings)

        tools_layout.addWidget(project_text_button)
        tools_layout.addWidget(display_button)
        tools_layout.addWidget(device_button)

        panels.addWidget(patient_panel)
        panels.addWidget(current_panel, 2)
        panels.addWidget(tools_panel, 1)
        root.addLayout(panels, 1)

        task_strip = QFrame()
        task_strip.setObjectName("workbenchTaskStrip")
        task_strip_layout = QVBoxLayout(task_strip)
        task_strip_layout.setContentsMargins(12, 9, 12, 10)
        task_strip_layout.setSpacing(6)
        task_grid = QGridLayout()
        task_grid.setHorizontalSpacing(8)
        task_grid.setVerticalSpacing(6)
        self.module_buttons = {}
        for index, module in enumerate(DEFAULT_MODULES):
            button = QPushButton(module.title)
            button.setObjectName(f"moduleButton_{module.module_id}")
            button.setProperty("moduleId", module.module_id)
            button.setProperty("idleText", module.title)
            button.setToolTip(f"直接打开：{module.title}")
            button.clicked.connect(partial(self._open_module, module))
            self.module_buttons[module.module_id] = button
            task_grid.addWidget(button, index // 5, index % 5)
        task_strip_layout.addLayout(task_grid)
        root.addWidget(task_strip)
        return container

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _open_session_history(
        self,
        checked: bool = False,
    ) -> None:
        """Open history for the selected patient."""

        del checked

        if self.current_patient is None:
            QMessageBox.information(
                self,
                "尚未选择患者",
                "请先选择当前患者，再查看实验记录。",
            )
            return

        if self.experiment_session_service is None:
            QMessageBox.warning(
                self,
                "实验会话服务未连接",
                "无法读取患者实验记录。",
            )
            return

        dialog = PatientSessionHistoryDialog(
            self.experiment_session_service,
            self.current_patient,
            self,
            is_session_active=self._is_gaze_session_active,
        )
        dialog.exec()

    def _is_gaze_session_active(
        self,
        session_id: UUID,
    ) -> bool:
        """Return whether this window still owns a live task process."""
        return session_id in self._gaze_launches

    def _build_module_area(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel("功能项目")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        for index, module in enumerate(DEFAULT_MODULES):
            grid.addWidget(self._create_module_card(module), index // 2, index % 2)

        layout.addLayout(grid)
        layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

    def _create_module_card(self, module: ModuleDefinition) -> QFrame:
        card = QFrame()
        card.setObjectName("moduleCard")
        card.setMinimumHeight(160)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)

        title = QLabel(module.title)
        title.setObjectName("moduleTitle")

        description = QLabel(module.description)
        description.setObjectName("moduleDescription")
        description.setWordWrap(True)

        button = QPushButton("打开项目")
        button.setObjectName(f"moduleButton_{module.module_id}")
        button.setProperty("moduleId", module.module_id)
        button.setProperty("idleText", "打开项目")
        button.clicked.connect(
            partial(
                self._open_module,
                module,
            )
        )
        self.module_buttons[module.module_id] = button

        layout.addWidget(title)
        layout.addWidget(description, 1)
        layout.addWidget(button)
        return card

    def _build_status_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(
            18,
            13,
            18,
            13,
        )

        self.gaze_status_label = QLabel(self._gaze_source_status_text())
        self.gaze_status_label.setObjectName("subtitle")
        self.device_settings_button = QPushButton("设备设置")
        self.device_settings_button.setObjectName("secondaryButton")
        self.device_settings_button.clicked.connect(self._open_device_settings)

        self.backend_status_button = HoverPairingButton(self._backend_status_text())
        self.backend_status_button.setObjectName("backendStatusButton")
        self.backend_status_button.setToolTip("悬停显示二维码；点击可固定或关闭配对卡")
        self.backend_status_button.clicked.connect(self._toggle_lan_pairing_pin)
        self.backend_status_button.hover_entered.connect(self._show_lan_pairing_hover)
        self.backend_status_button.hover_left.connect(self._schedule_lan_pairing_hide)

        self.patient_status_label = QLabel(self._patient_status_text())
        self.patient_status_label.setObjectName("subtitle")

        layout.addWidget(self.gaze_status_label)
        layout.addWidget(self.device_settings_button)
        layout.addStretch(1)
        layout.addWidget(self.backend_status_button)
        layout.addStretch(1)
        layout.addWidget(self.patient_status_label)
        self._refresh_gaze_status()
        return panel

    def _open_device_settings(self, checked: bool = False) -> None:
        del checked

        if self._task_in_progress():
            QMessageBox.information(
                self,
                "任务进行中",
                "请先结束当前眼动任务，再修改设备设置。",
            )
            return

        dialog = DeviceSettingsDialog(
            self.settings,
            self._gaze_device_config_store,
            self._current_gaze_preflight(),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        config = self._gaze_device_config_store.load(GazeDeviceConfig.from_settings(self.settings))
        self.settings = config.apply(self.settings)
        self._refresh_gaze_status()

    def _backend_status_text(self) -> str:
        return (
            f"本地后台：{self._backend_status_name}"
            f" · {self._lan_host}:{self.settings.admin_port}"
            " · 悬停扫码"
        )

    def _update_backend_status_button(self) -> None:
        if hasattr(self, "backend_status_button"):
            self.backend_status_button.setText(self._backend_status_text())

    def _should_auto_start_backend(self) -> bool:
        return self.settings.environment != "test" and "PYTEST_CURRENT_TEST" not in os.environ

    def _start_local_backend(self) -> None:
        if (
            self._backend_process is not None
            and self._backend_process.state() != QProcess.ProcessState.NotRunning
        ):
            return

        self._refresh_lan_pairing_address()
        self._lan_state_store.ensure()
        process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert(
            "OCULIDOC_ADMIN_HOST",
            "0.0.0.0",
        )
        environment.insert(
            "OCULIDOC_ADMIN_PORT",
            str(self.settings.admin_port),
        )
        environment.insert(
            "OCULIDOC_DATA_DIR",
            str(self.settings.data_dir),
        )
        environment.insert(
            "OCULIDOC_GAZE_SOURCE",
            self.settings.gaze_source,
        )
        environment.insert(
            "OCULIDOC_LAN_TOKEN",
            self._lan_token,
        )
        environment.insert(
            "OCULIDOC_LAN_STATE_PATH",
            str(self._lan_state_path),
        )
        environment.insert(
            "OCULIDOC_LAN_COMMAND_DIR",
            str(self._lan_command_directory),
        )
        process.setProcessEnvironment(environment)
        program, arguments = local_api_process_command()
        process.setProgram(program)
        process.setArguments(arguments)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.started.connect(self._backend_started)
        process.finished.connect(self._backend_finished)
        process.errorOccurred.connect(self._backend_error)
        process.readyReadStandardOutput.connect(self._drain_backend_output)
        self._backend_process = process
        self.backend_status_button.setText(
            f"本地后台：启动中 · {self._lan_host}:{self.settings.admin_port}"
        )
        process.start()

    def _backend_started(self) -> None:
        self._backend_status_name = "已启动"
        self._update_backend_status_button()

    def _backend_finished(
        self,
        exit_code: int,
        exit_status: object,
    ) -> None:
        del exit_status
        self._backend_status_name = f"已停止 · 退出码 {exit_code}"
        self._update_backend_status_button()

    def _backend_error(
        self,
        error: object,
    ) -> None:
        del error
        self._backend_status_name = "启动失败"
        self._update_backend_status_button()

    def _drain_backend_output(self) -> None:
        if self._backend_process is not None:
            self._backend_process.readAllStandardOutput()

    def _ensure_pairing_dialog(
        self,
    ) -> LanPairingDialog:
        if self._pairing_dialog is None:
            dialog = LanPairingDialog(
                self._lan_control_url,
                self,
            )
            dialog.pointer_entered.connect(self._cancel_lan_pairing_hide)
            dialog.pointer_left.connect(self._schedule_lan_pairing_hide)
            dialog.close_requested.connect(self._close_lan_pairing)
            dialog.refresh_requested.connect(self._refresh_lan_pairing_address)
            self._pairing_dialog = dialog

        return self._pairing_dialog

    def _show_lan_pairing_hover(
        self,
    ) -> None:
        self._show_lan_pairing(pin=False)

    def _show_lan_pairing(
        self,
        *,
        pin: bool,
    ) -> None:
        self._cancel_lan_pairing_hide()

        if (
            self._backend_process is None
            or self._backend_process.state() == QProcess.ProcessState.NotRunning
        ):
            self._start_local_backend()

        if pin:
            self._pairing_pinned = True

        dialog = self._ensure_pairing_dialog()
        dialog.show_near(self.backend_status_button)

    def _toggle_lan_pairing_pin(
        self,
        checked: bool = False,
    ) -> None:
        del checked

        if (
            self._pairing_pinned
            and self._pairing_dialog is not None
            and self._pairing_dialog.isVisible()
        ):
            self._close_lan_pairing()
            return

        self._show_lan_pairing(pin=True)

    def _schedule_lan_pairing_hide(
        self,
    ) -> None:
        if not self._pairing_pinned:
            self._pairing_hide_timer.start()

    def _cancel_lan_pairing_hide(
        self,
    ) -> None:
        self._pairing_hide_timer.stop()

    def _hide_lan_pairing_if_unpinned(
        self,
    ) -> None:
        if not self._pairing_pinned and self._pairing_dialog is not None:
            self._pairing_dialog.hide()

    def _close_lan_pairing(
        self,
    ) -> None:
        self._pairing_pinned = False
        self._pairing_hide_timer.stop()

        if self._pairing_dialog is not None:
            self._pairing_dialog.hide()

    def _refresh_lan_pairing_address(
        self,
    ) -> None:
        self._lan_host = preferred_private_ipv4()
        self._lan_control_url = build_control_url(
            self._lan_host,
            self.settings.admin_port,
            self._lan_token,
        )
        self._update_backend_status_button()

        if self._pairing_dialog is not None:
            self._pairing_dialog.update_control_url(self._lan_control_url)
            self._pairing_dialog.show_near(self.backend_status_button)

    def _poll_lan_control_state(self) -> None:
        self._refresh_gaze_status()

        try:
            state = self._lan_state_store.load()
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ):
            return

        if state.revision <= self._last_lan_revision:
            return

        self._last_lan_revision = state.revision
        self._patient_window.apply_state(state)

        if (
            state.mode is not PatientDisplayMode.CLOSED
            and self.settings.environment != "test"
            and not self._patient_window.isVisible()
        ):
            self._patient_window.showFullScreen()

    def _publish_patient_display(
        self,
        text: str,
        *,
        mode: PatientDisplayMode,
        task_id: str | None = None,
        countdown_seconds: int | None = None,
    ) -> LanControlState:
        state = self._lan_state_store.set_display(
            text,
            mode=mode,
            task_id=task_id,
            countdown_seconds=countdown_seconds,
        )
        self._last_lan_revision = state.revision
        self._patient_window.apply_state(state)
        return state

    def _reset_patient_display(self) -> LanControlState:
        state = self._lan_state_store.reset_idle()
        self._last_lan_revision = state.revision
        self._patient_window.apply_state(state)
        return state

    def _project_patient_text(self, checked: bool = False) -> None:
        del checked
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "投送患者端文字",
            "显示内容：",
        )

        if not accepted or not text.strip():
            return

        try:
            self._publish_patient_display(
                text,
                mode=PatientDisplayMode.PREVIEW,
            )
        except LanControlTransitionError:
            QMessageBox.information(
                self,
                "任务正在进行",
                "请先结束当前任务，再投送普通文字。",
            )
            return

        self._open_patient_display()

    def _poll_lan_commands(self) -> None:
        for command in self._lan_command_store.pending():
            try:
                accepted = self._lan_command_store.transition(
                    command.command_id,
                    LanCommandStatus.ACCEPTED,
                    "桌面管理员端已接收命令。",
                )
            except (OSError, ValueError):
                continue

            try:
                message = self._execute_lan_command(accepted)
            except LanCommandRejected as error:
                self._lan_command_store.transition(
                    accepted.command_id,
                    LanCommandStatus.REJECTED,
                    str(error),
                )
            except Exception as error:
                self._lan_command_store.transition(
                    accepted.command_id,
                    LanCommandStatus.REJECTED,
                    f"桌面端执行失败：{error}",
                )
            else:
                self._lan_command_store.transition(
                    accepted.command_id,
                    LanCommandStatus.COMPLETED,
                    message,
                )

    def _execute_lan_command(self, command: LanCommand) -> str:
        if command.command_type is LanCommandType.OPEN_PATIENT_DISPLAY:
            self._open_patient_display()
            return "患者显示端已打开。"

        if command.command_type is LanCommandType.START_TASK:
            return self._execute_remote_task_start(command)

        if command.command_type is LanCommandType.STOP_TASK:
            return self._execute_remote_task_stop(command)

        if command.command_type is LanCommandType.REPLAY_SPEECH:
            return self._execute_remote_speech_replay(command)

        raise LanCommandRejected("未知桌面命令。")

    def _execute_remote_task_start(self, command: LanCommand) -> str:
        module_id = command.module_id
        config_revision = command.config_revision
        game_mode = command.game_mode

        if module_id not in REMOTE_GAZE_MODULE_IDS:
            raise LanCommandRejected("该模块尚不支持手机远程启动。")

        module = next(
            (item for item in DEFAULT_MODULES if item.module_id == module_id),
            None,
        )

        if module is None:
            raise LanCommandRejected("未找到对应实验模块。")

        if self.current_patient is None:
            raise LanCommandRejected("尚未选择患者，请先在电脑端选择当前患者。")

        if self.experiment_session_service is None:
            raise LanCommandRejected("实验会话服务未连接。")

        if self._task_in_progress():
            raise LanCommandRejected("已有任务正在启动、设置或运行，请先结束当前任务。")

        if config_revision is None:
            raise LanCommandRejected("远程启动缺少任务设置版本。")

        if module_id == "gaze_games" and game_mode is None:
            raise LanCommandRejected("请选择游戏模式。")

        if module_id != "gaze_games" and game_mode is not None:
            raise LanCommandRejected("该任务不接受游戏模式。")

        current_config = self._task_config_store.load(module_id)

        if current_config.revision != config_revision:
            raise LanCommandRejected(
                f"任务设置已更新，请在手机端刷新后重新启动。当前版本：{current_config.revision}。"
            )

        self._publish_patient_display(
            f"正在启动：{module.title}",
            mode=PatientDisplayMode.PREVIEW,
            task_id=module_id,
        )
        if game_mode is None:
            self._open_gaze_task_module(
                module,
                config_revision=config_revision,
            )
        else:
            self._open_gaze_task_module(
                module,
                config_revision=config_revision,
                game_mode=game_mode,
            )

        if module_id not in self._active_gaze_module_ids:
            raise LanCommandRejected("任务进程未能启动，请查看电脑端提示。")

        mode_text = (
            " · 点亮花园"
            if game_mode == "garden"
            else " · 视觉寻宝"
            if game_mode == "treasure_hunt"
            else ""
        )
        return f"{module.title}{mode_text}已按设置版本 {config_revision} 直接启动。"

    def _execute_remote_task_stop(self, command: LanCommand) -> str:
        module_id = command.module_id
        matches: list[tuple[UUID, QProcess]] = []

        for session_id, launch in tuple(self._gaze_launches.items()):
            if module_id is not None and launch.module_id != module_id:
                continue

            process = self._gaze_processes.get(session_id)

            if process is not None and process.state() != QProcess.ProcessState.NotRunning:
                matches.append((session_id, process))

        if not matches:
            raise LanCommandRejected("没有匹配的运行中任务。")

        for _, process in matches:
            process.terminate()

            if not process.waitForFinished(1_500):
                process.kill()
                process.waitForFinished(1_000)

        self._reset_patient_display()
        return f"已向 {len(matches)} 个任务进程发送终止命令。"

    def _execute_remote_speech_replay(self, command: LanCommand) -> str:
        module_id = command.module_id

        if module_id is not None:
            if module_id not in self._active_gaze_module_ids:
                raise LanCommandRejected("指定任务当前没有运行。")
            active_module = module_id
        elif len(self._active_gaze_module_ids) == 1:
            active_module = next(iter(self._active_gaze_module_ids))
        else:
            raise LanCommandRejected("当前没有可重播语音的运行中任务。")

        request = self._speech_replay_store.request(active_module)
        return f"已请求重播当前任务语音（版本 {request.revision}）。"

    def _open_patient_display(self, checked: bool = False) -> None:
        del checked
        state = self._lan_state_store.load()

        if state.mode is PatientDisplayMode.CLOSED:
            state = self._lan_state_store.reset_idle()

        self._last_lan_revision = state.revision
        self._patient_window.apply_state(state)
        self._patient_window.showFullScreen()
        self._patient_window.raise_()
        self._patient_window.activateWindow()

    def _show_patient_display_behind_admin(self) -> None:
        """Prepare the patient screen without covering the administrator."""
        state = self._lan_state_store.load()
        if state.mode is PatientDisplayMode.CLOSED:
            state = self._lan_state_store.reset_idle()
        self._last_lan_revision = state.revision
        self._patient_window.apply_state(state)
        self._patient_window.showFullScreen()
        self._patient_window.lower()
        self._restore_admin_window()

    def _handle_patient_display_exit(self) -> None:
        state = self._lan_state_store.load()

        if state.mode is not PatientDisplayMode.CLOSED:
            state = self._lan_state_store.set_closed()
            self._last_lan_revision = state.revision
            self._patient_window.apply_state(state)

        self._restore_admin_window()

    def _restore_admin_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _refresh_patient_summary(self) -> None:
        """Refresh patient labels after database changes."""
        self.patient_label.setText(self._patient_panel_text())

        if hasattr(self, "patient_status_label"):
            self.patient_status_label.setText(self._patient_status_text())
        if hasattr(self, "workbench_patient_list"):
            self._refresh_workbench_patient_list()
            self._refresh_workbench_plan()
            self._refresh_workbench_recent_sessions()

    def _reload_current_patient(self) -> None:
        """Reload or clear the current patient."""
        if self.patient_service is None or self.current_patient is None:
            return

        patient = self.patient_service.get_patient(self.current_patient.patient_id)

        if patient.is_active:
            self.current_patient = patient
            self._task_config_store.set_active_patient(
                str(patient.patient_id),
                inherit_legacy=self._patient_has_history(patient.patient_id),
            )
        else:
            self.current_patient = None
            self._task_config_store.set_active_patient(None)

    def _patient_has_history(self, patient_id: UUID) -> bool:
        if self._test_plan_store.load(str(patient_id)) is not None:
            return True
        return bool(
            self.experiment_session_service is not None
            and self.experiment_session_service.list_sessions_for_patient(patient_id)
        )

    def _set_current_patient(
        self,
        patient: Patient,
    ) -> None:
        """Set and display the current patient."""
        if not patient.is_active:
            return
        if (
            self._task_in_progress()
            and self.current_patient is not None
            and patient.patient_id != self.current_patient.patient_id
        ):
            QMessageBox.information(
                self,
                "任务进行中",
                "请先结束当前任务，再切换患者。",
            )
            return

        self._task_config_store.set_active_patient(
            str(patient.patient_id),
            inherit_legacy=self._patient_has_history(patient.patient_id),
        )
        self.current_patient = patient
        self._refresh_patient_summary()

    def _refresh_workbench_patient_list(self) -> None:
        patient_list = self.workbench_patient_list
        patient_list.blockSignals(True)
        try:
            patient_list.clear()
            patients = (
                self.patient_service.list_patients(active_only=True)
                if self.patient_service is not None
                else []
            )
            if not patients:
                item = QListWidgetItem("暂无启用患者")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                patient_list.addItem(item)
                return

            selected_item: QListWidgetItem | None = None
            for patient in patients:
                item = QListWidgetItem(patient.display_label)
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    str(patient.patient_id),
                )
                patient_list.addItem(item)
                if (
                    self.current_patient is not None
                    and patient.patient_id == self.current_patient.patient_id
                ):
                    selected_item = item
            if selected_item is not None:
                patient_list.setCurrentItem(selected_item)
        finally:
            patient_list.blockSignals(False)

    def _select_workbench_patient(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        if current is None or self.patient_service is None:
            return
        raw_patient_id = current.data(Qt.ItemDataRole.UserRole)
        if raw_patient_id is None:
            return
        if self._task_in_progress():
            self.workbench_patient_list.blockSignals(True)
            try:
                if previous is None:
                    self.workbench_patient_list.clearSelection()
                    self.workbench_patient_list.setCurrentRow(-1)
                else:
                    self.workbench_patient_list.setCurrentItem(previous)
            finally:
                self.workbench_patient_list.blockSignals(False)
            QMessageBox.information(
                self,
                "任务进行中",
                "请先结束当前任务，再切换患者。",
            )
            return
        patient = self.patient_service.get_patient(UUID(str(raw_patient_id)))
        self._set_current_patient(patient)

    def _task_config_revisions(self) -> dict[str, int]:
        revisions: dict[str, int] = {}
        for definition in CLINICAL_TASK_ORDER:
            if definition.module_id == "eye_observation":
                continue
            revisions[definition.module_id] = self._task_config_store.load(
                definition.module_id,
                patient_id=(
                    str(self.current_patient.patient_id)
                    if self.current_patient is not None
                    else None
                ),
            ).revision
        return revisions

    def _recent_completion_by_module(self) -> dict[str, str]:
        if self.current_patient is None or self.experiment_session_service is None:
            return {}
        recent: dict[str, str] = {}
        for session in self.experiment_session_service.list_sessions_for_patient(
            self.current_patient.patient_id
        ):
            if (
                session.status is ExperimentSessionStatus.COMPLETED
                and session.module_id not in recent
            ):
                ended = session.ended_at or session.updated_at
                recent[session.module_id] = ended.astimezone().strftime("%Y-%m-%d")
        return recent

    def _current_test_plan(self) -> TestPlan | None:
        if self.current_patient is None:
            return None
        return self._test_plan_store.load(str(self.current_patient.patient_id))

    def _set_next_task_text(self, text: str, *, prominent: bool = False) -> None:
        self.next_task_label.setText(text)
        self.next_task_label.setStyleSheet(
            f"font-size:{'26px' if prominent else '22px'};font-weight:800;color:#17324d;"
        )

    @staticmethod
    def _plan_retryable_step(plan: TestPlan) -> TestPlanStep | None:
        return next(
            (
                step
                for step in reversed(plan.steps)
                if step.selected
                and step.status
                in {
                    TestPlanStepStatus.ABORTED,
                    TestPlanStepStatus.FAILED,
                }
            ),
            None,
        )

    def _refresh_workbench_plan(self) -> None:
        if not hasattr(self, "plan_progress_label"):
            return
        plan = self._current_test_plan()
        patient_selected = self.current_patient is not None
        busy = self._task_in_progress()

        self.plan_button.setEnabled(patient_selected and not busy)
        self.history_button.setEnabled(True)

        if not patient_selected:
            self.plan_progress_label.setText("尚未选择患者")
            self._set_next_task_text(
                "选择患者后可编排本次测试。",
                prominent=True,
            )
            self.start_next_button.setEnabled(False)
            self.retry_plan_button.setEnabled(False)
            self.finish_plan_button.setEnabled(False)
            return

        if plan is None:
            self.plan_progress_label.setText("本次测试：尚未编排")
            self._set_next_task_text("点击“编排本次测试”选择项目；0 号眼动采集与复核默认不选。")
            self.start_next_button.setEnabled(False)
            self.retry_plan_button.setEnabled(False)
            self.finish_plan_button.setEnabled(False)
            return

        terminal, selected = plan.progress
        counts = {
            status: sum(step.selected and step.status is status for step in plan.steps)
            for status in TestPlanStepStatus
        }
        self.plan_progress_label.setText(
            f"本次测试进度：{terminal}/{selected}　"
            f"完成 {counts[TestPlanStepStatus.COMPLETED]} · "
            f"跳过 {counts[TestPlanStepStatus.SKIPPED]} · "
            f"取消 {counts[TestPlanStepStatus.ABORTED]} · "
            f"失败 {counts[TestPlanStepStatus.FAILED]}"
        )

        running = next(
            (
                step
                for step in plan.steps
                if step.selected and step.status is TestPlanStepStatus.RUNNING
            ),
            None,
        )
        next_step = plan.next_pending_step
        definitions = {definition.step_id: definition for definition in CLINICAL_TASK_ORDER}
        if running is not None:
            definition = definitions[running.step_id]
            self._set_next_task_text(f"正在进行：{definition.clinical_number} · {definition.title}")
        elif next_step is not None:
            definition = definitions[next_step.step_id]
            rest_note = (
                "；完成后安排休息/警觉复核"
                if next_step.block_id in plan.rest_after_step_ids
                else ""
            )
            self._set_next_task_text(
                f"下一项：{definition.clinical_number} · {definition.title}{rest_note}"
            )
        else:
            self._set_next_task_text("本次所选步骤均已终止；请复核不同状态后结束本次测试。")

        retryable = self._plan_retryable_step(plan)
        self.start_next_button.setEnabled(not busy and running is None and next_step is not None)
        self.retry_plan_button.setEnabled(not busy and running is None and retryable is not None)
        self.finish_plan_button.setEnabled(
            not busy and running is None and next_step is None and selected > 0
        )

    def _refresh_workbench_recent_sessions(self) -> None:
        if not hasattr(self, "workbench_recent_list"):
            return
        self.workbench_recent_list.clear()
        if self.current_patient is None or self.experiment_session_service is None:
            self.workbench_recent_list.addItem("暂无实验记录")
            self.recent_result_label.setText("最近结果：暂无")
            return

        sessions = self.experiment_session_service.list_sessions_for_patient(
            self.current_patient.patient_id
        )
        if not sessions:
            self.workbench_recent_list.addItem("暂无实验记录")
            self.recent_result_label.setText("最近结果：暂无")
            return

        titles = {module.module_id: module.title for module in DEFAULT_MODULES}
        status_labels = {
            ExperimentSessionStatus.CREATED: "已创建",
            ExperimentSessionStatus.RUNNING: "进行中",
            ExperimentSessionStatus.COMPLETED: "已完成",
            ExperimentSessionStatus.ABORTED: "已取消",
            ExperimentSessionStatus.FAILED: "失败",
        }
        for session in sessions[:6]:
            self.workbench_recent_list.addItem(
                f"{session.created_at.astimezone().strftime('%m-%d %H:%M')} · "
                f"{titles.get(session.module_id, session.module_id)} · "
                f"{status_labels[session.status]}"
            )
        latest = sessions[0]
        self.recent_result_label.setText(
            "最近结果："
            f"{titles.get(latest.module_id, latest.module_id)} · "
            f"{status_labels[latest.status]}"
        )

    def _open_test_plan_dialog(self, checked: bool = False) -> None:
        del checked
        if self.current_patient is None:
            QMessageBox.information(
                self,
                "尚未选择患者",
                "请先从左栏选择一名启用患者。",
            )
            return
        if self._task_in_progress():
            QMessageBox.information(
                self,
                "任务进行中",
                "请先结束当前任务，再修改本次测试编排。",
            )
            return

        patient_id = str(self.current_patient.patient_id)
        current = self._test_plan_store.load(patient_id)
        if current is None:
            current = TestPlan.default(
                patient_id,
                config_revisions=self._task_config_revisions(),
            )
        dialog = TestPlanDialog(
            self.settings,
            self.current_patient,
            current,
            self._task_config_store,
            gaze_status_text=self._gaze_source_status_text(),
            recent_completion_by_module=self._recent_completion_by_module(),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.saved_plan is None:
            return
        try:
            self._test_plan_store.save(
                dialog.saved_plan,
                expected_revision=current.revision,
            )
        except TestPlanConflict:
            QMessageBox.warning(
                self,
                "本次测试编排已更新",
                "计划已被其他窗口修改。请重新打开后确认。",
            )
        self._refresh_patient_summary()

    def _start_next_plan_step(self, checked: bool = False) -> None:
        del checked
        plan = self._current_test_plan()
        if plan is None or self.current_patient is None:
            return
        if self._task_in_progress() or any(
            step.status is TestPlanStepStatus.RUNNING for step in plan.steps
        ):
            QMessageBox.information(
                self,
                "任务进行中",
                "请先结束当前任务，再开始下一项。",
            )
            return
        step = plan.next_pending_step
        if step is None:
            return
        if step.module_id != "eye_observation":
            current_revision = self._task_config_store.load(
                step.module_id,
                patient_id=str(self.current_patient.patient_id),
            ).revision
            if current_revision != step.config_revision:
                QMessageBox.warning(
                    self,
                    "设置已更新，请重新确认",
                    "本任务的共享设置在编排后发生变化。请重新打开“编排本次测试”确认设置版本。",
                )
                return

        module = next(item for item in DEFAULT_MODULES if item.module_id == step.module_id)
        if step.module_id == "eye_observation":
            self._open_eye_observation_module(
                module,
                plan_step_id=step.block_id,
            )
        else:
            self._open_gaze_task_module(
                module,
                config_revision=step.config_revision,
                game_mode=step.game_mode,
                plan_step_id=step.block_id,
            )

    def _retry_plan_step(self, checked: bool = False) -> None:
        del checked
        plan = self._current_test_plan()
        if plan is None or self._task_in_progress():
            return
        step = self._plan_retryable_step(plan)
        if step is None:
            return
        updated = plan.replace_step(step.prepare_retry())
        try:
            self._test_plan_store.save(
                updated,
                expected_revision=plan.revision,
            )
        except TestPlanConflict:
            QMessageBox.warning(
                self,
                "本次测试编排已更新",
                "计划已被其他窗口修改。请刷新后重试。",
            )
        self._refresh_workbench_plan()

    def _finish_current_test_plan(self, checked: bool = False) -> None:
        del checked
        plan = self._current_test_plan()
        if plan is None or plan.next_pending_step is not None:
            return
        if any(step.status is TestPlanStepStatus.RUNNING for step in plan.steps):
            return
        result = QMessageBox.question(
            self,
            "结束本次测试",
            "将关闭当前编排。实验会话、报告和历史记录仍完整保留。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        try:
            self._test_plan_store.close(
                plan.patient_id,
                expected_revision=plan.revision,
            )
        except TestPlanConflict:
            QMessageBox.warning(
                self,
                "本次测试编排已更新",
                "计划已被其他窗口修改，未关闭。",
            )
        self._refresh_workbench_plan()

    def _mark_plan_step_running(
        self,
        patient_id: UUID,
        block_id: str,
        session_id: UUID,
    ) -> None:
        plan = self._test_plan_store.load(str(patient_id))
        if plan is None:
            raise RuntimeError("当前患者的测试编排已不存在。")
        step = next(
            (item for item in plan.steps if item.block_id == block_id),
            None,
        )
        if step is None:
            raise RuntimeError("测试编排中找不到即将启动的步骤。")
        updated = plan.replace_step(step.start(str(session_id)))
        self._test_plan_store.save(
            updated,
            expected_revision=plan.revision,
        )
        self._refresh_workbench_plan()

    def _finish_plan_step_for_session(
        self,
        patient_id: UUID,
        session_id: UUID,
        status: ExperimentSessionStatus,
    ) -> None:
        plan = self._test_plan_store.load(str(patient_id))
        if plan is None:
            return
        step = next(
            (
                item
                for item in plan.steps
                if item.session_id == str(session_id) and item.status is TestPlanStepStatus.RUNNING
            ),
            None,
        )
        if step is None:
            return
        mapped = {
            ExperimentSessionStatus.COMPLETED: TestPlanStepStatus.COMPLETED,
            ExperimentSessionStatus.ABORTED: TestPlanStepStatus.ABORTED,
            ExperimentSessionStatus.FAILED: TestPlanStepStatus.FAILED,
        }.get(status)
        if mapped is None:
            return
        try:
            self._test_plan_store.save(
                plan.replace_step(step.finish(mapped)),
                expected_revision=plan.revision,
            )
        except TestPlanConflict:
            QMessageBox.warning(
                self,
                "本次测试状态未同步",
                "实验记录已保存，但测试编排被其他窗口修改。请重新打开编排核对。",
            )
        self._refresh_patient_summary()

    def _show_patient_placeholder(
        self,
        checked: bool = False,
    ) -> None:
        """Open the patient management dialog."""
        del checked

        if self.patient_service is None:
            QMessageBox.warning(
                self,
                "患者数据库未连接",
                "无法打开患者管理界面。",
            )
            return

        dialog = PatientManagementDialog(
            self.patient_service,
            self,
            experiment_session_service=self.experiment_session_service,
            is_patient_session_active=self._is_patient_task_active,
        )
        result = dialog.exec()

        if result == QDialog.DialogCode.Accepted and dialog.selected_patient is not None:
            self._set_current_patient(dialog.selected_patient)
        else:
            self._reload_current_patient()
            self._refresh_patient_summary()

    def _open_module(
        self,
        module: ModuleDefinition,
        checked: bool = False,
    ) -> None:
        """Open an implemented module or its placeholder."""
        del checked

        if module.module_id == "eye_observation":
            self._open_eye_observation_module(module)
            return

        if module.module_id in {
            "tracking_ball",
            "binary_horizontal",
            "binary_vertical",
            "screen_keyboard",
            "multiple_choice",
            "image_choice",
            "instruction_fixation",
            "gaze_games",
            "visual_preference",
        }:
            self._open_gaze_task_module(module)
            return

        self._show_module_placeholder(module)

    def _open_eye_observation_module(
        self,
        module: ModuleDefinition,
        *,
        plan_step_id: str | None = None,
    ) -> None:
        """Create a session and open the eye workbench."""
        if self._task_in_progress():
            QMessageBox.information(
                self,
                "任务已在运行",
                "已有任务正在运行，请先结束当前任务。",
            )
            return
        if self.current_patient is None:
            QMessageBox.warning(
                self,
                "尚未选择患者",
                "请先在患者管理中选择一名启用患者。",
            )
            return

        if self.experiment_session_service is None:
            QMessageBox.warning(
                self,
                "实验会话服务未连接",
                "无法创建眼动采集会话。",
            )
            return

        session_id: UUID | None = None

        try:
            session = self.experiment_session_service.create_session(
                CreateExperimentSessionRequest(
                    patient_id=(self.current_patient.patient_id),
                    module_id=module.module_id,
                )
            )
            session_id = session.session_id

            self.experiment_session_service.start_session(session_id)
            session_directory = self.experiment_session_service.resolve_session_directory(
                session_id
            )
            dataset_directory = session_directory / "eye_observations"

            workbench = CameraPreviewWindow(
                patient_key=str(self.current_patient.patient_id),
                patient_display_label=self.current_patient.display_label,
                dataset_directory=dataset_directory,
            )
            if plan_step_id is not None:
                self._mark_plan_step_running(
                    self.current_patient.patient_id,
                    plan_step_id,
                    session_id,
                )
        except Exception as error:
            if session_id is not None:
                with suppress(Exception):
                    self.experiment_session_service.abort_session(
                        session_id,
                        str(error),
                    )

            QMessageBox.critical(
                self,
                "无法启动眼动工作台",
                str(error),
            )
            return

        workbench.artifacts_saved.connect(
            partial(
                self._register_eye_artifacts,
                session_id,
                session_directory,
            )
        )
        workbench.workbench_closed.connect(
            partial(
                self._finish_eye_session,
                session_id,
            )
        )

        self._eye_windows[session_id] = workbench
        self._refresh_task_controls()

        workbench.show()
        workbench.raise_()
        workbench.activateWindow()

    def _register_eye_artifacts(
        self,
        session_id: UUID,
        session_directory: Path,
        paths: tuple[Path, ...],
    ) -> None:
        """Register files saved by the eye workbench."""
        if self.experiment_session_service is None:
            return

        image_suffixes = {
            ".bmp",
            ".jpeg",
            ".jpg",
            ".png",
            ".tif",
            ".tiff",
            ".webp",
        }
        resolved_session_directory = session_directory.resolve()

        for raw_path in paths:
            path = Path(raw_path).resolve()

            try:
                relative_path = path.relative_to(resolved_session_directory).as_posix()
            except ValueError:
                QMessageBox.warning(
                    self,
                    "跳过会话目录外文件",
                    str(path),
                )
                continue

            kind = (
                SessionArtifactKind.CAMERA_FRAMES
                if path.suffix.lower() in image_suffixes
                else SessionArtifactKind.OTHER
            )
            mime_type = mimetypes.guess_type(path.name)[0]

            try:
                self.experiment_session_service.register_artifact(
                    RegisterSessionArtifactRequest(
                        session_id=session_id,
                        kind=kind,
                        relative_path=relative_path,
                        source="eye_workbench",
                        mime_type=mime_type,
                        size_bytes=path.stat().st_size,
                    )
                )
            except DuplicateSessionArtifactError:
                continue
            except Exception as error:
                QMessageBox.warning(
                    self,
                    "会话文件登记失败",
                    f"{relative_path}\n{error}",
                )

    def _finish_eye_session(
        self,
        session_id: UUID,
    ) -> None:
        """Complete the session when its workbench closes."""
        self._eye_windows.pop(
            session_id,
            None,
        )

        if self.experiment_session_service is None:
            self._refresh_task_controls()
            return

        try:
            session = self.experiment_session_service.get_session(session_id)

            if session.status is ExperimentSessionStatus.RUNNING:
                self.experiment_session_service.complete_session(session_id)
            elif session.status is ExperimentSessionStatus.CREATED:
                self.experiment_session_service.abort_session(
                    session_id,
                    "Workbench closed before acquisition started.",
                )
            session = self.experiment_session_service.get_session(session_id)
            self._finish_plan_step_for_session(
                session.patient_id,
                session_id,
                session.status,
            )
        except Exception as error:
            QMessageBox.warning(
                self,
                "实验会话结束失败",
                str(error),
            )
        self._refresh_task_controls()
        self._refresh_patient_summary()

    def _task_in_progress(self) -> bool:
        return bool(self._active_gaze_module_ids or self._eye_windows)

    def _is_patient_task_active(self, patient_id: UUID) -> bool:
        if any(launch.patient_id == patient_id for launch in self._gaze_launches.values()):
            return True
        if self.experiment_session_service is None:
            return False
        for session_id in self._eye_windows:
            with suppress(Exception):
                session = self.experiment_session_service.get_session(session_id)
                if session.patient_id == patient_id and not session.is_terminal:
                    return True
        return False

    def _refresh_task_controls(self) -> None:
        busy = self._task_in_progress()
        if self._update_process is None:
            self.update_button.setEnabled(not busy)
        self.stop_task_button.setEnabled(busy)
        for module_id, button in self.module_buttons.items():
            button.setEnabled(not busy)
            button.setText(
                "任务运行中…"
                if busy and module_id in self._active_gaze_module_ids
                else str(button.property("idleText") or "打开项目")
            )
        if hasattr(self, "workbench_patient_list"):
            self.workbench_patient_list.setEnabled(not busy)
            self._refresh_workbench_plan()

    def _stop_active_tasks(self, checked: bool = False) -> None:
        del checked
        if not self._task_in_progress():
            return
        result = QMessageBox.question(
            self,
            "停止当前任务",
            "确定停止当前任务并保留已产生的数据以供复核吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        for process in tuple(self._gaze_processes.values()):
            process.terminate()
            if not process.waitForFinished(1_500):
                process.kill()

        for session_id, workbench in tuple(self._eye_windows.items()):
            if self.experiment_session_service is not None:
                with suppress(Exception):
                    session = self.experiment_session_service.get_session(session_id)
                    if not session.is_terminal:
                        self.experiment_session_service.abort_session(
                            session_id,
                            "管理员停止任务。",
                        )
            workbench.close()

    def _set_gaze_module_busy(
        self,
        module_id: str,
        busy: bool,
    ) -> None:
        """Reserve or release one gaze-task module."""

        if busy:
            self._active_gaze_module_ids.add(module_id)
        else:
            self._active_gaze_module_ids.discard(module_id)

        self._refresh_gaze_status()
        self._refresh_task_controls()

    def _open_gaze_task_module(
        self,
        module: ModuleDefinition,
        *,
        config_revision: int | None = None,
        game_mode: str | None = None,
        plan_step_id: str | None = None,
    ) -> None:
        """Create a patient session and launch a gaze task."""

        if self.current_patient is None:
            QMessageBox.warning(
                self,
                "尚未选择患者",
                "请先在患者管理中选择一名启用患者。",
            )
            return

        if self.experiment_session_service is None:
            QMessageBox.warning(
                self,
                "实验会话服务未连接",
                "无法创建眼动任务会话。",
            )
            return

        if self._task_in_progress():
            QMessageBox.information(
                self,
                "任务已在运行",
                "已有任务正在启动、设置或运行，请先关闭当前任务。",
            )
            return

        patient_id = self.current_patient.patient_id
        self._set_gaze_module_busy(module.module_id, True)
        self.lower()
        self._open_patient_display()
        self._launch_gaze_task_process(
            module,
            patient_id=patient_id,
            config_revision=config_revision,
            game_mode=game_mode,
            plan_step_id=plan_step_id,
        )

    def _launch_gaze_task_process(
        self,
        module: ModuleDefinition,
        *,
        patient_id: UUID,
        config_revision: int | None,
        game_mode: str | None,
        plan_step_id: str | None,
    ) -> None:
        """Launch a reserved gaze-task child process."""
        launch: GazeTaskLaunch | None = None
        session_service = self.experiment_session_service

        try:
            if session_service is None:
                raise RuntimeError("实验会话服务未连接。")

            launch = create_gaze_task_launch(
                session_service,
                patient_id=patient_id,
                module_id=module.module_id,
            )
            if plan_step_id is not None:
                self._mark_plan_step_running(
                    patient_id,
                    plan_step_id,
                    launch.session_id,
                )

            process = QProcess(self)
            environment = QProcessEnvironment.systemEnvironment()

            for name, value in launch.process_environment.items():
                environment.insert(name, value)

            environment.insert(
                "OCULIDOC_GAZE_SOURCE",
                self.settings.gaze_source,
            )
            environment.insert(
                "OCULIDOC_DATA_DIR",
                str(self.settings.data_dir),
            )
            process.setProcessEnvironment(environment)
            program, arguments = gaze_task_process_command(
                launch.command,
                config_revision=config_revision,
                game_mode=game_mode,
            )
            process.setProgram(program)
            process.setArguments(arguments)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.finished.connect(
                partial(
                    self._finish_gaze_task_process,
                    launch.session_id,
                )
            )

            self._gaze_processes[launch.session_id] = process
            self._gaze_launches[launch.session_id] = launch

            process.start()

            if not process.waitForStarted(5_000):
                raise RuntimeError(process.errorString() or "任务子进程启动失败。")
        except Exception as error:
            self._set_gaze_module_busy(
                module.module_id,
                False,
            )
            self._restore_admin_window()

            if launch is not None and session_service is not None:
                self._gaze_processes.pop(
                    launch.session_id,
                    None,
                )
                self._gaze_launches.pop(
                    launch.session_id,
                    None,
                )

                with suppress(Exception):
                    session = session_service.get_session(launch.session_id)

                    if not session.is_terminal:
                        session_service.fail_session(
                            launch.session_id,
                            str(error),
                        )
                    session = session_service.get_session(launch.session_id)
                    self._finish_plan_step_for_session(
                        launch.patient_id,
                        launch.session_id,
                        session.status,
                    )

            with suppress(Exception):
                result_state = self._publish_patient_display(
                    f"{module.title}启动失败\n请联系管理员",
                    mode=PatientDisplayMode.ERROR,
                    task_id=module.module_id,
                )
                QTimer.singleShot(
                    RESULT_DISPLAY_MILLISECONDS,
                    partial(
                        self._reset_patient_display_after_result,
                        result_state.revision,
                    ),
                )

            self._show_timed_task_message(
                QMessageBox.Icon.Critical,
                "无法启动眼动任务",
                str(error),
            )

    def _finish_gaze_task_process(
        self,
        session_id: UUID,
        exit_code: int,
        exit_status: object,
    ) -> None:
        """Register child outputs and close the database session."""

        del exit_status
        process = self._gaze_processes.pop(
            session_id,
            None,
        )
        launch = self._gaze_launches.pop(
            session_id,
            None,
        )

        if launch is not None:
            self._set_gaze_module_busy(
                launch.module_id,
                False,
            )

        self._restore_admin_window()

        if process is None or launch is None or self.experiment_session_service is None:
            return

        raw_output = bytes(process.readAllStandardOutput())  # type: ignore[call-overload]
        process_output = raw_output.decode(
            "utf-8",
            errors="replace",
        )

        try:
            status = finalize_gaze_task_launch(
                self.experiment_session_service,
                launch,
                exit_code=exit_code,
                process_output=process_output,
            )
        except Exception as error:
            with suppress(Exception):
                session = self.experiment_session_service.get_session(session_id)

                if not session.is_terminal:
                    self.experiment_session_service.fail_session(
                        session_id,
                        str(error),
                    )
                session = self.experiment_session_service.get_session(session_id)
                self._finish_plan_step_for_session(
                    launch.patient_id,
                    session_id,
                    session.status,
                )

            with suppress(Exception):
                result_state = self._publish_patient_display(
                    "任务记录处理失败\n请联系管理员",
                    mode=PatientDisplayMode.ERROR,
                    task_id=launch.module_id,
                )
                QTimer.singleShot(
                    RESULT_DISPLAY_MILLISECONDS,
                    partial(
                        self._reset_patient_display_after_result,
                        result_state.revision,
                    ),
                )

            self._show_timed_task_message(
                QMessageBox.Icon.Warning,
                "眼动任务会话结束失败",
                str(error),
            )
            return

        self._finish_plan_step_for_session(
            launch.patient_id,
            session_id,
            status,
        )

        patient_label = "该患者"
        if self.patient_service is not None:
            with suppress(Exception):
                patient_label = self.patient_service.get_patient(launch.patient_id).display_label

        if status is ExperimentSessionStatus.COMPLETED:
            result_state = self._publish_patient_display(
                "任务已结束\n请休息",
                mode=PatientDisplayMode.RESULT,
                task_id=launch.module_id,
            )
            QTimer.singleShot(
                RESULT_DISPLAY_MILLISECONDS,
                partial(
                    self._reset_patient_display_after_result,
                    result_state.revision,
                ),
            )
            self._show_timed_task_message(
                QMessageBox.Icon.Information,
                "眼动任务已保存",
                f"任务记录已保存至 {patient_label} 的实验历史。",
            )
        elif status is ExperimentSessionStatus.ABORTED:
            self._reset_patient_display()
            self._show_timed_task_message(
                QMessageBox.Icon.Information,
                "眼动任务已取消",
                "设置窗口关闭，未产生正式任务记录。",
            )
        else:
            message = "任务进程未正常完成。"

            result_state = self._publish_patient_display(
                "任务运行异常\n请联系管理员",
                mode=PatientDisplayMode.ERROR,
                task_id=launch.module_id,
            )
            QTimer.singleShot(
                RESULT_DISPLAY_MILLISECONDS,
                partial(
                    self._reset_patient_display_after_result,
                    result_state.revision,
                ),
            )

            if process_output.strip():
                message += "\n\n进程输出：\n" + process_output.strip()[-2_000:]

            self._show_timed_task_message(
                QMessageBox.Icon.Warning,
                "眼动任务失败",
                message,
            )

    def _reset_patient_display_after_result(self, result_revision: int) -> None:
        state = self._lan_state_store.load()

        if state.revision == result_revision and state.mode in {
            PatientDisplayMode.RESULT,
            PatientDisplayMode.ERROR,
        }:
            self._reset_patient_display()

    def _show_module_placeholder(
        self,
        module: ModuleDefinition,
        checked: bool = False,
    ) -> None:
        del checked
        QMessageBox.information(
            self,
            module.title,
            f"{module.title}模块已登记，具体实验逻辑将在后续里程碑实现。",
        )

    def _request_application_exit(self, checked: bool = False) -> None:
        del checked
        result = QMessageBox.question(
            self,
            "退出 OculiDoC",
            "确定要立即退出程序吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._lan_poll_timer.stop()
        self._lan_command_timer.stop()
        self._pairing_hide_timer.stop()

        with suppress(Exception):
            self._lan_state_store.set_closed()

        if self._pairing_dialog is not None:
            self._pairing_dialog.close()

        if (
            self._backend_process is not None
            and self._backend_process.state() != QProcess.ProcessState.NotRunning
        ):
            self._backend_process.terminate()

            if not self._backend_process.waitForFinished(1_500):
                self._backend_process.kill()
                self._backend_process.waitForFinished(1_000)

        for workbench in tuple(self._eye_windows.values()):
            workbench.close()

        self._patient_window.close()
        super().closeEvent(event)
