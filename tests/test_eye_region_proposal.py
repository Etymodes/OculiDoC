"""Face-based eye-region proposal tests."""

import numpy as np
import pytest

from oculidoc.vision.eye_observation import (
    EyeOpeningState,
    EyeSide,
    ObservationSource,
)
from oculidoc.vision.eye_region_proposal import (
    EyeRegionProposalConfig,
    FaceDetection,
    opencv_haar_face_detector_available,
    propose_eye_regions,
    propose_eye_regions_from_face,
)


class FakeFaceDetector:
    """Deterministic face detector used by tests."""

    def __init__(
        self,
        faces: tuple[FaceDetection, ...],
    ) -> None:
        self.faces = faces
        self.calls = 0

    def detect_faces(self, image):
        self.calls += 1
        return self.faces


def test_anatomical_left_is_on_image_right() -> None:
    observations = propose_eye_regions_from_face(
        FaceDetection(
            x_px=100,
            y_px=50,
            width_px=200,
            height_px=240,
        ),
        image_width_px=640,
        image_height_px=480,
    )

    left, right = observations

    assert left.side is EyeSide.LEFT
    assert right.side is EyeSide.RIGHT
    assert left.box.x_px > right.box.x_px


def test_proposals_are_unknown_algorithm_labels() -> None:
    observations = propose_eye_regions_from_face(
        FaceDetection(
            x_px=100,
            y_px=50,
            width_px=200,
            height_px=240,
        ),
        image_width_px=640,
        image_height_px=480,
    )

    for observation in observations:
        assert observation.opening_state is EyeOpeningState.UNKNOWN
        assert observation.source is ObservationSource.ALGORITHM
        assert "operator review" in (observation.note or "").lower()


def test_eye_boxes_remain_inside_image() -> None:
    observations = propose_eye_regions_from_face(
        FaceDetection(
            x_px=0,
            y_px=0,
            width_px=120,
            height_px=140,
        ),
        image_width_px=100,
        image_height_px=100,
    )

    for observation in observations:
        assert observation.box.x_px >= 0
        assert observation.box.y_px >= 0
        assert observation.box.right_px <= 100
        assert observation.box.bottom_px <= 100


def test_largest_face_is_selected() -> None:
    image = np.zeros(
        (480, 640, 3),
        dtype=np.uint8,
    )
    small_face = FaceDetection(
        x_px=20,
        y_px=20,
        width_px=80,
        height_px=80,
    )
    large_face = FaceDetection(
        x_px=200,
        y_px=100,
        width_px=220,
        height_px=240,
    )
    detector = FakeFaceDetector((small_face, large_face))

    proposal = propose_eye_regions(
        image,
        detector,
    )

    assert proposal is not None
    assert proposal.face == large_face
    assert detector.calls == 1


def test_no_face_returns_none() -> None:
    image = np.zeros(
        (480, 640, 3),
        dtype=np.uint8,
    )
    detector = FakeFaceDetector(())

    assert (
        propose_eye_regions(
            image,
            detector,
        )
        is None
    )


def test_grayscale_image_is_rejected() -> None:
    grayscale = np.zeros(
        (100, 100),
        dtype=np.uint8,
    )

    with pytest.raises(
        ValueError,
        match="three-channel",
    ):
        propose_eye_regions(
            grayscale,
            FakeFaceDetector(()),
        )


@pytest.mark.parametrize(
    "ratio",
    [0.0, 1.0, -0.1],
)
def test_invalid_geometry_ratio_is_rejected(
    ratio: float,
) -> None:
    with pytest.raises(
        ValueError,
        match="ratios",
    ):
        EyeRegionProposalConfig(eye_width_ratio=ratio)


def test_haar_capability_check_returns_boolean() -> None:
    result = opencv_haar_face_detector_available()

    assert isinstance(result, bool)
