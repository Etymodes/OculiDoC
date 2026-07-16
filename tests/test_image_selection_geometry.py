"""Qt preview selection geometry tests."""

from PySide6.QtCore import (
    QRectF,
    QSize,
)

from oculidoc.vision import (
    EyeBoundingBox,
    fitted_image_rect,
    map_display_selection_to_image,
)


def test_fitted_rect_letterboxes_widescreen_image() -> None:
    rect = fitted_image_rect(
        QSize(800, 600),
        QSize(1280, 720),
    )

    assert rect == QRectF(
        0.0,
        75.0,
        800.0,
        450.0,
    )


def test_selection_maps_to_image_pixels() -> None:
    display_rect = QRectF(
        0.0,
        75.0,
        800.0,
        450.0,
    )
    selection = QRectF(
        100.0,
        100.0,
        200.0,
        100.0,
    )

    box = map_display_selection_to_image(
        selection,
        display_rect,
        image_width_px=1280,
        image_height_px=720,
    )

    assert box == EyeBoundingBox(
        x_px=160,
        y_px=40,
        width_px=320,
        height_px=160,
    )


def test_selection_is_clipped_to_displayed_image() -> None:
    display_rect = QRectF(
        100.0,
        50.0,
        400.0,
        300.0,
    )
    selection = QRectF(
        50.0,
        0.0,
        200.0,
        150.0,
    )

    box = map_display_selection_to_image(
        selection,
        display_rect,
        image_width_px=800,
        image_height_px=600,
    )

    assert box == EyeBoundingBox(
        x_px=0,
        y_px=0,
        width_px=300,
        height_px=200,
    )


def test_tiny_selection_returns_none() -> None:
    box = map_display_selection_to_image(
        QRectF(
            20.0,
            20.0,
            1.0,
            1.0,
        ),
        QRectF(
            0.0,
            0.0,
            100.0,
            100.0,
        ),
        image_width_px=640,
        image_height_px=480,
    )

    assert box is None
