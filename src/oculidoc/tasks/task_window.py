"""Reusable full-screen shell for clinical tasks."""

from PySide6.QtCore import (
    QElapsedTimer,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TimedTaskWindow(QMainWindow):
    """Display one task with emergency exit and a time limit."""

    finished = Signal(str)

    def __init__(
        self,
        task_widget: QWidget,
        *,
        duration_seconds: int,
        title: str,
    ) -> None:
        super().__init__()

        if not 5 <= duration_seconds <= 3_600:
            raise ValueError("duration_seconds must be between 5 and 3600.")

        self.task_widget = task_widget
        self.duration_seconds = duration_seconds
        self._finished = False

        self.setWindowTitle(title)
        self.setMinimumSize(960, 640)
        self.setStyleSheet(
            """
            QMainWindow {
                background: #071521;
            }

            QWidget#taskHeader {
                background: #10283d;
            }

            QLabel#taskTitle {
                color: white;
                font-family: "Microsoft YaHei UI";
                font-size: 24px;
                font-weight: 700;
            }

            QLabel#taskCountdown {
                color: #ffe66d;
                font-family: "Microsoft YaHei UI";
                font-size: 25px;
                font-weight: 800;
            }

            QPushButton#taskEmergencyExitButton {
                min-width: 120px;
                min-height: 50px;
                background: #c62828;
                color: white;
                border: 3px solid white;
                border-radius: 12px;
                font-family: "Microsoft YaHei UI";
                font-size: 21px;
                font-weight: 800;
                padding: 4px 14px;
            }

            QPushButton#taskEmergencyExitButton:hover {
                background: #ef3b35;
            }
            """
        )

        header = QWidget()
        header.setObjectName("taskHeader")

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(
            16,
            9,
            12,
            9,
        )
        header_layout.setSpacing(14)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("taskTitle")

        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("taskCountdown")

        self.exit_button = QPushButton("✕ 退出")
        self.exit_button.setObjectName("taskEmergencyExitButton")
        self.exit_button.clicked.connect(self.request_exit)

        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.countdown_label)
        header_layout.addWidget(self.exit_button)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(task_widget, 1)

        self.setCentralWidget(central)

        self._elapsed = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._update_countdown)

        self._update_countdown()

    @property
    def remaining_seconds(self) -> int:
        if not self._elapsed.isValid():
            return self.duration_seconds

        elapsed_seconds = self._elapsed.elapsed() / 1_000.0

        return max(
            0,
            int(self.duration_seconds - elapsed_seconds + 0.999),
        )

    def start(self) -> None:
        start_method = getattr(
            self.task_widget,
            "start",
            None,
        )

        if callable(start_method):
            start_method()

        self._elapsed.start()
        self._timer.start()
        self._update_countdown()

    def request_exit(
        self,
        checked: bool = False,
    ) -> None:
        del checked
        self.finish("manual_exit")

    def _update_countdown(self) -> None:
        remaining = self.remaining_seconds
        minutes, seconds = divmod(
            remaining,
            60,
        )

        self.countdown_label.setText(f"剩余 {minutes:02d}:{seconds:02d}")

        if self._elapsed.isValid() and remaining <= 0:
            self.finish("timeout")

    def _stop_task(self) -> None:
        stop_method = getattr(
            self.task_widget,
            "stop",
            None,
        )

        if callable(stop_method):
            stop_method()

    def finish(
        self,
        reason: str,
    ) -> None:
        if self._finished:
            return

        self._finished = True
        self._timer.stop()
        self._stop_task()
        self.finished.emit(reason)
        self.close()

    def closeEvent(
        self,
        event: QCloseEvent,
    ) -> None:
        self._timer.stop()
        self._stop_task()

        if not self._finished:
            self._finished = True
            self.finished.emit("window_closed")

        event.accept()
