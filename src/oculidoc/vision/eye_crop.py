"""Export padded per-eye image crops for classification."""

from dataclasses import dataclass
from math import ceil, floor
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from oculidoc.vision.eye_observation import (
    EyeBoundingBox,
    EyeObservation,
    EyeOpeningState,
    EyeSide,
)


@dataclass(frozen=True, slots=True)
class EyeCropArtifact:
    """Metadata for one exported eye image crop."""

    side: EyeSide
    opening_state: EyeOpeningState
    filename: str
    box: EyeBoundingBox

    def __post_init__(self) -> None:
        normalized_filename = self.filename.strip()

        if not normalized_filename:
            raise ValueError("filename cannot be empty.")

        object.__setattr__(
            self,
            "filename",
            normalized_filename,
        )


def _bounded_interval(
    *,
    center: float,
    requested_length: int,
    limit: int,
) -> tuple[int, int]:
    """Create a fixed-length interval within zero and limit."""
    if requested_length <= 0:
        raise ValueError("requested_length must be positive.")

    if limit <= 0:
        raise ValueError("limit must be positive.")

    length = min(
        requested_length,
        limit,
    )
    start = floor(center - length / 2)
    end = start + length

    if start < 0:
        end -= start
        start = 0

    if end > limit:
        start -= end - limit
        end = limit

    start = max(
        0,
        start,
    )

    return start, end


def expand_eye_box(
    box: EyeBoundingBox,
    *,
    image_width_px: int,
    image_height_px: int,
    padding_ratio: float = 0.75,
    minimum_width_px: int = 48,
    minimum_height_px: int = 32,
) -> EyeBoundingBox:
    """Expand a tight eye box while staying inside the image."""
    if image_width_px <= 0 or image_height_px <= 0:
        raise ValueError("Image dimensions must be positive.")

    if padding_ratio < 0:
        raise ValueError("padding_ratio cannot be negative.")

    if minimum_width_px <= 0 or minimum_height_px <= 0:
        raise ValueError("Minimum crop dimensions must be positive.")

    requested_width = max(
        minimum_width_px,
        ceil(box.width_px * (1.0 + 2.0 * padding_ratio)),
    )
    requested_height = max(
        minimum_height_px,
        ceil(box.height_px * (1.0 + 2.0 * padding_ratio)),
    )

    center_x = box.x_px + box.width_px / 2.0
    center_y = box.y_px + box.height_px / 2.0

    left, right = _bounded_interval(
        center=center_x,
        requested_length=requested_width,
        limit=image_width_px,
    )
    top, bottom = _bounded_interval(
        center=center_y,
        requested_length=requested_height,
        limit=image_height_px,
    )

    return EyeBoundingBox(
        x_px=left,
        y_px=top,
        width_px=right - left,
        height_px=bottom - top,
    )


def crop_eye_image(
    image: NDArray[np.uint8],
    box: EyeBoundingBox,
) -> NDArray[np.uint8]:
    """Extract an owned BGR crop from one image."""
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array.")

    if image.dtype != np.uint8:
        raise ValueError("image must use uint8 pixels.")

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a three-channel BGR frame.")

    clipped_box = box.clip_to_image(
        image_width_px=image.shape[1],
        image_height_px=image.shape[0],
    )

    if clipped_box is None:
        raise ValueError("The crop box does not overlap the image.")

    crop = image[
        clipped_box.y_px : clipped_box.bottom_px,
        clipped_box.x_px : clipped_box.right_px,
    ].copy()

    if crop.size == 0:
        raise ValueError("The resulting eye crop is empty.")

    return crop


def export_eye_crops(
    image: NDArray[np.uint8],
    observations: tuple[EyeObservation, ...],
    *,
    output_directory: str | Path,
    sample_stem: str,
    padding_ratio: float = 0.75,
    minimum_width_px: int = 48,
    minimum_height_px: int = 32,
) -> tuple[EyeCropArtifact, ...]:
    """Export one padded crop for each eye observation."""
    normalized_stem = sample_stem.strip()

    if not normalized_stem:
        raise ValueError("sample_stem cannot be empty.")

    output_path = Path(output_directory)
    output_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    artifacts: list[EyeCropArtifact] = []

    for observation in observations:
        crop_box = expand_eye_box(
            observation.box,
            image_width_px=image.shape[1],
            image_height_px=image.shape[0],
            padding_ratio=padding_ratio,
            minimum_width_px=minimum_width_px,
            minimum_height_px=minimum_height_px,
        )
        crop = crop_eye_image(
            image,
            crop_box,
        )

        filename = (
            f"{normalized_stem}_{observation.side.value}_{observation.opening_state.value}.png"
        )
        final_path = output_path / filename
        temporary_path = output_path / (f".{final_path.stem}.tmp.png")

        write_ok = cv2.imwrite(
            str(temporary_path),
            crop,
        )

        if not write_ok:
            raise RuntimeError(f"Could not save eye crop: {final_path}")

        temporary_path.replace(final_path)

        artifacts.append(
            EyeCropArtifact(
                side=observation.side,
                opening_state=(observation.opening_state),
                filename=filename,
                box=crop_box,
            )
        )

    return tuple(artifacts)
