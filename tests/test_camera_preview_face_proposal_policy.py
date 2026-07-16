"""Workbench face-proposal integration tests."""

import inspect

from oculidoc.vision.camera_preview_window import (
    CameraPreviewWindow,
)


def test_workbench_can_start_face_selection() -> None:
    source = inspect.getsource(CameraPreviewWindow._begin_face_selection)

    assert "_pending_face_selection = True" in source
    assert "begin_selection" in source


def test_face_selection_generates_eye_boxes() -> None:
    source = inspect.getsource(CameraPreviewWindow._finish_eye_selection)

    assert "FaceDetection" in source
    assert "propose_eye_regions_from_face" in source
    assert "ObservationSource.ALGORITHM" in source


def test_manual_eye_selection_overrides_source() -> None:
    source = inspect.getsource(CameraPreviewWindow._finish_eye_selection)

    assert "ObservationSource.MANUAL" in source
    assert "self._eye_sources[side]" in source


def test_observation_preserves_source() -> None:
    source = inspect.getsource(CameraPreviewWindow._build_observations)

    assert "self._eye_sources.get" in source
    assert "source=source" in source
