"""Screen geometry setup and participant-driven ThinksTracker nine-point calibration."""

from __future__ import annotations

import base64
import zlib
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QKeyEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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

from oculidoc.config import Settings
from oculidoc.devices.thinks_tracker import (
    ThinksConfig,
    ThinksProcess,
    discover_thinks_sdk_dll,
    read_thinks_config,
)

POINTS = tuple((x, y) for y in (0.1, 0.5, 0.9) for x in (0.1, 0.5, 0.9))


def preview_panel_rect(width: int, height: int, target: tuple[float, float]) -> QRect:
    """Place the eye preview in the corner farthest from the current target."""
    margin = 20
    top = 64  # Keep the instruction line clear.
    panel_width = min(320, max(140, round(width * 0.22)), max(1, width - 2 * margin))
    panel_height = min(round(panel_width * 0.75) + 28, max(1, height - top - margin))
    right = max(margin, width - margin - panel_width)
    bottom = max(top, height - margin - panel_height)
    candidates = (
        QRect(right, top, panel_width, panel_height),
        QRect(right, bottom, panel_width, panel_height),
        QRect(margin, top, panel_width, panel_height),
        QRect(margin, bottom, panel_width, panel_height),
    )
    point = QPoint(round(target[0] * width), round(target[1] * height))

    def clearance(rect: QRect) -> int:
        dx = max(rect.left() - point.x(), 0, point.x() - rect.right())
        dy = max(rect.top() - point.y(), 0, point.y() - rect.bottom())
        return dx * dx + dy * dy

    return max(candidates, key=clearance)


def _preview_box(value: object, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    if any(not isinstance(item, int) for item in value):
        return None
    x, y, box_width, box_height = value
    if box_width <= 0 or box_height <= 0:
        return None
    left, top = max(0, x), max(0, y)
    right, bottom = min(width, x + box_width), min(height, y + box_height)
    if right <= left or bottom <= top:
        return None
    return (left, top, right - left, bottom - top)


class ThinksSetupDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("ThinksTracker 屏幕与 SDK 设置")
        self.resize(680, 440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.dll = QLineEdit()
        browse = QPushButton("选择 DLL…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.dll)
        row.addWidget(browse)
        form.addRow("厂商 SDK：", row)
        self.license = QLineEdit()
        self.license.setPlaceholderText("可留空，仍由厂商 SDK 检查设备内授权")
        form.addRow("授权文件：", self.license)
        self.resolution = QComboBox()
        self.resolution.addItems(["1920x1080x120", "1920x608x300", "1920x1080x60"])
        form.addRow("采集模式：", self.resolution)
        screen = next(
            (s for s in QApplication.screens() if s.name() == settings.patient_screen_name),
            QApplication.primaryScreen(),
        )
        width = round(screen.physicalSize().width()) if screen else 354
        height = round(screen.physicalSize().height()) if screen else 200
        # Only width/height can come from EDID. Installation dimensions require confirmation.
        self.geometry_controls: list[QSpinBox] = []
        for label, value, low, high in (
            ("屏幕与设备夹角（度）：", 20, 0, 90),
            ("设备中心到屏幕平面（mm）：", 25, 0, 500),
            ("屏幕有效宽度（mm）：", width, 50, 3000),
            ("屏幕有效高度（mm）：", height, 50, 3000),
            ("底部高度（mm，按 SDK 定义）：", 60, 0, 500),
        ):
            control = QSpinBox()
            control.setRange(low, high)
            control.setValue(value)
            self.geometry_controls.append(control)
            form.addRow(label, control)
        self._python: Path | None = None
        try:
            # Parse the saved settings independently of the DLL's current path.
            # Moving a private SDK must not discard the participant's geometry,
            # acquisition mode, license path or helper choice.
            config = ThinksConfig.model_validate_json(
                settings.thinks_tracker_config_path.read_text(encoding="utf-8-sig")
            )
            dll = config.dll_path
            if not dll.is_file():
                dll = discover_thinks_sdk_dll() or dll
            self.dll.setText(str(dll))
            self.license.setText(str(config.license_file or ""))
            self.resolution.setCurrentText(config.resolution)
            for control, value in zip(self.geometry_controls, config.screen_physical, strict=True):
                control.setValue(value)
            self._python = config.python_executable
        except Exception:
            discovered = discover_thinks_sdk_dll()
            if discovered is not None:
                self.dll.setText(str(discovered))
        layout.addLayout(form)
        note = QLabel(
            "宽高初值来自屏幕信息，安装角度和距离初值仅为 SDK 示例。请按实际安装核对。"
            "底部高度为设备底部到设备中心在屏幕上垂直投影点的距离。"
            "更换屏幕、安装位置或受试者后请重新校准。保存表示确认以上参数。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ThinksTracker.dll", self.dll.text(), "ThinksTracker (ThinksTracker.dll)"
        )
        if path:
            self.dll.setText(path)

    def _save(self) -> None:
        try:
            values = tuple(control.value() for control in self.geometry_controls)
            config = ThinksConfig.model_validate(
                {
                    "dll_path": self.dll.text().strip(),
                    "license_file": self.license.text().strip() or None,
                    "resolution": self.resolution.currentText(),
                    "screen_physical": values,
                    "geometry_confirmed": True,
                    "python_executable": self._python,
                }
            )
            if not config.dll_path.is_file():
                raise ValueError("请选择有效的 ThinksTracker.dll。")
            path = self.settings.thinks_tracker_write_path
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".pending.json")
            temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(path)
            self.accept()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法保存设置", str(error))


class CalibrationWorker(QThread):
    packet = Signal(object)
    failed = Signal(str)

    def __init__(self, process: ThinksProcess, parent: QWidget) -> None:
        super().__init__(parent)
        self.process = process

    def run(self) -> None:
        try:
            self.packet.emit(self.process.start("calibrate"))
            while not self.isInterruptionRequested():
                try:
                    self.packet.emit(self.process.receive())
                except TimeoutError:
                    continue
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
        finally:
            self.process.close()


class ThinksCalibrationDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._worker: CalibrationWorker | None = None
        self._index = 0
        self._ready = False
        self._busy = False
        self._point_in_flight = False
        self._point_confirmed = False
        self._closed = False
        self.saved_path: Path | None = None
        self._message = "正在连接 ThinksTracker；Esc 取消。"
        self._preview_image: QImage | None = None
        self._preview_boxes: dict[str, tuple[int, int, int, int] | None] = {}
        self._preview_detection_state = "off"
        self._preview_last_at = 0.0
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(500)
        self._preview_timer.timeout.connect(self._expire_preview)
        self._collect_timer = QTimer(self)
        self._collect_timer.setSingleShot(True)
        self._collect_timer.timeout.connect(self._collect_point)
        self._advance_timer = QTimer(self)
        self._advance_timer.setSingleShot(True)
        self._advance_timer.timeout.connect(self._advance_point)
        self.setWindowTitle("ThinksTracker 九点校准")

    def start(self) -> None:
        screen = next(
            (s for s in QApplication.screens() if s.name() == self.settings.patient_screen_name),
            None,
        )
        if not self.settings.patient_id or screen is None:
            QMessageBox.warning(self, "无法校准", "请先选择受试者和已连接的患者显示屏。")
            return
        try:
            config = read_thinks_config(self.settings.thinks_tracker_config_path)
        except Exception as error:
            QMessageBox.warning(self, "无法校准", str(error))
            return
        self._screen = screen
        screen_name = screen.name()
        self._screen_removed = lambda removed: (
            self.reject() if removed.name() == screen_name else None
        )
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        app.screenRemoved.connect(self._screen_removed)
        process = ThinksProcess(
            config,
            self.settings.data_dir / "runtime/thinks_tracker",
            {
                "patient_id": self.settings.patient_id,
                "screen": [screen_name, screen.geometry().width(), screen.geometry().height()],
            },
        )
        self._worker = CalibrationWorker(process, self)
        self._worker.packet.connect(self._packet)
        self._worker.failed.connect(self._fail)
        self.setScreen(screen)
        self.move(screen.geometry().topLeft())
        self.showFullScreen()
        self._preview_timer.start()
        self._worker.start()
        self.exec()
        if self.result() == QDialog.DialogCode.Accepted and self.saved_path is not None:
            self._show_saved_result()

    def _show_saved_result(self) -> None:
        assert self.saved_path is not None
        message = QMessageBox(self.parentWidget())
        message.setWindowTitle("ThinksTracker 校准完成")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText("九点校准已完成，结果文件已保存。")
        message.setInformativeText(
            f"保存位置：\n{self.saved_path}\n\n"
            "同一受试者、设备、屏幕及安装参数下，下次启动会自动加载此校准。\n"
            "请继续运行自检并保存设备设置，以验证实际注视表现。"
        )
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_folder = message.addButton("打开结果文件夹", QMessageBox.ButtonRole.ActionRole)
        message.addButton(QMessageBox.StandardButton.Ok)
        message.exec()
        if message.clickedButton() == open_folder:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.saved_path.parent))):
                QMessageBox.warning(
                    self.parentWidget(), "无法打开文件夹", str(self.saved_path.parent)
                )

    def _packet(self, packet: dict) -> None:
        if self._closed:
            return
        event = packet.get("event")
        if event == "ready":
            self._ready = True
            self._message = (
                "看蓝点，按空格开始；绿圈表示双眼检测可用，采点成功后自动换点；Esc 取消。"
            )
        elif event == "preview":
            self._show_preview(packet)
        elif event == "eye_detection_paused":
            self._preview_detection_state = "paused"
            self._preview_boxes.clear()
        elif event == "eye_detection_resumed":
            self._preview_detection_state = "running"
        elif event == "eye_detection_unavailable":
            self._preview_detection_state = "off"
            self._preview_boxes.clear()
        elif event == "point_done":
            if not self._point_in_flight or packet.get("count") != self._index + 1:
                return
            self._point_in_flight = False
            self._point_confirmed = True
            self._message = f"当前点已采集 · {self._index + 1} / 9 · Esc 取消"
            self._advance_timer.start(350)
        elif event == "point_retry":
            if not self._point_in_flight or packet.get("count") != self._index + 1:
                return
            self._point_in_flight = False
            self._preview_boxes.clear()
            self._message = f"当前点等待有效眼部信号，将自动重试 · {self._index + 1} / 9 · Esc 取消"
            self._collect_timer.start(1200)
        elif event == "saved":
            value = packet.get("calibration_path")
            try:
                path = Path(value).resolve() if isinstance(value, str) and value else None
                directory = (
                    self.settings.data_dir / "runtime/thinks_tracker/calibration"
                ).resolve()
                if (
                    path is None
                    or path.parent != directory
                    or path.suffix != ".dat"
                    or not path.is_file()
                    or path.stat().st_size == 0
                ):
                    raise ValueError("Missing calibration result")
            except (OSError, ValueError):
                self._fail("未找到完整的校准结果文件，请重新校准。")
                return
            self.saved_path = path
            self.accept()
        self.update()

    def _show_preview(self, packet: dict) -> None:
        """Decode one bounded, transient gray frame from the SDK helper."""
        width, height = packet.get("width"), packet.get("height")
        encoded = packet.get("image_base64")
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or not 0 < width <= 320
            or not 0 < height <= 180
            or not isinstance(encoded, str)
            or len(encoded) > 200_000
        ):
            return
        try:
            compressed = base64.b64decode(encoded, validate=True)
            decoder = zlib.decompressobj()
            pixels = decoder.decompress(compressed, width * height + 1)
            if len(pixels) != width * height or not decoder.eof:
                return
            image = QImage(pixels, width, height, width, QImage.Format.Format_Grayscale8).copy()
        except (ValueError, zlib.error):
            return
        if image.isNull():
            return
        self._preview_image = image
        self._preview_last_at = monotonic()
        state = packet.get("eye_detection")
        if state in {"running", "paused", "off"}:
            self._preview_detection_state = state
        self._preview_boxes = (
            {
                name: _preview_box(packet.get(f"{name}_bbox"), width, height)
                for name in ("left", "right")
            }
            if self._preview_detection_state == "running"
            else {}
        )

    def _expire_preview(self) -> None:
        if self._preview_image is not None and monotonic() - self._preview_last_at > 1.5:
            self._preview_image = None
            self._preview_boxes.clear()
        self.update()

    def _eyes_detected(self) -> bool:
        # Before calibration the SDK exposes eye boxes, not validated screen gaze.
        return (
            self._preview_image is not None
            and self._preview_detection_state == "running"
            and monotonic() - self._preview_last_at <= 0.5
            and all(self._preview_boxes.get(name) is not None for name in ("left", "right"))
        )

    def _send(self, packet: dict) -> None:
        if self._closed or self._worker is None:
            return
        try:
            self._worker.process.send(packet)
        except Exception as error:
            self._fail(str(error))

    def _show_point(self) -> None:
        self._point_confirmed = False
        self._message = f"请看蓝点并保持自然坐姿 · {self._index + 1} / 9 · Esc 取消"
        self.update()
        # Give the participant time to saccade and settle before SDK collection begins.
        self._collect_timer.start(900)

    def _advance_point(self) -> None:
        if self._closed or not self._point_confirmed:
            return
        self._point_confirmed = False
        self._index += 1
        if self._index == len(POINTS):
            self._message = "正在检查并保存校准…"
            self._send({"command": "save"})
            self.update()
        else:
            self._show_point()

    def _collect_point(self) -> None:
        if self._closed or self._index >= len(POINTS) or self._point_in_flight:
            return
        self._point_in_flight = True
        self._message = f"正在采集当前点，请保持注视 · {self._index + 1} / 9 · Esc 取消"
        self._send({"command": "point", "point": POINTS[self._index]})
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() == Qt.Key.Key_Space and self._ready and not self._busy:
            self._busy = True
            self._show_point()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f4f7fa"))
        painter.setPen(QColor("#183246"))
        painter.drawText(
            self.rect().adjusted(30, 20, -30, -20),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
            self._message,
        )
        x, y = POINTS[min(self._index, len(POINTS) - 1)]
        self._paint_preview(painter, preview_panel_rect(self.width(), self.height(), (x, y)))
        painter.setPen(QColor("#168dcc"))
        painter.setBrush(QColor("#168dcc"))
        painter.drawEllipse(round(x * self.width()) - 12, round(y * self.height()) - 12, 24, 24)
        if self._point_confirmed or self._eyes_detected():
            painter.setPen(QPen(QColor("#27ae60"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(round(x * self.width()) - 18, round(y * self.height()) - 18, 36, 36)

    def _paint_preview(self, painter: QPainter, panel: QRect) -> None:
        painter.setPen(QPen(QColor("#183246"), 2))
        painter.setBrush(QColor("#183246"))
        painter.drawRect(panel)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            panel.adjusted(8, 2, -8, -panel.height() + 27),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._preview_status_text(),
        )
        viewport = QRectF(panel.adjusted(2, 28, -2, -2))
        image = self._preview_image
        if image is None:
            painter.drawText(viewport, Qt.AlignmentFlag.AlignCenter, "等待眼部画面…")
            return
        scale = min(viewport.width() / image.width(), viewport.height() / image.height())
        image_rect = QRectF(
            viewport.x() + (viewport.width() - image.width() * scale) / 2,
            viewport.y() + (viewport.height() - image.height() * scale) / 2,
            image.width() * scale,
            image.height() * scale,
        )
        painter.drawImage(image_rect, image)
        for name, color in (("left", "#67e391"), ("right", "#ffd166")):
            box = self._preview_boxes.get(name)
            if box is None:
                continue
            x, y, width, height = box
            painter.setPen(QPen(QColor(color), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(
                QRectF(
                    image_rect.x() + x * scale,
                    image_rect.y() + y * scale,
                    width * scale,
                    height * scale,
                )
            )

    def _preview_status_text(self) -> str:
        if self._preview_detection_state == "paused":
            return "眼部实时画面 · 采点中"
        if self._preview_detection_state == "off":
            return "眼部实时画面 · 检测不可用"
        left = self._preview_boxes.get("left") is not None
        right = self._preview_boxes.get("right") is not None
        if left and right:
            return "眼部实时画面 · 双眼已检测"
        if left or right:
            return "眼部实时画面 · 仅检测到一只眼"
        return "眼部实时画面 · 未检测到眼睛"

    def _fail(self, message: str) -> None:
        if self._closed:
            return
        self.reject()
        QMessageBox.warning(self.parentWidget(), "ThinksTracker 校准未完成", message)

    def done(self, result: int) -> None:
        self._closed = True
        self._preview_timer.stop()
        self._collect_timer.stop()
        self._advance_timer.stop()
        if self._worker:
            self._worker.requestInterruption()
            self._worker.process.close()
            self._worker.wait(6000)
            app = QApplication.instance()
            if isinstance(app, QApplication):
                app.screenRemoved.disconnect(self._screen_removed)
        super().done(result)
