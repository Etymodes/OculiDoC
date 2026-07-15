"""Persistent eye-observation record tests."""

import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns

import numpy as np
import pytest

from oculidoc.devices import (
    CameraFramePacket,
    DeviceTimestamp,
)
from oculidoc.vision import (
    EyeBoundingBox,
    EyeCropArtifact,
    EyeObservation,
    EyeObservationRecord,
    EyeOpeningState,
    EyeSide,
    build_eye_observation_record,
    raw_path_for_overlay,
    record_path_for_overlay,
    write_eye_observation_record,
)
from oculidoc.vision.frame_identity import (
    build_camera_frame_key,
)

PATIENT_KEY = "patient-test-001"


def create_packet() -> CameraFramePacket:
    """Create a deterministic camera packet."""
    return CameraFramePacket(
        timestamp=DeviceTimestamp(
            sequence=12,
            monotonic_timestamp_ns=monotonic_ns(),
            utc_timestamp=datetime(
                2026,
                7,
                15,
                9,
                30,
                tzinfo=UTC,
            ),
        ),
        frame_index=12,
        image=np.zeros(
            (480, 640, 3),
            dtype=np.uint8,
        ),
    )


def create_observations():
    """Create left and right eye labels."""
    return (
        EyeObservation(
            side=EyeSide.LEFT,
            box=EyeBoundingBox(
                x_px=180,
                y_px=150,
                width_px=70,
                height_px=30,
            ),
            opening_state=(EyeOpeningState.PARTIALLY_OPEN),
        ),
        EyeObservation(
            side=EyeSide.RIGHT,
            box=EyeBoundingBox(
                x_px=380,
                y_px=150,
                width_px=70,
                height_px=30,
            ),
            opening_state=(EyeOpeningState.CLOSED),
        ),
    )


def create_frame_key(
    packet: CameraFramePacket,
) -> str:
    """Create a valid deterministic frame identity."""
    return build_camera_frame_key(
        packet=packet,
        camera_index=0,
        backend_name="DSHOW",
    )


def create_crops():
    """Create crop metadata matching the observations."""
    return (
        EyeCropArtifact(
            side=EyeSide.LEFT,
            opening_state=(EyeOpeningState.PARTIALLY_OPEN),
            filename=("sample_left_partially_open.png"),
            box=EyeBoundingBox(
                x_px=165,
                y_px=140,
                width_px=100,
                height_px=50,
            ),
        ),
        EyeCropArtifact(
            side=EyeSide.RIGHT,
            opening_state=EyeOpeningState.CLOSED,
            filename="sample_right_closed.png",
            box=EyeBoundingBox(
                x_px=365,
                y_px=140,
                width_px=100,
                height_px=50,
            ),
        ),
    )


def build_record(
    *,
    crops=(),
) -> EyeObservationRecord:
    """Build one complete test record."""
    packet = create_packet()

    return build_eye_observation_record(
        packet=packet,
        patient_key=PATIENT_KEY,
        frame_key=create_frame_key(packet),
        camera_index=0,
        backend_name="DSHOW",
        raw_image_filename="sample_raw.png",
        overlay_image_filename="sample.png",
        observations=create_observations(),
        crops=crops,
    )


def test_build_record_contains_patient_and_frame_data() -> None:
    record = build_record()
    payload = record.to_dict()

    assert payload["schema_version"] == "1.2"
    assert payload["patient"] == {"key": PATIENT_KEY}
    assert payload["camera"] == {
        "index": 0,
        "backend": "DSHOW",
    }
    assert payload["frame"]["index"] == 12
    assert len(payload["frame"]["identity"]) == 64
    assert payload["frame"]["width_px"] == 640
    assert payload["frame"]["height_px"] == 480

    assert len(payload["observations"]) == 2
    assert payload["observations"][0]["side"] == "left"
    assert payload["observations"][0]["opening_state"] == "partially_open"


def test_record_writes_utf8_json(
    tmp_path: Path,
) -> None:
    record = build_record()
    output_path = tmp_path / "sample.json"

    returned_path = write_eye_observation_record(
        record,
        output_path,
    )

    assert returned_path == output_path
    assert output_path.exists()
    assert not (tmp_path / "sample.json.tmp").exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["patient"]["key"] == PATIENT_KEY
    assert payload["frame"]["raw_image_filename"] == "sample_raw.png"


def test_companion_paths() -> None:
    overlay = Path("dataset/frame_001.png")

    assert raw_path_for_overlay(overlay) == Path("dataset/frame_001_raw.png")
    assert record_path_for_overlay(overlay) == Path("dataset/frame_001.json")


def test_record_rejects_duplicate_eye_side() -> None:
    packet = create_packet()
    observation = create_observations()[0]

    with pytest.raises(
        ValueError,
        match="one observation per eye side",
    ):
        EyeObservationRecord(
            schema_version="1.2",
            recorded_at_utc=datetime.now(UTC),
            patient_key=PATIENT_KEY,
            frame_key=create_frame_key(packet),
            camera_index=0,
            backend_name="DSHOW",
            frame_index=0,
            image_width_px=640,
            image_height_px=480,
            raw_image_filename="raw.png",
            overlay_image_filename="overlay.png",
            observations=(
                observation,
                observation,
            ),
        )


def test_record_serializes_crop_metadata() -> None:
    payload = build_record(crops=create_crops()).to_dict()

    assert payload["observations"][0]["crop"] == {
        "filename": ("sample_left_partially_open.png"),
        "box": {
            "x_px": 165,
            "y_px": 140,
            "width_px": 100,
            "height_px": 50,
        },
    }


def test_record_rejects_invalid_patient_key() -> None:
    packet = create_packet()

    with pytest.raises(ValueError):
        build_eye_observation_record(
            packet=packet,
            patient_key="../wrong-patient",
            frame_key=create_frame_key(packet),
            camera_index=0,
            backend_name="DSHOW",
            raw_image_filename="raw.png",
            overlay_image_filename="overlay.png",
            observations=create_observations(),
        )


@pytest.mark.parametrize(
    "frame_key",
    ["", "not-a-hash", "a" * 63],
)
def test_record_rejects_invalid_frame_key(
    frame_key: str,
) -> None:
    packet = create_packet()

    with pytest.raises(
        ValueError,
        match="frame_key",
    ):
        build_eye_observation_record(
            packet=packet,
            patient_key=PATIENT_KEY,
            frame_key=frame_key,
            camera_index=0,
            backend_name="DSHOW",
            raw_image_filename="raw.png",
            overlay_image_filename="overlay.png",
            observations=create_observations(),
        )
