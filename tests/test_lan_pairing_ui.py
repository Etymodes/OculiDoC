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


def test_pairing_dialog_refreshes_address_and_qr(
    qtbot: QtBot,
) -> None:
    original = "http://192.168.1.20:8000/control?token=test-token"
    updated = "http://192.168.1.21:8000/control?token=test-token"
    dialog = LanPairingDialog(original)
    qtbot.addWidget(dialog)

    original_cache_key = dialog.qr_label.pixmap().cacheKey()
    dialog.update_control_url(updated)

    assert dialog.control_url == updated
    assert dialog.url_edit.text() == updated
    assert dialog.qr_label.pixmap().cacheKey() != original_cache_key
    assert "已刷新" in dialog.hint_label.text()


def test_pairing_dialog_close_emits_request(
    qtbot: QtBot,
) -> None:
    dialog = LanPairingDialog("http://192.168.1.20:8000/control?token=test-token")
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(
        dialog.close_requested,
        timeout=1_000,
    ):
        dialog.reject()

    assert not dialog.isVisible()
