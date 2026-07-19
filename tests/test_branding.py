from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
)
from pytest import raises

from oculidoc.app import (
    create_qt_application,
)
from oculidoc.branding import (
    APPLICATION_NAME,
    ORGANIZATION_NAME,
    application_icon,
    apply_application_branding,
    brand_asset_path,
    brand_mark_pixmap,
    brand_wordmark_pixmap,
)


def test_brand_assets_are_present() -> None:
    for name in (
        "app_icon.png",
        "app_icon.ico",
        "brand_mark_blue.png",
        "brand_mark_white.png",
        "brand_wordmark_blue.png",
    ):
        path = brand_asset_path(name)

        assert path.is_file()
        assert path.stat().st_size > 0


def test_unknown_brand_asset_is_rejected() -> None:
    with raises(
        ValueError,
        match="Unknown branding asset",
    ):
        brand_asset_path("../outside.png")


def test_brand_pixmaps_load(
    qapp: QApplication,
) -> None:
    del qapp

    blue = brand_mark_pixmap(
        variant="blue",
        max_width=160,
        max_height=100,
    )
    white = brand_mark_pixmap(
        variant="white",
        max_width=160,
        max_height=100,
    )
    wordmark = brand_wordmark_pixmap(
        max_width=480,
        max_height=440,
    )

    assert not blue.isNull()
    assert not white.isNull()
    assert not wordmark.isNull()
    assert blue.width() <= 160
    assert blue.height() <= 100


def test_application_branding_sets_names_and_icon(
    qapp: QApplication,
) -> None:
    apply_application_branding(qapp)

    assert qapp.applicationName() == APPLICATION_NAME
    assert qapp.applicationDisplayName() == APPLICATION_NAME
    assert qapp.organizationName() == ORGANIZATION_NAME
    assert not qapp.windowIcon().isNull()
    assert not application_icon().isNull()


def test_create_qt_application_reapplies_branding(
    qapp: QApplication,
) -> None:
    qapp.setApplicationName("Temporary")
    qapp.setWindowIcon(type(qapp.windowIcon())())

    active = create_qt_application([])

    assert active is qapp
    assert active.applicationName() == APPLICATION_NAME
    assert not active.windowIcon().isNull()
