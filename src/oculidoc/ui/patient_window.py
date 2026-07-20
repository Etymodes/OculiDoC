"""Patient-facing display window."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from oculidoc.lan_control import LanControlState, PatientDisplayMode

_MODE_LABELS = {
    PatientDisplayMode.CLOSED: "已关闭",
    PatientDisplayMode.IDLE: "待机",
    PatientDisplayMode.READY: "准备",
    PatientDisplayMode.PREVIEW: "提示",
    PatientDisplayMode.RUNNING: "任务进行中",
    PatientDisplayMode.PAUSED: "已暂停",
    PatientDisplayMode.RESULT: "任务结束",
    PatientDisplayMode.ERROR: "异常",
}

_MODE_COLORS = {
    PatientDisplayMode.CLOSED: "#5a7184",
    PatientDisplayMode.IDLE: "#176b36",
    PatientDisplayMode.READY: "#8a5a00",
    PatientDisplayMode.PREVIEW: "#1565c0",
    PatientDisplayMode.RUNNING: "#176b36",
    PatientDisplayMode.PAUSED: "#8a5a00",
    PatientDisplayMode.RESULT: "#176b36",
    PatientDisplayMode.ERROR: "#b42318",
}


def patient_message_font_size(text: str) -> int:
    """Choose a large readable pixel size from the message length."""
    character_count = len("".join(text.split()))

    if character_count <= 10:
        return 84

    if character_count <= 24:
        return 72

    if character_count <= 48:
        return 60

    if character_count <= 90:
        return 48

    return 40


class PatientDisplayWindow(QWidget):
    exit_requested = Signal()

    def __init__(self) -> None:
        super().__init__()

        self.setObjectName("patientDisplayWindow")
        self.setWindowTitle("OculiDoC 患者显示端")
        self.setMinimumSize(960, 640)
        self.setStyleSheet(
            """
            QWidget#patientDisplayWindow { background: #f4f7fb; }
            QLabel#patientTitle {
                color: #17324d;
                font-family: "Microsoft YaHei UI";
                font-size: 28px;
                font-weight: 700;
            }
            QLabel#patientPlaceholder {
                color: #203f58;
                font-family: "Microsoft YaHei UI";
                font-weight: 700;
                padding: 34px;
            }
            QPushButton#emergencyExitButton {
                background: #b42318;
                color: white;
                border: none;
                border-radius: 10px;
                padding: 14px 24px;
                font-family: "Microsoft YaHei UI";
                font-size: 18px;
                font-weight: 700;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(18)

        header = QHBoxLayout()
        title = QLabel("OculiDoC 患者显示端")
        title.setObjectName("patientTitle")

        self.state_label = QLabel()
        self.state_label.setObjectName("patientState")

        emergency_button = QPushButton("紧急退出")
        emergency_button.setObjectName("emergencyExitButton")
        emergency_button.clicked.connect(self._emergency_exit)

        header.addWidget(title)
        header.addWidget(self.state_label)
        header.addStretch(1)
        header.addWidget(emergency_button)

        display_frame = QFrame()
        display_frame.setStyleSheet(
            "QFrame { background: white; border: 2px solid #d7e2ec; border-radius: 18px; }"
        )
        display_layout = QVBoxLayout(display_frame)

        self.placeholder_label = QLabel()
        self.placeholder_label.setObjectName("patientPlaceholder")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setWordWrap(True)
        display_layout.addWidget(self.placeholder_label)

        root.addLayout(header)
        root.addWidget(display_frame, 1)
        self.current_state = LanControlState.idle()
        self.apply_state(self.current_state)

    def apply_state(self, state: LanControlState) -> None:
        """Render one shared patient-display state."""
        self.current_state = state
        label = _MODE_LABELS[state.mode]

        if state.countdown_seconds is not None:
            label += f" · {state.countdown_seconds} 秒"

        self.state_label.setText(label)
        self.state_label.setStyleSheet(
            "font-family: 'Microsoft YaHei UI'; font-size: 20px; font-weight: 800; "
            f"color: {_MODE_COLORS[state.mode]};"
        )
        self.set_placeholder(state.text)

    def set_placeholder(
        self,
        text: str,
    ) -> None:
        normalized = text.strip()
        font = self.placeholder_label.font()
        font.setPixelSize(patient_message_font_size(normalized))
        self.placeholder_label.setFont(font)
        self.placeholder_label.setText(normalized)

    def _emergency_exit(self, checked: bool = False) -> None:
        del checked
        self.exit_requested.emit()
        self.close()
