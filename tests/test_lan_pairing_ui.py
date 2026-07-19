from __future__ import annotations

from pytestqt.qtbot import QtBot

from oculidoc.ui.lan_pairing import (
    LanPairingDialog,
    qr_pixmap,
)


def test_qr_pixmap_is_rendered(
    qtbot: QtBot,
) -> None:
    del qtbot
    pixmap = qr_pixmap(
        "http://192.168.1.20:8000/control?token=test-token",
        size=180,
    )

    assert not pixmap.isNull()
    assert pixmap.width() == 180
    assert pixmap.height() == 180


def test_pairing_dialog_displays_control_url(
    qtbot: QtBot,
) -> None:
    url = "http://192.168.1.20:8000/control?token=test-token"
    dialog = LanPairingDialog(url)
    qtbot.addWidget(dialog)

    assert dialog.url_edit.text() == url
    assert not dialog.qr_label.pixmap().isNull()
