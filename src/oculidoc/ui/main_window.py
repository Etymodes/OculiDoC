"""Administrator desktop dashboard."""

from functools import partial

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from oculidoc.application import PatientService
from oculidoc.config import Settings
from oculidoc.modules.registry import DEFAULT_MODULES, ModuleDefinition
from oculidoc.ui.patient_window import PatientDisplayWindow


class AdminMainWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        patient_service: PatientService | None = None,
    ) -> None:
        super().__init__()

        self.settings = settings
        self.patient_service = patient_service
        self.module_buttons: dict[str, QPushButton] = {}
        self._patient_window = PatientDisplayWindow()
        self._patient_window.exit_requested.connect(self._restore_admin_window)

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

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        titles = QVBoxLayout()

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

        header.addLayout(titles)
        header.addStretch(1)
        header.addWidget(emergency_button)
        return header

    def _build_patient_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)

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

        display_button = QPushButton("打开患者显示端")
        display_button.setObjectName("primaryButton")
        display_button.clicked.connect(self._open_patient_display)

        layout.addLayout(text, 1)
        layout.addWidget(manage_button)
        layout.addWidget(display_button)
        return panel

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
        button.clicked.connect(partial(self._show_module_placeholder, module))
        self.module_buttons[module.module_id] = button

        layout.addWidget(title)
        layout.addWidget(description, 1)
        layout.addWidget(button)
        return card

    def _build_status_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(18, 13, 18, 13)

        for text in (
            "眼动源：模拟数据源",
            f"本地后台：未启动 · {self.settings.admin_base_url}",
            self._patient_status_text(),
        ):
            label = QLabel(text)
            label.setObjectName("subtitle")
            layout.addWidget(label)
            layout.addStretch(1)

        return panel

    def _open_patient_display(self, checked: bool = False) -> None:
        del checked
        self._patient_window.set_placeholder("患者显示端已打开\n等待管理员启动实验")
        self._patient_window.showFullScreen()
        self._patient_window.raise_()
        self._patient_window.activateWindow()

    def _restore_admin_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _show_patient_placeholder(self, checked: bool = False) -> None:
        del checked
        QMessageBox.information(self, "患者管理", "患者档案将在下一里程碑实现。")

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
        self._patient_window.close()
        super().closeEvent(event)
