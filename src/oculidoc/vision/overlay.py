"""OpenCV rendering of eye observations."""

from collections.abc import Iterable

import cv2
import numpy as np
from numpy.typing import NDArray

from oculidoc.vision.eye_observation import (
    EyeObservation,
)


def draw_eye_observations(
    image: NDArray[np.uint8],
    observations: Iterable[EyeObservation],
    *,
    line_thickness: int = 2,
) -> NDArray[np.uint8]:
    """Return a copy of an image with labeled eye boxes."""
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array.")

    if image.dtype != np.uint8:
        raise ValueError("image must use uint8 pixels.")

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a three-channel BGR image.")

    if image.size == 0:
        raise ValueError("image cannot be empty.")

    if line_thickness <= 0:
        raise ValueError("line_thickness must be positive.")

    rendered = image.copy()
    image_height_px, image_width_px = rendered.shape[:2]

    for observation in observations:
        clipped_box = observation.box.clip_to_image(
            image_width_px=image_width_px,
            image_height_px=image_height_px,
        )

        if clipped_box is None:
            continue

        color = observation.color_bgr

        left = clipped_box.x_px
        top = clipped_box.y_px
        right = clipped_box.right_px - 1
        bottom = clipped_box.bottom_px - 1

        cv2.rectangle(
            rendered,
            (left, top),
            (right, bottom),
            color,
            line_thickness,
        )

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        text_thickness = 1

        (
            (
                text_width,
                text_height,
            ),
            baseline,
        ) = cv2.getTextSize(
            observation.label,
            font,
            font_scale,
            text_thickness,
        )

        label_left = left
        label_bottom = max(
            top,
            text_height + baseline + 4,
        )
        label_top = max(
            0,
            label_bottom - text_height - baseline - 4,
        )
        label_right = min(
            image_width_px - 1,
            label_left + text_width + 6,
        )

        cv2.rectangle(
            rendered,
            (label_left, label_top),
            (label_right, label_bottom),
            color,
            thickness=-1,
        )

        cv2.putText(
            rendered,
            observation.label,
            (
                label_left + 3,
                label_bottom - baseline - 2,
            ),
            font,
            font_scale,
            (0, 0, 0),
            text_thickness,
            cv2.LINE_AA,
        )

    return rendered
