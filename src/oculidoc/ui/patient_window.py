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
                color: #294861;
                font-family: "Microsoft YaHei UI";
                font-size: 38px;
                font-weight: 600;
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

        emergency_button = QPushButton("紧急退出")
        emergency_button.setObjectName("emergencyExitButton")
        emergency_button.clicked.connect(self._emergency_exit)

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(emergency_button)

        display_frame = QFrame()
        display_frame.setStyleSheet(
            "QFrame { background: white; border: 2px solid #d7e2ec; border-radius: 18px; }"
        )
        display_layout = QVBoxLayout(display_frame)

        self.placeholder_label = QLabel("等待管理员选择测试项目")
        self.placeholder_label.setObjectName("patientPlaceholder")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setWordWrap(True)
        display_layout.addWidget(self.placeholder_label)

        root.addLayout(header)
        root.addWidget(display_frame, 1)

    def set_placeholder(self, text: str) -> None:
        self.placeholder_label.setText(text)

    def _emergency_exit(self, checked: bool = False) -> None:
        del checked
        self.exit_requested.emit()
        self.close()
