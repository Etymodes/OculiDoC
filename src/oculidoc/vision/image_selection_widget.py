"""Qt image widget supporting pixel-space region selection."""

from math import floor

from PySide6.QtCore import (
    QPointF,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QLabel

from oculidoc.vision.eye_observation import (
    EyeBoundingBox,
)


def fitted_image_rect(
    container_size: QSize,
    image_size: QSize,
) -> QRectF:
    """Return the centered KeepAspectRatio image rectangle."""
    if (
        container_size.width() <= 0
        or container_size.height() <= 0
        or image_size.width() <= 0
        or image_size.height() <= 0
    ):
        raise ValueError("Container and image dimensions must be positive.")

    scale = min(
        container_size.width() / image_size.width(),
        container_size.height() / image_size.height(),
    )
    displayed_width = image_size.width() * scale
    displayed_height = image_size.height() * scale

    return QRectF(
        (container_size.width() - displayed_width) / 2.0,
        (container_size.height() - displayed_height) / 2.0,
        displayed_width,
        displayed_height,
    )


def map_display_selection_to_image(
    selection_rect: QRectF,
    display_rect: QRectF,
    *,
    image_width_px: int,
    image_height_px: int,
) -> EyeBoundingBox | None:
    """Map a widget-space selection into image pixels."""
    if image_width_px <= 0 or image_height_px <= 0:
        raise ValueError("Image dimensions must be positive.")

    if display_rect.width() <= 0 or display_rect.height() <= 0:
        raise ValueError("display_rect must have positive dimensions.")

    clipped = selection_rect.normalized().intersected(display_rect)

    if clipped.width() < 2 or clipped.height() < 2:
        return None

    scale_x = image_width_px / display_rect.width()
    scale_y = image_height_px / display_rect.height()

    left = floor((clipped.left() - display_rect.left()) * scale_x)
    top = floor((clipped.top() - display_rect.top()) * scale_y)
    right = round((clipped.right() - display_rect.left()) * scale_x)
    bottom = round((clipped.bottom() - display_rect.top()) * scale_y)

    left = min(
        max(left, 0),
        image_width_px,
    )
    top = min(
        max(top, 0),
        image_height_px,
    )
    right = min(
        max(right, 0),
        image_width_px,
    )
    bottom = min(
        max(bottom, 0),
        image_height_px,
    )

    if right <= left or bottom <= top:
        return None

    return EyeBoundingBox(
        x_px=left,
        y_px=top,
        width_px=right - left,
        height_px=bottom - top,
    )


class ImageSelectionLabel(QLabel):
    """Image display that emits one dragged image-space box."""

    selection_completed = Signal(object)
    selection_cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMouseTracking(True)

        self._source_pixmap: QPixmap | None = None
        self._image_size: QSize | None = None
        self._selection_enabled = False
        self._drag_start: QPointF | None = None
        self._drag_current: QPointF | None = None

    @property
    def selection_enabled(self) -> bool:
        """Return whether a drag selection is armed."""
        return self._selection_enabled

    def set_frame_pixmap(
        self,
        pixmap: QPixmap,
        *,
        image_width_px: int,
        image_height_px: int,
    ) -> None:
        """Display a frame and retain its original dimensions."""
        if pixmap.isNull():
            raise ValueError("pixmap cannot be null.")

        if image_width_px <= 0 or image_height_px <= 0:
            raise ValueError("Image dimensions must be positive.")

        self._source_pixmap = pixmap
        self._image_size = QSize(
            image_width_px,
            image_height_px,
        )
        self._update_scaled_pixmap()

    def clear_frame(self) -> None:
        """Remove the current image and selection."""
        self.cancel_selection()
        self._source_pixmap = None
        self._image_size = None
        self.clear()

    def begin_selection(self) -> None:
        """Arm one rectangular drag operation."""
        if self._source_pixmap is None or self._image_size is None:
            raise RuntimeError("No image is available for selection.")

        self._selection_enabled = True
        self._drag_start = None
        self._drag_current = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def cancel_selection(self) -> None:
        """Cancel an active drag selection."""
        was_enabled = self._selection_enabled

        self._selection_enabled = False
        self._drag_start = None
        self._drag_current = None
        self.unsetCursor()
        self.update()

        if was_enabled:
            self.selection_cancelled.emit()

    def _display_rect(self) -> QRectF | None:
        if self._image_size is None:
            return None

        return fitted_image_rect(
            self.size(),
            self._image_size,
        )

    def _clamp_to_display(
        self,
        point: QPointF,
    ) -> QPointF:
        display_rect = self._display_rect()

        if display_rect is None:
            return point

        return QPointF(
            min(
                max(point.x(), display_rect.left()),
                display_rect.right(),
            ),
            min(
                max(point.y(), display_rect.top()),
                display_rect.bottom(),
            ),
        )

    def _update_scaled_pixmap(self) -> None:
        if self._source_pixmap is None:
            return

        scaled = self._source_pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)

    def resizeEvent(
        self,
        event: QResizeEvent,
    ) -> None:
        """Rescale the current frame when the widget resizes."""
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def mousePressEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        """Start a selection inside the displayed image."""
        display_rect = self._display_rect()

        if (
            self._selection_enabled
            and event.button() is Qt.MouseButton.LeftButton
            and display_rect is not None
            and display_rect.contains(event.position())
        ):
            point = self._clamp_to_display(event.position())
            self._drag_start = point
            self._drag_current = point
            self.update()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        """Update the current selection rectangle."""
        if self._selection_enabled and self._drag_start is not None:
            self._drag_current = self._clamp_to_display(event.position())
            self.update()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        """Complete and emit one image-space selection."""
        if (
            self._selection_enabled
            and self._drag_start is not None
            and event.button() is Qt.MouseButton.LeftButton
        ):
            self._drag_current = self._clamp_to_display(event.position())
            display_rect = self._display_rect()
            image_size = self._image_size

            selection_rect = QRectF(
                self._drag_start,
                self._drag_current,
            )

            self._selection_enabled = False
            self._drag_start = None
            self._drag_current = None
            self.unsetCursor()
            self.update()

            if display_rect is not None and image_size is not None:
                box = map_display_selection_to_image(
                    selection_rect,
                    display_rect,
                    image_width_px=(image_size.width()),
                    image_height_px=(image_size.height()),
                )

                if box is not None:
                    self.selection_completed.emit(box)
                else:
                    self.selection_cancelled.emit()

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def paintEvent(
        self,
        event: QPaintEvent,
    ) -> None:
        """Draw the active drag rectangle."""
        super().paintEvent(event)

        if self._drag_start is None or self._drag_current is None:
            return

        painter = QPainter(self)
        pen = QPen(
            QColor(0, 220, 255),
            2,
            Qt.PenStyle.DashLine,
        )
        painter.setPen(pen)
        painter.drawRect(
            QRectF(
                self._drag_start,
                self._drag_current,
            ).normalized()
        )
