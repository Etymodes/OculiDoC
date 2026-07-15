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
    EyeObservation,
    EyeObservationRecord,
    EyeOpeningState,
    EyeSide,
    build_eye_observation_record,
    raw_path_for_overlay,
    record_path_for_overlay,
    write_eye_observation_record,
)


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


def test_build_record_contains_frame_and_eye_data() -> None:
    record = build_eye_observation_record(
        packet=create_packet(),
        camera_index=0,
        backend_name="DSHOW",
        raw_image_filename="sample_raw.png",
        overlay_image_filename="sample.png",
        observations=create_observations(),
    )

    payload = record.to_dict()

    assert payload["schema_version"] == "1.0"
    assert payload["camera"] == {
        "index": 0,
        "backend": "DSHOW",
    }
    assert payload["frame"]["index"] == 12
    assert payload["frame"]["width_px"] == 640
    assert payload["frame"]["height_px"] == 480

    assert len(payload["observations"]) == 2
    assert payload["observations"][0]["side"] == "left"
    assert payload["observations"][0]["opening_state"] == "partially_open"
    assert payload["observations"][1]["opening_state"] == "closed"


def test_record_writes_utf8_json(
    tmp_path: Path,
) -> None:
    record = build_eye_observation_record(
        packet=create_packet(),
        camera_index=0,
        backend_name="DSHOW",
        raw_image_filename="sample_raw.png",
        overlay_image_filename="sample.png",
        observations=create_observations(),
    )
    output_path = tmp_path / "sample.json"

    returned_path = write_eye_observation_record(
        record,
        output_path,
    )

    assert returned_path == output_path
    assert output_path.exists()
    assert not (tmp_path / "sample.json.tmp").exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["frame"]["raw_image_filename"] == "sample_raw.png"
    assert payload["frame"]["overlay_image_filename"] == "sample.png"


def test_companion_paths() -> None:
    overlay = Path("dataset/patient_001/frame_001.png")

    assert raw_path_for_overlay(overlay) == Path("dataset/patient_001/frame_001_raw.png")
    assert record_path_for_overlay(overlay) == Path("dataset/patient_001/frame_001.json")


def test_record_rejects_duplicate_eye_side() -> None:
    observation = create_observations()[0]

    with pytest.raises(
        ValueError,
        match="one observation per eye side",
    ):
        EyeObservationRecord(
            schema_version="1.0",
            recorded_at_utc=datetime.now(UTC),
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
