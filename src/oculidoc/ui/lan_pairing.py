"""Desktop widgets for LAN mobile-control pairing."""

from __future__ import annotations

import io

import segno
from PySide6.QtCore import QByteArray, Qt, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QEnterEvent,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class HoverPairingButton(QPushButton):
    """A status button that opens pairing details on hover or click."""

    hover_entered = Signal()

    def enterEvent(self, event: QEnterEvent) -> None:
        self.hover_entered.emit()
        super().enterEvent(event)


def qr_pixmap(
    value: str,
    *,
    size: int = 230,
) -> QPixmap:
    """Render a scannable QR code to a Qt pixmap."""
    code = segno.make_qr(
        value,
        error="m",
    )
    buffer = io.BytesIO()
    code.save(
        buffer,
        kind="svg",
        scale=6,
        border=2,
    )
    renderer = QSvgRenderer(QByteArray(buffer.getvalue()))
    image = QImage(
        size,
        size,
        QImage.Format.Format_ARGB32,
    )
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)

    try:
        renderer.render(painter)
    finally:
        painter.end()

    return QPixmap.fromImage(image)


class LanPairingDialog(QDialog):
    """Non-modal pairing card shown beside the backend status button."""

    def __init__(
        self,
        control_url: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.control_url = control_url
        self.setWindowTitle("手机后台配对")
        self.setModal(False)
        self.setMinimumWidth(330)
        self.setStyleSheet(
            """
            QDialog { background: white; }
            QLabel { font-family: "Microsoft YaHei UI"; color: #17324d; }
            QLabel#pairingTitle { font-size: 20px; font-weight: 700; }
            QLabel#pairingHint { color: #5a7184; }
            QLineEdit { border: 1px solid #bfd3e4; border-radius: 8px; padding: 8px; }
            QPushButton { min-height: 34px; border-radius: 8px; padding: 4px 12px; }
            """
        )

        title = QLabel("局域网手机控制")
        title.setObjectName("pairingTitle")
        hint = QLabel("手机与本机连接同一局域网后扫描二维码。")
        hint.setObjectName("pairingHint")
        hint.setWordWrap(True)

        self.qr_label = QLabel()
        self.qr_label.setPixmap(qr_pixmap(control_url))

        self.url_edit = QLineEdit(control_url)
        self.url_edit.setReadOnly(True)

        copy_button = QPushButton("复制地址")
        copy_button.clicked.connect(self._copy_url)
        open_button = QPushButton("本机打开")
        open_button.clicked.connect(self._open_url)

        actions = QHBoxLayout()
        actions.addWidget(copy_button)
        actions.addWidget(open_button)

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.rejected.connect(self.hide)

        root = QVBoxLayout(self)
        root.addWidget(title)
        root.addWidget(hint)
        root.addWidget(
            self.qr_label,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        root.addWidget(self.url_edit)
        root.addLayout(actions)
        root.addWidget(close_buttons)
        self.adjustSize()

    def _copy_url(
        self,
        checked: bool = False,
    ) -> None:
        del checked
        QApplication.clipboard().setText(self.control_url)

    def _open_url(
        self,
        checked: bool = False,
    ) -> None:
        del checked
        QDesktopServices.openUrl(QUrl(self.control_url))

    def show_near(
        self,
        anchor: QWidget,
    ) -> None:
        """Show the pairing card above and right-aligned with its anchor."""
        self.adjustSize()
        anchor_top_left = anchor.mapToGlobal(anchor.rect().topLeft())
        x = anchor_top_left.x() + anchor.width() - self.width()
        y = anchor_top_left.y() - self.height() - 8
        self.move(
            max(0, x),
            max(0, y),
        )
        self.show()
        self.raise_()
        self.activateWindow()
