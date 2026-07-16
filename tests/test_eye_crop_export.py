"""Padded eye-crop export tests."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from oculidoc.vision import (
    EyeBoundingBox,
    EyeObservation,
    EyeOpeningState,
    EyeSide,
    crop_eye_image,
    expand_eye_box,
    export_eye_crops,
)


def test_tight_eye_box_expands_to_minimum_size() -> None:
    box = EyeBoundingBox(
        x_px=417,
        y_px=150,
        width_px=14,
        height_px=17,
    )

    expanded = expand_eye_box(
        box,
        image_width_px=640,
        image_height_px=480,
    )

    assert expanded.width_px >= 48
    assert expanded.height_px >= 32
    assert expanded.x_px <= box.x_px
    assert expanded.y_px <= box.y_px
    assert expanded.right_px >= box.right_px
    assert expanded.bottom_px >= box.bottom_px


def test_expansion_stays_inside_image_border() -> None:
    box = EyeBoundingBox(
        x_px=1,
        y_px=2,
        width_px=10,
        height_px=8,
    )

    expanded = expand_eye_box(
        box,
        image_width_px=100,
        image_height_px=80,
        minimum_width_px=48,
        minimum_height_px=32,
    )

    assert expanded.x_px == 0
    assert expanded.y_px == 0
    assert expanded.right_px <= 100
    assert expanded.bottom_px <= 80


def test_crop_eye_image_returns_owned_pixels() -> None:
    image = np.zeros(
        (80, 100, 3),
        dtype=np.uint8,
    )
    image[20:40, 30:60, 1] = 200

    crop = crop_eye_image(
        image,
        EyeBoundingBox(
            x_px=30,
            y_px=20,
            width_px=30,
            height_px=20,
        ),
    )

    assert crop.shape == (20, 30, 3)
    assert crop.flags["OWNDATA"] is True

    crop[:, :, :] = 0

    assert np.any(image[20:40, 30:60] != 0)


def test_export_writes_one_file_per_eye(
    tmp_path: Path,
) -> None:
    image = np.zeros(
        (120, 200, 3),
        dtype=np.uint8,
    )
    observations = (
        EyeObservation(
            side=EyeSide.LEFT,
            box=EyeBoundingBox(
                x_px=40,
                y_px=35,
                width_px=20,
                height_px=14,
            ),
            opening_state=EyeOpeningState.OPEN,
        ),
        EyeObservation(
            side=EyeSide.RIGHT,
            box=EyeBoundingBox(
                x_px=130,
                y_px=35,
                width_px=20,
                height_px=14,
            ),
            opening_state=(EyeOpeningState.PARTIALLY_OPEN),
        ),
    )

    artifacts = export_eye_crops(
        image,
        observations,
        output_directory=tmp_path,
        sample_stem="sample_001",
    )

    assert len(artifacts) == 2
    assert artifacts[0].filename == ("sample_001_left_open.png")
    assert artifacts[1].filename == ("sample_001_right_partially_open.png")

    for artifact in artifacts:
        crop_path = tmp_path / artifact.filename

        assert crop_path.exists()

        crop = cv2.imread(str(crop_path))

        assert crop is not None
        assert crop.shape[1] >= 48
        assert crop.shape[0] >= 32


@pytest.mark.parametrize(
    "padding_ratio",
    [-0.1, -1.0],
)
def test_expansion_rejects_negative_padding(
    padding_ratio: float,
) -> None:
    with pytest.raises(
        ValueError,
        match="padding_ratio",
    ):
        expand_eye_box(
            EyeBoundingBox(
                x_px=10,
                y_px=10,
                width_px=10,
                height_px=10,
            ),
            image_width_px=100,
            image_height_px=100,
            padding_ratio=padding_ratio,
        )
