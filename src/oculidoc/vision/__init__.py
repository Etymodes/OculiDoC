"""Visual observation and camera preview utilities."""

from oculidoc.vision.camera_preview import (
    CameraPreviewController,
    bgr_frame_to_qimage,
)
from oculidoc.vision.eye_crop import (
    EyeCropArtifact,
    crop_eye_image,
    expand_eye_box,
    export_eye_crops,
)
from oculidoc.vision.eye_observation import (
    EYE_STATE_COLORS_BGR,
    EYE_STATE_LABELS,
    EyeBoundingBox,
    EyeObservation,
    EyeOpeningState,
    EyeSide,
    ObservationReviewStatus,
    ObservationSource,
)
from oculidoc.vision.eye_record import (
    EyeObservationRecord,
    build_eye_observation_record,
    raw_path_for_overlay,
    record_path_for_overlay,
    write_eye_observation_record,
)
from oculidoc.vision.image_selection_widget import (
    ImageSelectionLabel,
    fitted_image_rect,
    map_display_selection_to_image,
)
from oculidoc.vision.overlay import (
    draw_eye_observations,
)

__all__ = [
    "EYE_STATE_COLORS_BGR",
    "EYE_STATE_LABELS",
    "CameraPreviewController",
    "EyeBoundingBox",
    "EyeCropArtifact",
    "EyeObservation",
    "EyeObservationRecord",
    "EyeOpeningState",
    "EyeSide",
    "ImageSelectionLabel",
    "ObservationReviewStatus",
    "ObservationSource",
    "bgr_frame_to_qimage",
    "build_eye_observation_record",
    "crop_eye_image",
    "draw_eye_observations",
    "expand_eye_box",
    "export_eye_crops",
    "fitted_image_rect",
    "map_display_selection_to_image",
    "raw_path_for_overlay",
    "record_path_for_overlay",
    "write_eye_observation_record",
]
