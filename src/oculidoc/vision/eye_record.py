"""Persistent records for manually labeled eye observations."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oculidoc.devices.contracts import (
    CameraFramePacket,
)
from oculidoc.vision.eye_observation import (
    EyeObservation,
)


@dataclass(frozen=True, slots=True)
class EyeObservationRecord:
    """One labeled camera frame and its eye observations."""

    schema_version: str
    recorded_at_utc: datetime
    camera_index: int
    backend_name: str | None
    frame_index: int
    image_width_px: int
    image_height_px: int
    raw_image_filename: str
    overlay_image_filename: str
    observations: tuple[EyeObservation, ...]

    def __post_init__(self) -> None:
        normalized_schema = self.schema_version.strip()

        if not normalized_schema:
            raise ValueError("schema_version cannot be empty.")

        object.__setattr__(
            self,
            "schema_version",
            normalized_schema,
        )

        if self.recorded_at_utc.tzinfo is None:
            raise ValueError("recorded_at_utc must be timezone-aware.")

        object.__setattr__(
            self,
            "recorded_at_utc",
            self.recorded_at_utc.astimezone(UTC),
        )

        if self.camera_index < 0:
            raise ValueError("camera_index cannot be negative.")

        if self.frame_index < 0:
            raise ValueError("frame_index cannot be negative.")

        if self.image_width_px <= 0 or self.image_height_px <= 0:
            raise ValueError("Image dimensions must be positive.")

        for field_name in (
            "raw_image_filename",
            "overlay_image_filename",
        ):
            normalized = getattr(
                self,
                field_name,
            ).strip()

            if not normalized:
                raise ValueError(f"{field_name} cannot be empty.")

            object.__setattr__(
                self,
                field_name,
                normalized,
            )

        if self.backend_name is not None:
            object.__setattr__(
                self,
                "backend_name",
                self.backend_name.strip() or None,
            )

        sides = [observation.side for observation in self.observations]

        if len(sides) != len(set(sides)):
            raise ValueError("Only one observation per eye side is allowed.")

    def to_dict(self) -> dict[str, Any]:
        """Convert the record into JSON-compatible values."""
        return {
            "schema_version": self.schema_version,
            "recorded_at_utc": (self.recorded_at_utc.isoformat()),
            "camera": {
                "index": self.camera_index,
                "backend": self.backend_name,
            },
            "frame": {
                "index": self.frame_index,
                "width_px": self.image_width_px,
                "height_px": self.image_height_px,
                "raw_image_filename": (self.raw_image_filename),
                "overlay_image_filename": (self.overlay_image_filename),
            },
            "observations": [
                {
                    "side": observation.side.value,
                    "opening_state": (observation.opening_state.value),
                    "source": observation.source.value,
                    "confidence": observation.confidence,
                    "note": observation.note,
                    "box": {
                        "x_px": observation.box.x_px,
                        "y_px": observation.box.y_px,
                        "width_px": (observation.box.width_px),
                        "height_px": (observation.box.height_px),
                    },
                }
                for observation in self.observations
            ],
        }


def build_eye_observation_record(
    *,
    packet: CameraFramePacket,
    camera_index: int,
    backend_name: str | None,
    raw_image_filename: str,
    overlay_image_filename: str,
    observations: tuple[EyeObservation, ...],
) -> EyeObservationRecord:
    """Create a record from a captured camera packet."""
    return EyeObservationRecord(
        schema_version="1.0",
        recorded_at_utc=(packet.timestamp.utc_timestamp),
        camera_index=camera_index,
        backend_name=backend_name,
        frame_index=packet.frame_index,
        image_width_px=packet.width_px,
        image_height_px=packet.height_px,
        raw_image_filename=raw_image_filename,
        overlay_image_filename=(overlay_image_filename),
        observations=observations,
    )


def raw_path_for_overlay(
    overlay_path: str | Path,
) -> Path:
    """Return the companion raw-image path."""
    path = Path(overlay_path)

    return path.with_name(f"{path.stem}_raw{path.suffix}")


def record_path_for_overlay(
    overlay_path: str | Path,
) -> Path:
    """Return the companion JSON record path."""
    return Path(overlay_path).with_suffix(".json")


def write_eye_observation_record(
    record: EyeObservationRecord,
    output_path: str | Path,
) -> Path:
    """Atomically write one UTF-8 JSON annotation record."""
    path = Path(output_path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(
            record.to_dict(),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)

    return path
