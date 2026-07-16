"""Conservative face-based eye-region proposals."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from oculidoc.vision.eye_observation import (
    EyeBoundingBox,
    EyeObservation,
    EyeOpeningState,
    EyeSide,
    ObservationSource,
)


@dataclass(frozen=True, slots=True)
class FaceDetection:
    """One detected face in image pixel coordinates."""

    x_px: int
    y_px: int
    width_px: int
    height_px: int
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.x_px < 0 or self.y_px < 0:
            raise ValueError("Face coordinates cannot be negative.")

        if self.width_px <= 0 or self.height_px <= 0:
            raise ValueError("Face dimensions must be positive.")

        if self.confidence is not None:
            if not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
                raise ValueError("confidence must be between 0 and 1.")

    @property
    def right_px(self) -> int:
        """Return the exclusive right edge."""
        return self.x_px + self.width_px

    @property
    def bottom_px(self) -> int:
        """Return the exclusive bottom edge."""
        return self.y_px + self.height_px

    @property
    def area_px(self) -> int:
        """Return face area in pixels."""
        return self.width_px * self.height_px


class FaceDetectorProtocol(Protocol):
    """Interface required by the proposal generator."""

    def detect_faces(
        self,
        image: NDArray[np.uint8],
    ) -> tuple[FaceDetection, ...]:
        """Return all detected faces."""


@dataclass(frozen=True, slots=True)
class EyeRegionProposalConfig:
    """Relative face geometry used to estimate both eye regions."""

    eye_width_ratio: float = 0.28
    eye_height_ratio: float = 0.18
    eye_center_y_ratio: float = 0.39
    anatomical_right_center_x_ratio: float = 0.32
    anatomical_left_center_x_ratio: float = 0.68
    minimum_eye_width_px: int = 12
    minimum_eye_height_px: int = 8

    def __post_init__(self) -> None:
        ratio_fields = (
            self.eye_width_ratio,
            self.eye_height_ratio,
            self.eye_center_y_ratio,
            self.anatomical_right_center_x_ratio,
            self.anatomical_left_center_x_ratio,
        )

        if any(not isfinite(value) or not 0.0 < value < 1.0 for value in ratio_fields):
            raise ValueError("Eye proposal ratios must be between 0 and 1.")

        if self.minimum_eye_width_px <= 0 or self.minimum_eye_height_px <= 0:
            raise ValueError("Minimum eye dimensions must be positive.")


@dataclass(frozen=True, slots=True)
class EyeRegionProposal:
    """Face detection and its proposed anatomical eye boxes."""

    face: FaceDetection
    observations: tuple[
        EyeObservation,
        EyeObservation,
    ]


def _resolve_cascade_classifier_type() -> Any | None:
    """Resolve OpenCV 4 or OpenCV 5 CascadeClassifier."""
    classifier_type = getattr(
        cv2,
        "CascadeClassifier",
        None,
    )

    if classifier_type is not None:
        return classifier_type

    objdetect_module = getattr(
        cv2,
        "objdetect",
        None,
    )

    if objdetect_module is None:
        return None

    return getattr(
        objdetect_module,
        "CascadeClassifier",
        None,
    )


def _default_haar_cascade_path() -> Path | None:
    """Resolve OpenCV's bundled frontal-face cascade."""
    data_module = getattr(
        cv2,
        "data",
        None,
    )

    if data_module is None:
        return None

    cascade_directory = getattr(
        data_module,
        "haarcascades",
        None,
    )

    if not cascade_directory:
        return None

    return Path(cascade_directory) / "haarcascade_frontalface_default.xml"


def opencv_haar_face_detector_available() -> bool:
    """Return whether this OpenCV build supports Haar faces."""
    return (
        _resolve_cascade_classifier_type() is not None and _default_haar_cascade_path() is not None
    )


class OpenCVHaarFaceDetector:
    """OpenCV frontal-face detector used only for proposals."""

    def __init__(
        self,
        *,
        cascade_path: str | Path | None = None,
        scale_factor: float = 1.1,
        minimum_neighbors: int = 5,
        minimum_face_size_px: int = 80,
    ) -> None:
        if scale_factor <= 1.0:
            raise ValueError("scale_factor must be greater than 1.")

        if minimum_neighbors < 0:
            raise ValueError("minimum_neighbors cannot be negative.")

        if minimum_face_size_px <= 0:
            raise ValueError("minimum_face_size_px must be positive.")

        classifier_type = _resolve_cascade_classifier_type()

        if classifier_type is None:
            raise RuntimeError(
                "This OpenCV build does not provide "
                "CascadeClassifier in either cv2 or "
                "cv2.objdetect."
            )

        if cascade_path is not None:
            path = Path(cascade_path)
        else:
            default_path = _default_haar_cascade_path()

            if default_path is None:
                raise RuntimeError(
                    "This OpenCV build does not expose the bundled Haar cascade directory."
                )

            path = default_path

        classifier = classifier_type(str(path))
        empty_method = getattr(
            classifier,
            "empty",
            None,
        )

        if empty_method is None or empty_method():
            raise RuntimeError(f"Could not load face cascade: {path}")

        self._classifier = classifier
        self._scale_factor = scale_factor
        self._minimum_neighbors = minimum_neighbors
        self._minimum_face_size_px = minimum_face_size_px

    def detect_faces(
        self,
        image: NDArray[np.uint8],
    ) -> tuple[FaceDetection, ...]:
        """Detect frontal faces in a BGR frame."""
        _validate_bgr_image(image)

        grayscale = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2GRAY,
        )
        equalized = cv2.equalizeHist(grayscale)

        detected = self._classifier.detectMultiScale(
            equalized,
            scaleFactor=self._scale_factor,
            minNeighbors=self._minimum_neighbors,
            minSize=(
                self._minimum_face_size_px,
                self._minimum_face_size_px,
            ),
        )

        return tuple(
            FaceDetection(
                x_px=int(x_px),
                y_px=int(y_px),
                width_px=int(width_px),
                height_px=int(height_px),
            )
            for (
                x_px,
                y_px,
                width_px,
                height_px,
            ) in detected
        )


def _validate_bgr_image(
    image: NDArray[np.uint8],
) -> None:
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array.")

    if image.dtype != np.uint8:
        raise ValueError("image must use uint8 pixels.")

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a three-channel BGR frame.")

    if image.size == 0:
        raise ValueError("image cannot be empty.")


def _centered_clipped_box(
    *,
    center_x: float,
    center_y: float,
    requested_width_px: int,
    requested_height_px: int,
    image_width_px: int,
    image_height_px: int,
) -> EyeBoundingBox:
    width_px = min(
        max(requested_width_px, 1),
        image_width_px,
    )
    height_px = min(
        max(requested_height_px, 1),
        image_height_px,
    )

    left_px = round(center_x - width_px / 2)
    top_px = round(center_y - height_px / 2)

    left_px = min(
        max(left_px, 0),
        image_width_px - width_px,
    )
    top_px = min(
        max(top_px, 0),
        image_height_px - height_px,
    )

    return EyeBoundingBox(
        x_px=left_px,
        y_px=top_px,
        width_px=width_px,
        height_px=height_px,
    )


def propose_eye_regions_from_face(
    face: FaceDetection,
    *,
    image_width_px: int,
    image_height_px: int,
    config: EyeRegionProposalConfig | None = None,
) -> tuple[
    EyeObservation,
    EyeObservation,
]:
    """Estimate anatomical left and right eye regions."""
    if image_width_px <= 0 or image_height_px <= 0:
        raise ValueError("Image dimensions must be positive.")

    active_config = config or EyeRegionProposalConfig()

    eye_width_px = max(
        active_config.minimum_eye_width_px,
        round(face.width_px * active_config.eye_width_ratio),
    )
    eye_height_px = max(
        active_config.minimum_eye_height_px,
        round(face.height_px * active_config.eye_height_ratio),
    )

    center_y = face.y_px + face.height_px * active_config.eye_center_y_ratio

    anatomical_right_center_x = (
        face.x_px + face.width_px * active_config.anatomical_right_center_x_ratio
    )
    anatomical_left_center_x = (
        face.x_px + face.width_px * active_config.anatomical_left_center_x_ratio
    )

    anatomical_left_box = _centered_clipped_box(
        center_x=anatomical_left_center_x,
        center_y=center_y,
        requested_width_px=eye_width_px,
        requested_height_px=eye_height_px,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
    )
    anatomical_right_box = _centered_clipped_box(
        center_x=anatomical_right_center_x,
        center_y=center_y,
        requested_width_px=eye_width_px,
        requested_height_px=eye_height_px,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
    )

    return (
        EyeObservation(
            side=EyeSide.LEFT,
            box=anatomical_left_box,
            opening_state=EyeOpeningState.UNKNOWN,
            source=ObservationSource.ALGORITHM,
            confidence=face.confidence,
            note="Face-geometry proposal; operator review required.",
        ),
        EyeObservation(
            side=EyeSide.RIGHT,
            box=anatomical_right_box,
            opening_state=EyeOpeningState.UNKNOWN,
            source=ObservationSource.ALGORITHM,
            confidence=face.confidence,
            note="Face-geometry proposal; operator review required.",
        ),
    )


def propose_eye_regions(
    image: NDArray[np.uint8],
    detector: FaceDetectorProtocol,
    *,
    config: EyeRegionProposalConfig | None = None,
) -> EyeRegionProposal | None:
    """Detect the largest face and propose both eye regions."""
    _validate_bgr_image(image)

    faces = detector.detect_faces(image)

    if not faces:
        return None

    face = max(
        faces,
        key=lambda candidate: candidate.area_px,
    )
    observations = propose_eye_regions_from_face(
        face,
        image_width_px=image.shape[1],
        image_height_px=image.shape[0],
        config=config,
    )

    return EyeRegionProposal(
        face=face,
        observations=observations,
    )
