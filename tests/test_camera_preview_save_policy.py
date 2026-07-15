"""Regression tests for the camera workbench save policy."""

import inspect

from oculidoc.vision.camera_preview_window import (
    CameraPreviewWindow,
)


def test_preview_saves_to_application_dataset() -> None:
    source = inspect.getsource(CameraPreviewWindow._save_snapshot)

    assert "eye_observation_dataset_directory" in source
    assert "next_eye_sample_paths" in source
    assert "QFileDialog" not in source
    assert "getSaveFileName" not in source
    assert "Desktop" not in source
