"""Administrator desktop dashboard."""

import mimetypes
import os
from contextlib import suppress
from functools import partial
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import (
    QProcess,
    QProcessEnvironment,
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
from oculidoc.config import GazeDeviceConfig, GazeDeviceConfigStore, Settings
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
from oculidoc.vision.camera_preview_window import (
    CameraPreviewWindow,
)

RESULT_DISPLAY_MILLISECONDS = 1_500


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
            QFrame#panel, QFrame#moduleCard {
                background: white;
                border: 1px solid #d9e3ec;
                border-radius: 14px;
            }
            QPushButton {
                min-height: 38px;
                border-radius: 9px;
                padding: 4px 16px;
                font-family: "Microsoft YaHei UI";
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton#primaryButton { background: #1565c0; color: white; border: none; }
            QPushButton#secondaryButton {
                background: #edf4fb;
                color: #184e77;
                border: 1px solid #bfd3e4;
            }
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
            """
        )

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        root.addLayout(self._build_header())
        root.addWidget(self._build_patient_panel())
        root.addWidget(self._build_module_area(), 1)
        root.addWidget(self._build_status_panel())

        self.setCentralWidget(central)
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
                self._open_patient_display,
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
                return "眼动源：模拟模式（仅工程测试）"

            return (
                "眼动源：模拟模式（仅工程测试）"
                f" · {preflight.sample_rate_hz:.0f} Hz"
                f" · 有效率 {preflight.valid_ratio:.0%}"
            )

        labels = {
            "tobii_stream_engine": "Tobii Eye Tracker 5",
            "tobii_legacy_bridge": "Tobii 兼容桥接",
        }
        source_name = labels.get(self.settings.gaze_source, self.settings.gaze_source)

        if preflight is None:
            return f"眼动源：{source_name} · 尚未预检"

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

        header.addWidget(logo_label)
        header.addLayout(titles)
        header.addStretch(1)
        header.addWidget(emergency_button)
        return header

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
        )
        dialog.exec()

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

        if self._active_gaze_module_ids:
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

        if self._active_gaze_module_ids:
            raise LanCommandRejected("已有任务正在启动、设置或运行，请先结束当前任务。")

        if config_revision is None:
            raise LanCommandRejected("远程启动缺少任务设置版本。")

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
        self._open_gaze_task_module(module, config_revision=config_revision)

        if module_id not in self._active_gaze_module_ids:
            raise LanCommandRejected("任务进程未能启动，请查看电脑端提示。")

        return f"{module.title}已按设置版本 {config_revision} 直接启动。"

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

    def _reload_current_patient(self) -> None:
        """Reload or clear the current patient."""
        if self.patient_service is None or self.current_patient is None:
            return

        patient = self.patient_service.get_patient(self.current_patient.patient_id)

        if patient.is_active:
            self.current_patient = patient
        else:
            self.current_patient = None

    def _set_current_patient(
        self,
        patient: Patient,
    ) -> None:
        """Set and display the current patient."""
        if not patient.is_active:
            return

        self.current_patient = patient
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
        }:
            self._open_gaze_task_module(module)
            return

        self._show_module_placeholder(module)

    def _open_eye_observation_module(
        self,
        module: ModuleDefinition,
    ) -> None:
        """Create a session and open the eye workbench."""
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
                dataset_directory=dataset_directory,
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
        except Exception as error:
            QMessageBox.warning(
                self,
                "实验会话结束失败",
                str(error),
            )

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

        button = self.module_buttons.get(module_id)

        if button is None:
            return

        button.setEnabled(not busy)
        button.setText("任务运行中…" if busy else "打开项目")

    def _open_gaze_task_module(
        self,
        module: ModuleDefinition,
        *,
        config_revision: int | None = None,
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

        if self._active_gaze_module_ids:
            QMessageBox.information(
                self,
                "任务已在运行",
                "已有任务正在启动、设置或运行，请先关闭当前任务。",
            )
            return

        patient_id = self.current_patient.patient_id
        self._set_gaze_module_busy(module.module_id, True)
        self._open_patient_display()
        self._launch_gaze_task_process(
            module,
            patient_id=patient_id,
            config_revision=config_revision,
        )

    def _launch_gaze_task_process(
        self,
        module: ModuleDefinition,
        *,
        patient_id: UUID,
        config_revision: int | None,
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

            with suppress(Exception):
                self._publish_patient_display(
                    f"{module.title}启动失败\n请联系管理员",
                    mode=PatientDisplayMode.ERROR,
                    task_id=module.module_id,
                )

            QMessageBox.critical(
                self,
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

        if process is None or launch is None or self.experiment_session_service is None:
            return

        raw_output = bytes(process.readAllStandardOutput())
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

            with suppress(Exception):
                self._publish_patient_display(
                    "任务记录处理失败\n请联系管理员",
                    mode=PatientDisplayMode.ERROR,
                    task_id=launch.module_id,
                )

            QMessageBox.warning(
                self,
                "眼动任务会话结束失败",
                str(error),
            )
            return

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
            QMessageBox.information(
                self,
                "眼动任务已保存",
                (f"任务记录已关联到当前患者的实验会话。\n目录：{launch.session_directory}"),
            )
        elif status is ExperimentSessionStatus.ABORTED:
            self._reset_patient_display()
            QMessageBox.information(
                self,
                "眼动任务已取消",
                "设置窗口关闭，未产生正式任务记录。",
            )
        else:
            message = "任务进程未正常完成。"

            self._publish_patient_display(
                "任务运行异常\n请联系管理员",
                mode=PatientDisplayMode.ERROR,
                task_id=launch.module_id,
            )

            if process_output.strip():
                message += "\n\n进程输出：\n" + process_output.strip()[-2_000:]

            QMessageBox.warning(
                self,
                "眼动任务失败",
                message,
            )

    def _reset_patient_display_after_result(self, result_revision: int) -> None:
        state = self._lan_state_store.load()

        if state.revision == result_revision and state.mode is PatientDisplayMode.RESULT:
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
