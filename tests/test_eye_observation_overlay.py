"""Eye observation and overlay tests."""

import numpy as np
import pytest

from oculidoc.vision import (
    EYE_STATE_COLORS_BGR,
    EyeBoundingBox,
    EyeObservation,
    EyeOpeningState,
    EyeSide,
    ObservationReviewStatus,
    ObservationSource,
    draw_eye_observations,
)


def test_eye_observation_label_and_color() -> None:
    observation = EyeObservation(
        side=EyeSide.LEFT,
        box=EyeBoundingBox(
            x_px=10,
            y_px=20,
            width_px=30,
            height_px=15,
        ),
        opening_state=EyeOpeningState.OPEN,
        source=ObservationSource.ALGORITHM,
        confidence=0.92,
    )

    assert observation.label == "L OPEN 92%"
    assert observation.color_bgr == EYE_STATE_COLORS_BGR[EyeOpeningState.OPEN]


def test_eye_states_use_distinct_colors() -> None:
    colors = set(EYE_STATE_COLORS_BGR.values())

    assert len(colors) == len(EyeOpeningState)


def test_bounding_box_clips_to_image() -> None:
    box = EyeBoundingBox(
        x_px=80,
        y_px=40,
        width_px=40,
        height_px=30,
    )

    clipped = box.clip_to_image(
        image_width_px=100,
        image_height_px=60,
    )

    assert clipped == EyeBoundingBox(
        x_px=80,
        y_px=40,
        width_px=20,
        height_px=20,
    )


def test_box_outside_image_returns_none() -> None:
    box = EyeBoundingBox(
        x_px=200,
        y_px=200,
        width_px=20,
        height_px=20,
    )

    assert (
        box.clip_to_image(
            image_width_px=100,
            image_height_px=100,
        )
        is None
    )


def test_overlay_returns_modified_copy() -> None:
    image = np.zeros(
        (100, 160, 3),
        dtype=np.uint8,
    )
    original = image.copy()

    observation = EyeObservation(
        side=EyeSide.RIGHT,
        box=EyeBoundingBox(
            x_px=40,
            y_px=30,
            width_px=60,
            height_px=25,
        ),
        opening_state=EyeOpeningState.CLOSED,
    )

    rendered = draw_eye_observations(
        image,
        [observation],
    )

    assert np.array_equal(
        image,
        original,
    )
    assert not np.array_equal(
        rendered,
        original,
    )

    assert tuple(rendered[30, 40]) == observation.color_bgr


def test_overlay_supports_multiple_states() -> None:
    image = np.zeros(
        (120, 240, 3),
        dtype=np.uint8,
    )
    left = EyeObservation(
        side=EyeSide.LEFT,
        box=EyeBoundingBox(
            x_px=20,
            y_px=40,
            width_px=70,
            height_px=30,
        ),
        opening_state=(EyeOpeningState.PARTIALLY_OPEN),
    )
    right = EyeObservation(
        side=EyeSide.RIGHT,
        box=EyeBoundingBox(
            x_px=140,
            y_px=40,
            width_px=70,
            height_px=30,
        ),
        opening_state=EyeOpeningState.CLOSED,
    )

    rendered = draw_eye_observations(
        image,
        [left, right],
    )

    assert tuple(rendered[40, 20]) == left.color_bgr
    assert tuple(rendered[40, 140]) == right.color_bgr


@pytest.mark.parametrize(
    "confidence",
    [-0.01, 1.01, float("inf")],
)
def test_observation_rejects_invalid_confidence(
    confidence: float,
) -> None:
    with pytest.raises(
        ValueError,
        match="confidence",
    ):
        EyeObservation(
            side=EyeSide.LEFT,
            box=EyeBoundingBox(
                x_px=0,
                y_px=0,
                width_px=10,
                height_px=10,
            ),
            opening_state=EyeOpeningState.UNKNOWN,
            confidence=confidence,
        )


def test_overlay_rejects_non_bgr_image() -> None:
    grayscale = np.zeros(
        (100, 100),
        dtype=np.uint8,
    )

    with pytest.raises(
        ValueError,
        match="three-channel",
    ):
        draw_eye_observations(
            grayscale,
            [],
        )


def test_manual_observation_defaults_to_manual_review() -> None:
    observation = EyeObservation(
        side=EyeSide.LEFT,
        box=EyeBoundingBox(
            x_px=10,
            y_px=10,
            width_px=20,
            height_px=10,
        ),
        opening_state=EyeOpeningState.OPEN,
    )

    assert observation.review_status is ObservationReviewStatus.MANUAL


def test_algorithm_observation_defaults_to_proposed() -> None:
    observation = EyeObservation(
        side=EyeSide.LEFT,
        box=EyeBoundingBox(
            x_px=10,
            y_px=10,
            width_px=20,
            height_px=10,
        ),
        opening_state=EyeOpeningState.UNKNOWN,
        source=ObservationSource.ALGORITHM,
    )

    assert observation.review_status is ObservationReviewStatus.PROPOSED
