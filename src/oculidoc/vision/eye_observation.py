"""Eye-region and eye-opening observation models."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class EyeSide(StrEnum):
    """Anatomical side of the observed eye."""

    LEFT = "left"
    RIGHT = "right"


class EyeOpeningState(StrEnum):
    """Visible eye-opening state, not a clinical diagnosis."""

    OPEN = "open"
    PARTIALLY_OPEN = "partially_open"
    CLOSED = "closed"
    OBSCURED = "obscured"
    UNKNOWN = "unknown"


class ObservationSource(StrEnum):
    """How an eye observation was produced."""

    MANUAL = "manual"
    ALGORITHM = "algorithm"


class ObservationReviewStatus(StrEnum):
    """Human-review state of an eye-region observation."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    MANUAL = "manual"


EYE_STATE_LABELS: dict[
    EyeOpeningState,
    str,
] = {
    EyeOpeningState.OPEN: "OPEN",
    EyeOpeningState.PARTIALLY_OPEN: "PARTIAL",
    EyeOpeningState.CLOSED: "CLOSED",
    EyeOpeningState.OBSCURED: "OBSCURED",
    EyeOpeningState.UNKNOWN: "UNKNOWN",
}


EYE_STATE_COLORS_BGR: dict[
    EyeOpeningState,
    tuple[int, int, int],
] = {
    EyeOpeningState.OPEN: (0, 200, 0),
    EyeOpeningState.PARTIALLY_OPEN: (0, 191, 255),
    EyeOpeningState.CLOSED: (0, 0, 255),
    EyeOpeningState.OBSCURED: (180, 70, 180),
    EyeOpeningState.UNKNOWN: (160, 160, 160),
}


@dataclass(frozen=True, slots=True)
class EyeBoundingBox:
    """Pixel-space rectangular eye region."""

    x_px: int
    y_px: int
    width_px: int
    height_px: int

    def __post_init__(self) -> None:

        if self.x_px < 0 or self.y_px < 0:
            raise ValueError("Bounding-box coordinates cannot be negative.")

        if self.width_px <= 0 or self.height_px <= 0:
            raise ValueError("Bounding-box dimensions must be positive.")

    @property
    def right_px(self) -> int:
        """Return exclusive right coordinate."""
        return self.x_px + self.width_px

    @property
    def bottom_px(self) -> int:
        """Return exclusive bottom coordinate."""
        return self.y_px + self.height_px

    def clip_to_image(
        self,
        *,
        image_width_px: int,
        image_height_px: int,
    ) -> "EyeBoundingBox | None":
        """Clip the box to image bounds."""
        if image_width_px <= 0 or image_height_px <= 0:
            raise ValueError("Image dimensions must be positive.")

        left = min(
            max(self.x_px, 0),
            image_width_px,
        )
        top = min(
            max(self.y_px, 0),
            image_height_px,
        )
        right = min(
            max(self.right_px, 0),
            image_width_px,
        )
        bottom = min(
            max(self.bottom_px, 0),
            image_height_px,
        )

        if right <= left or bottom <= top:
            return None

        return EyeBoundingBox(
            x_px=left,
            y_px=top,
            width_px=right - left,
            height_px=bottom - top,
        )


@dataclass(frozen=True, slots=True)
class EyeObservation:
    """One labeled eye region in an image."""

    side: EyeSide
    box: EyeBoundingBox
    opening_state: EyeOpeningState
    source: ObservationSource = ObservationSource.MANUAL

    review_status: ObservationReviewStatus | None = None
    confidence: float | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        derived_review_status = self.review_status

        if derived_review_status is None:
            derived_review_status = (
                ObservationReviewStatus.MANUAL
                if self.source is ObservationSource.MANUAL
                else ObservationReviewStatus.PROPOSED
            )

        object.__setattr__(
            self,
            "review_status",
            derived_review_status,
        )

        if self.confidence is not None:
            if not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
                raise ValueError("confidence must be between 0 and 1.")

        if self.note is not None:
            object.__setattr__(
                self,
                "note",
                self.note.strip() or None,
            )

    @property
    def color_bgr(self) -> tuple[int, int, int]:
        """Return the display color for this state."""
        return EYE_STATE_COLORS_BGR[self.opening_state]

    @property
    def label(self) -> str:
        """Return a compact ASCII overlay label."""
        side_label = "L" if self.side is EyeSide.LEFT else "R"
        state_label = EYE_STATE_LABELS[self.opening_state]

        if self.confidence is None:
            return f"{side_label} {state_label}"

        confidence_percent = round(self.confidence * 100)

        return f"{side_label} {state_label} {confidence_percent}%"
