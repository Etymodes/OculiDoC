"""Regression tests for camera workbench save policy."""

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


def test_preview_records_patient_and_frame_identity() -> None:
    source = inspect.getsource(CameraPreviewWindow._save_snapshot)

    assert "patient_key=self._patient_key" in source
    assert "frame_key=frame_key" in source
    assert "build_camera_frame_key" in source


def test_workbench_displays_patient_context() -> None:
    constructor_source = inspect.getsource(CameraPreviewWindow.__init__)
    interface_source = inspect.getsource(CameraPreviewWindow._build_interface)

    assert "normalize_patient_key" in constructor_source
    assert "self._patient_key" in constructor_source
    assert "self.patient_status" in interface_source
