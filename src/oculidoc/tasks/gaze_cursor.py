"""Transparent gaze-cursor overlay for tasks built from child widgets."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from oculidoc.devices.contracts import EyeTrackerSample


class GazeCursorOverlay(QWidget):
    """Draw a non-interactive cursor above button- and label-based tasks."""

    _size_px = 54
    _center_px = _size_px / 2.0

    def __init__(self, parent: QWidget, *, enabled: bool) -> None:
        super().__init__(parent)
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a boolean.")

        self.enabled = enabled
        self._normalized_position: tuple[float, float] | None = None
        self.setObjectName("gazeCursorOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(self._size_px, self._size_px)
        self.hide()

    @property
    def normalized_position(self) -> tuple[float, float] | None:
        return self._normalized_position

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        if (
            not self.enabled
            or not sample.gaze_valid
            or sample.gaze_x_normalized is None
            or sample.gaze_y_normalized is None
        ):
            self.clear()
            return

        self.set_normalized_position(
            sample.gaze_x_normalized,
            sample.gaze_y_normalized,
        )

    def set_normalized_position(self, x: float, y: float) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.clear()
            return

        normalized_x = max(0.0, min(1.0, float(x)))
        normalized_y = max(0.0, min(1.0, float(y)))
        left = round(normalized_x * max(0, parent.width() - 1) - self._center_px)
        top = round(normalized_y * max(0, parent.height() - 1) - self._center_px)
        left = max(0, min(max(0, parent.width() - self.width()), left))
        top = max(0, min(max(0, parent.height() - self.height()), top))

        self._normalized_position = (normalized_x, normalized_y)
        self.move(left, top)
        self.show()
        self.raise_()
        self.update()

    def clear(self) -> None:
        self._normalized_position = None
        self.hide()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor(255, 255, 255, 45))
        painter.setPen(QPen(QColor("#40e0ff"), 4))
        center = QPointF(self._center_px, self._center_px)
        painter.drawEllipse(center, 18, 18)
        painter.drawLine(QPointF(2, self._center_px), QPointF(52, self._center_px))
        painter.drawLine(QPointF(self._center_px, 2), QPointF(self._center_px, 52))
        painter.end()
