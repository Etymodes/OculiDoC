"""Standalone PySide6 camera preview workbench."""

from pathlib import Path

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QCloseEvent,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.devices.diagnostics import (
    probe_cameras,
)
from oculidoc.vision.camera_preview import (
    CameraPreviewController,
    bgr_frame_to_qimage,
)

_BACKENDS: dict[str, int | None] = {
    "DirectShow (DSHOW)": cv2.CAP_DSHOW,
    "Media Foundation (MSMF)": cv2.CAP_MSMF,
    "Automatic": None,
}


class CameraPreviewWindow(QMainWindow):
    """Standalone camera source and live preview workbench."""

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("OculiDoC Camera Preview Workbench")
        self.resize(1100, 760)

        self._controller = CameraPreviewController()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._read_frame)

        self._build_interface()
        self._refresh_camera_list()

    def _build_interface(self) -> None:
        central_widget = QWidget()
        root_layout = QVBoxLayout(central_widget)

        controls_frame = QFrame()
        controls_layout = QHBoxLayout(controls_frame)

        camera_form = QFormLayout()

        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(220)
        camera_form.addRow(
            "Camera source:",
            self.camera_combo,
        )

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(_BACKENDS.keys())
        camera_form.addRow(
            "OpenCV backend:",
            self.backend_combo,
        )

        self.max_index_spin = QSpinBox()
        self.max_index_spin.setRange(0, 20)
        self.max_index_spin.setValue(6)
        camera_form.addRow(
            "Highest index:",
            self.max_index_spin,
        )

        controls_layout.addLayout(camera_form)

        button_layout = QVBoxLayout()

        self.refresh_button = QPushButton("Refresh cameras")
        self.refresh_button.clicked.connect(self._refresh_camera_list)
        button_layout.addWidget(self.refresh_button)

        self.start_button = QPushButton("Start preview")
        self.start_button.clicked.connect(self._start_preview)
        button_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop preview")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_preview)
        button_layout.addWidget(self.stop_button)

        self.snapshot_button = QPushButton("Save snapshot")
        self.snapshot_button.setEnabled(False)
        self.snapshot_button.clicked.connect(self._save_snapshot)
        button_layout.addWidget(self.snapshot_button)

        controls_layout.addLayout(button_layout)
        controls_layout.addStretch(1)

        status_layout = QFormLayout()

        self.connection_status = QLabel("Disconnected")
        status_layout.addRow(
            "Status:",
            self.connection_status,
        )

        self.backend_status = QLabel("—")
        status_layout.addRow(
            "Active backend:",
            self.backend_status,
        )

        self.mode_status = QLabel("—")
        status_layout.addRow(
            "Reported mode:",
            self.mode_status,
        )

        self.frame_status = QLabel("—")
        status_layout.addRow(
            "Latest frame:",
            self.frame_status,
        )

        controls_layout.addLayout(status_layout)

        root_layout.addWidget(controls_frame)

        self.preview_label = QLabel("Camera preview is stopped.")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(
            640,
            480,
        )
        self.preview_label.setStyleSheet(
            """
            QLabel {
                background: #15181d;
                color: #d6d9df;
                border: 1px solid #3a4049;
                font-size: 16px;
            }
            """
        )

        root_layout.addWidget(
            self.preview_label,
            stretch=1,
        )

        self.setCentralWidget(central_widget)

        self.statusBar().showMessage("Ready")

    def _selected_backend(
        self,
    ) -> int | None:
        return _BACKENDS[self.backend_combo.currentText()]

    def _selected_camera_index(self) -> int:
        value = self.camera_combo.currentData()

        if value is None:
            return 0

        return int(value)

    def _set_controls_running(
        self,
        running: bool,
    ) -> None:
        self.camera_combo.setEnabled(not running)
        self.backend_combo.setEnabled(not running)
        self.max_index_spin.setEnabled(not running)
        self.refresh_button.setEnabled(not running)
        self.start_button.setEnabled(not running and self.camera_combo.count() > 0)
        self.stop_button.setEnabled(running)

        if not running:
            self.snapshot_button.setEnabled(False)

    def _refresh_camera_list(self) -> None:
        if self._controller.running:
            return

        self.statusBar().showMessage("Scanning camera indices...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            backend = self._selected_backend()
            results = probe_cameras(
                self.max_index_spin.value(),
                backend=backend,
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Camera scan failed",
                str(error),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        current_index = self._selected_camera_index()
        self.camera_combo.clear()

        available_count = 0

        for result in results:
            if result.available:
                available_count += 1
                mode_text = f"{result.width_px or '?'}x{result.height_px or '?'}"
                label = f"Camera {result.index} — available — {mode_text}"
            else:
                label = f"Camera {result.index} — {result.status.value}"

            self.camera_combo.addItem(
                label,
                result.index,
            )

        restored_position = self.camera_combo.findData(current_index)

        if restored_position >= 0:
            self.camera_combo.setCurrentIndex(restored_position)

        self.start_button.setEnabled(self.camera_combo.count() > 0)
        self.statusBar().showMessage(f"Scan complete: {available_count} available camera(s)")

    def _start_preview(self) -> None:
        try:
            self._controller.start(
                index=self._selected_camera_index(),
                backend=self._selected_backend(),
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Could not start camera",
                str(error),
            )
            return

        width, height, fps = self._controller.reported_mode
        mode_text = f"{width or '?'}x{height or '?'}; {fps if fps is not None else '?'} FPS"

        self.connection_status.setText("Streaming")
        self.backend_status.setText(self._controller.backend_name or "Unknown")
        self.mode_status.setText(mode_text)
        self.frame_status.setText("Waiting")
        self.preview_label.setText("Waiting for camera frame...")

        self._set_controls_running(True)
        self._timer.start()
        self.statusBar().showMessage("Camera preview started")

    def _read_frame(self) -> None:
        try:
            packet, rendered_frame = self._controller.read_next_frame()
        except Exception as error:
            self._timer.stop()
            self._controller.stop()
            self._set_controls_running(False)
            self.connection_status.setText("Read error")

            QMessageBox.critical(
                self,
                "Camera read failed",
                str(error),
            )
            return

        qimage = bgr_frame_to_qimage(rendered_frame)
        pixmap = QPixmap.fromImage(qimage)
        scaled_pixmap = pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        self.preview_label.setPixmap(scaled_pixmap)
        self.frame_status.setText(f"{packet.frame_index}; {packet.width_px}x{packet.height_px}")
        self.snapshot_button.setEnabled(True)

    def _stop_preview(self) -> None:
        self._timer.stop()
        self._controller.stop()

        self.preview_label.clear()
        self.preview_label.setText("Camera preview is stopped.")
        self.connection_status.setText("Disconnected")
        self.backend_status.setText("—")
        self.mode_status.setText("—")
        self.frame_status.setText("—")
        self._set_controls_running(False)

        self.statusBar().showMessage("Camera preview stopped")

    def _save_snapshot(self) -> None:
        default_path = str(Path.home() / "Desktop" / "OculiDoC_Preview_Snapshot.png")
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save camera snapshot",
            default_path,
            "PNG image (*.png);;JPEG image (*.jpg *.jpeg)",
        )

        if not selected_path:
            return

        try:
            saved_path = self._controller.save_snapshot(selected_path)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Could not save snapshot",
                str(error),
            )
            return

        self.statusBar().showMessage(f"Snapshot saved: {saved_path}")

    def closeEvent(
        self,
        event: QCloseEvent,
    ) -> None:
        """Release hardware before closing the window."""
        self._timer.stop()
        self._controller.stop()
        event.accept()
