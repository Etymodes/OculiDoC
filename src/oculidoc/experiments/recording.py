"""Normalized gaze-event recording for OculiDoC tasks."""

from __future__ import annotations

import json
import os
import platform
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from oculidoc.devices.contracts import (
    EyeTrackerSample,
)


class AoiRole(StrEnum):
    """Semantic role of an area of interest."""

    CORRECT_OPTION = "correct_option"
    INCORRECT_OPTION = "incorrect_option"
    TARGET = "target"
    NON_OPTION = "non_option"
    OTHER = "other"


class RecorderState(StrEnum):
    """Lifecycle of a task-run recorder."""

    RECORDING = "recording"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class NormalizedAoi:
    """Area of interest in normalized screen coordinates."""

    aoi_id: str
    role: AoiRole
    left: float
    top: float
    right: float
    bottom: float
    label: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized_id = self.aoi_id.strip()

        if not normalized_id:
            raise ValueError("aoi_id cannot be empty.")

        if not (0.0 <= self.left < self.right <= 1.0):
            raise ValueError("AOI horizontal bounds must satisfy 0 <= left < right <= 1.")

        if not (0.0 <= self.top < self.bottom <= 1.0):
            raise ValueError("AOI vertical bounds must satisfy 0 <= top < bottom <= 1.")

        object.__setattr__(
            self,
            "aoi_id",
            normalized_id,
        )
        object.__setattr__(
            self,
            "metadata",
            dict(self.metadata),
        )

    @property
    def area(self) -> float:
        return (self.right - self.left) * (self.bottom - self.top)

    def contains(
        self,
        x_normalized: float,
        y_normalized: float,
    ) -> bool:
        return self.left <= x_normalized <= self.right and self.top <= y_normalized <= self.bottom

    def to_dict(self) -> dict[str, object]:
        return {
            "aoi_id": self.aoi_id,
            "role": self.role.value,
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "label": self.label,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ScreenContext:
    """Screen and task-window geometry for reconstruction."""

    screen_width_px: int
    screen_height_px: int
    window_x_px: int = 0
    window_y_px: int = 0
    window_width_px: int | None = None
    window_height_px: int | None = None
    device_pixel_ratio: float = 1.0
    dpi_x: float | None = None
    dpi_y: float | None = None
    orientation: str = "landscape"
    display_index: int = 0
    platform_system: str = field(default_factory=platform.system)
    platform_release: str = field(default_factory=platform.release)

    def __post_init__(self) -> None:
        if self.screen_width_px <= 0:
            raise ValueError("screen_width_px must be positive.")

        if self.screen_height_px <= 0:
            raise ValueError("screen_height_px must be positive.")

        window_width = (
            self.screen_width_px if self.window_width_px is None else self.window_width_px
        )
        window_height = (
            self.screen_height_px if self.window_height_px is None else self.window_height_px
        )

        if window_width <= 0:
            raise ValueError("window_width_px must be positive.")

        if window_height <= 0:
            raise ValueError("window_height_px must be positive.")

        if self.device_pixel_ratio <= 0:
            raise ValueError("device_pixel_ratio must be positive.")

        if self.display_index < 0:
            raise ValueError("display_index cannot be negative.")

        object.__setattr__(
            self,
            "window_width_px",
            window_width,
        )
        object.__setattr__(
            self,
            "window_height_px",
            window_height,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


GAZE_EVENT_SCHEMA = pa.schema(
    [
        ("patient_id", pa.string()),
        ("session_id", pa.string()),
        ("run_id", pa.string()),
        ("task_kind", pa.string()),
        ("question_id", pa.string()),
        ("phase", pa.string()),
        ("sequence", pa.int64()),
        ("monotonic_timestamp_ns", pa.int64()),
        ("utc_timestamp", pa.string()),
        ("source_timestamp_ns", pa.int64()),
        ("source_clock_id", pa.string()),
        ("gaze_x_normalized", pa.float64()),
        ("gaze_y_normalized", pa.float64()),
        ("device_gaze_valid", pa.bool_()),
        ("in_screen_bounds", pa.bool_()),
        ("analysis_valid", pa.bool_()),
        ("left_eye_valid", pa.bool_()),
        ("right_eye_valid", pa.bool_()),
        ("left_pupil_diameter_mm", pa.float64()),
        ("right_pupil_diameter_mm", pa.float64()),
        ("aoi_id", pa.string()),
        ("aoi_role", pa.string()),
        ("reference_aoi_id", pa.string()),
        ("reference_aoi_role", pa.string()),
        ("reference_aoi_left", pa.float64()),
        ("reference_aoi_top", pa.float64()),
        ("reference_aoi_right", pa.float64()),
        ("reference_aoi_bottom", pa.float64()),
        ("reference_aoi_label", pa.string()),
        ("duration_ms", pa.float64()),
    ]
)


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, StrEnum):
        return value.value

    raise TypeError(f"Object is not JSON serializable: {type(value).__name__}")


def _json_text(
    value: object,
    *,
    pretty: bool = True,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=pretty,
        default=_json_default,
    ) + ("\n" if pretty else "")


def _atomic_write_text(
    path: Path,
    content: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(handle)
    temporary_path = Path(temporary_name)

    try:
        temporary_path.write_text(
            content,
            encoding="utf-8",
        )
        os.replace(
            temporary_path,
            path,
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_write_json(
    path: Path,
    value: object,
) -> None:
    _atomic_write_text(
        path,
        _json_text(value),
    )


def _atomic_write_parquet(
    path: Path,
    rows: Sequence[Mapping[str, object]],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(handle)
    temporary_path = Path(temporary_name)

    try:
        table = pa.Table.from_pylist(
            [dict(row) for row in rows],
            schema=GAZE_EVENT_SCHEMA,
        )
        pq.write_table(
            table,
            temporary_path,
            compression="zstd",
        )
        os.replace(
            temporary_path,
            path,
        )
    finally:
        temporary_path.unlink(missing_ok=True)


class TaskRunRecorder:
    """Record one gaze-driven task run."""

    schema_version = "1.0"

    def __init__(
        self,
        *,
        session_directory: str | Path,
        patient_id: str,
        session_id: str,
        task_kind: str,
        task_config: Mapping[str, object],
        screen_context: ScreenContext,
        run_id: str | None = None,
        task_started_monotonic_ns: int | None = None,
        maximum_sample_gap_ms: float = 250.0,
    ) -> None:
        self.patient_id = self._required_text(
            patient_id,
            "patient_id",
        )
        self.session_id = self._required_text(
            session_id,
            "session_id",
        )
        self.task_kind = self._required_text(
            task_kind,
            "task_kind",
        )
        self.run_id = (
            self._required_text(
                run_id,
                "run_id",
            )
            if run_id is not None
            else str(uuid4())
        )

        if task_started_monotonic_ns is not None and task_started_monotonic_ns < 0:
            raise ValueError("task_started_monotonic_ns cannot be negative.")

        if maximum_sample_gap_ms <= 0:
            raise ValueError("maximum_sample_gap_ms must be positive.")

        self.session_directory = Path(session_directory).expanduser().resolve()
        self.run_directory = self.session_directory / "tasks" / self.run_id

        self.run_directory.mkdir(
            parents=True,
            exist_ok=False,
        )

        self.task_config = dict(task_config)
        self.screen_context = screen_context
        self.task_started_monotonic_ns = task_started_monotonic_ns
        self.maximum_sample_gap_ms = float(maximum_sample_gap_ms)

        self.state = RecorderState.RECORDING
        self.created_at_utc = datetime.now(UTC)

        self._records: list[dict[str, object]] = []
        self._events: list[dict[str, object]] = []
        self._question_layouts: dict[str, dict[str, object]] = {}
        self._question_aois: dict[str, tuple[NormalizedAoi, ...]] = {}

        self._write_initial_files()

    @staticmethod
    def _required_text(
        value: str,
        field_name: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")

        return normalized

    @property
    def gaze_events_path(self) -> Path:
        return self.run_directory / "gaze_events.parquet"

    @property
    def task_events_path(self) -> Path:
        return self.run_directory / "task_events.jsonl"

    @property
    def result_path(self) -> Path:
        return self.run_directory / "task_result.json"

    def _require_recording(self) -> None:
        if self.state is not RecorderState.RECORDING:
            raise RuntimeError("The task recorder is already finished.")

    def _manifest(
        self,
        *,
        status: str,
        ended_at_utc: datetime | None = None,
        end_reason: str | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "patient_id": self.patient_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "task_kind": self.task_kind,
            "status": status,
            "created_at_utc": self.created_at_utc,
            "ended_at_utc": ended_at_utc,
            "end_reason": end_reason,
        }

    def _write_initial_files(self) -> None:
        _atomic_write_json(
            self.run_directory / "run_manifest.json",
            self._manifest(status=RecorderState.RECORDING.value),
        )
        _atomic_write_json(
            self.run_directory / "task_config.json",
            self.task_config,
        )
        _atomic_write_json(
            self.run_directory / "screen_context.json",
            self.screen_context.to_dict(),
        )
        _atomic_write_json(
            self.run_directory / "question_layouts.json",
            {
                "schema_version": (self.schema_version),
                "questions": [],
            },
        )

    def register_question(
        self,
        question_id: str,
        *,
        aois: Sequence[NormalizedAoi],
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        self._require_recording()

        normalized_id = self._required_text(
            question_id,
            "question_id",
        )
        normalized_aois = tuple(aois)

        aoi_ids = [aoi.aoi_id for aoi in normalized_aois]

        if len(aoi_ids) != len(set(aoi_ids)):
            raise ValueError("AOI identifiers must be unique within a question.")

        layout = {
            "question_id": normalized_id,
            "aois": [aoi.to_dict() for aoi in normalized_aois],
            "metadata": dict(metadata or {}),
        }

        existing = self._question_layouts.get(normalized_id)

        if existing is not None and existing != layout:
            raise ValueError("The question layout is already registered with different data.")

        self._question_layouts[normalized_id] = layout
        self._question_aois[normalized_id] = normalized_aois

        self._write_question_layouts()

    def _write_question_layouts(self) -> None:
        _atomic_write_json(
            self.run_directory / "question_layouts.json",
            {
                "schema_version": (self.schema_version),
                "questions": list(self._question_layouts.values()),
            },
        )

    def record_event(
        self,
        event_type: str,
        *,
        monotonic_timestamp_ns: int | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        self._require_recording()

        normalized_type = self._required_text(
            event_type,
            "event_type",
        )

        if monotonic_timestamp_ns is not None and monotonic_timestamp_ns < 0:
            raise ValueError("monotonic_timestamp_ns cannot be negative.")

        self._events.append(
            {
                "event_type": normalized_type,
                "utc_timestamp": datetime.now(UTC),
                "monotonic_timestamp_ns": (monotonic_timestamp_ns),
                "payload": dict(payload or {}),
            }
        )

    def _resolve_aois(
        self,
        *,
        question_id: str | None,
        aois: Sequence[NormalizedAoi] | None,
    ) -> tuple[NormalizedAoi, ...]:
        if aois is not None:
            return tuple(aois)

        if question_id is None:
            return ()

        return self._question_aois.get(
            question_id,
            (),
        )

    @staticmethod
    def _classify_aoi(
        x_normalized: float,
        y_normalized: float,
        aois: Sequence[NormalizedAoi],
    ) -> tuple[str | None, AoiRole]:
        matching = [
            aoi
            for aoi in aois
            if aoi.contains(
                x_normalized,
                y_normalized,
            )
        ]

        if not matching:
            return (
                None,
                AoiRole.NON_OPTION,
            )

        selected = min(
            matching,
            key=lambda aoi: aoi.area,
        )

        return (
            selected.aoi_id,
            selected.role,
        )

    def record_sample(
        self,
        sample: EyeTrackerSample,
        *,
        question_id: str | None = None,
        phase: str | None = None,
        aois: Sequence[NormalizedAoi] | None = None,
        reference_aoi: NormalizedAoi | None = None,
    ) -> None:
        self._require_recording()

        timestamp = sample.timestamp
        monotonic_timestamp_ns = timestamp.monotonic_timestamp_ns

        if self._records:
            previous = self._records[-1]
            previous_timestamp = int(previous["monotonic_timestamp_ns"])
            delta_ns = monotonic_timestamp_ns - previous_timestamp

            if delta_ns > 0:
                previous["duration_ms"] = min(
                    delta_ns / 1_000_000.0,
                    self.maximum_sample_gap_ms,
                )

        if self.task_started_monotonic_ns is None:
            self.task_started_monotonic_ns = monotonic_timestamp_ns

        gaze_x = float(sample.gaze_x_normalized) if sample.gaze_x_normalized is not None else None
        gaze_y = float(sample.gaze_y_normalized) if sample.gaze_y_normalized is not None else None

        device_gaze_valid = bool(sample.gaze_valid)
        in_screen_bounds = (
            gaze_x is not None
            and gaze_y is not None
            and 0.0 <= gaze_x <= 1.0
            and 0.0 <= gaze_y <= 1.0
        )
        analysis_valid = device_gaze_valid and in_screen_bounds

        aoi_id: str | None = None
        aoi_role: AoiRole | None = None

        if analysis_valid:
            resolved_aois = self._resolve_aois(
                question_id=question_id,
                aois=aois,
            )
            aoi_id, aoi_role = self._classify_aoi(
                gaze_x,
                gaze_y,
                resolved_aois,
            )

        reference_aoi_id = reference_aoi.aoi_id if reference_aoi is not None else None
        reference_aoi_role = reference_aoi.role.value if reference_aoi is not None else None
        reference_aoi_left = reference_aoi.left if reference_aoi is not None else None
        reference_aoi_top = reference_aoi.top if reference_aoi is not None else None
        reference_aoi_right = reference_aoi.right if reference_aoi is not None else None
        reference_aoi_bottom = reference_aoi.bottom if reference_aoi is not None else None
        reference_aoi_label = reference_aoi.label if reference_aoi is not None else None

        self._records.append(
            {
                "patient_id": self.patient_id,
                "session_id": self.session_id,
                "run_id": self.run_id,
                "task_kind": self.task_kind,
                "question_id": question_id,
                "phase": phase,
                "sequence": timestamp.sequence,
                "monotonic_timestamp_ns": (monotonic_timestamp_ns),
                "utc_timestamp": (timestamp.utc_timestamp.isoformat()),
                "source_timestamp_ns": (timestamp.source_timestamp_ns),
                "source_clock_id": (timestamp.source_clock_id),
                "gaze_x_normalized": gaze_x,
                "gaze_y_normalized": gaze_y,
                "device_gaze_valid": (device_gaze_valid),
                "in_screen_bounds": (in_screen_bounds),
                "analysis_valid": (analysis_valid),
                "left_eye_valid": (sample.left_eye_valid),
                "right_eye_valid": (sample.right_eye_valid),
                "left_pupil_diameter_mm": (sample.left_pupil_diameter_mm),
                "right_pupil_diameter_mm": (sample.right_pupil_diameter_mm),
                "aoi_id": aoi_id,
                "aoi_role": (aoi_role.value if aoi_role is not None else None),
                "reference_aoi_id": (reference_aoi_id),
                "reference_aoi_role": (reference_aoi_role),
                "reference_aoi_left": (reference_aoi_left),
                "reference_aoi_top": (reference_aoi_top),
                "reference_aoi_right": (reference_aoi_right),
                "reference_aoi_bottom": (reference_aoi_bottom),
                "reference_aoi_label": (reference_aoi_label),
                "duration_ms": 0.0,
            }
        )

    def _build_summary(
        self,
    ) -> dict[str, object]:
        sample_count = len(self._records)
        valid_rows = [row for row in self._records if bool(row["analysis_valid"])]
        valid_count = len(valid_rows)
        invalid_count = sample_count - valid_count

        dwell_by_role: defaultdict[
            str,
            float,
        ] = defaultdict(float)
        dwell_by_aoi: defaultdict[
            str,
            float,
        ] = defaultdict(float)

        for row in valid_rows:
            duration_ms = float(row["duration_ms"])
            role = str(row["aoi_role"] or AoiRole.NON_OPTION.value)
            dwell_by_role[role] += duration_ms

            if row["aoi_id"] is not None:
                dwell_by_aoi[str(row["aoi_id"])] += duration_ms

        role_switch_count = 0
        previous_role: str | None = None
        previous_question: str | None = None

        for row in valid_rows:
            question_id = str(row["question_id"]) if row["question_id"] is not None else None
            role = str(row["aoi_role"] or AoiRole.NON_OPTION.value)

            if question_id != previous_question:
                previous_role = None
                previous_question = question_id

            if previous_role is not None and role != previous_role:
                role_switch_count += 1

            previous_role = role

        first_valid_reaction_ms: float | None = None

        if valid_rows and self.task_started_monotonic_ns is not None:
            first_valid_reaction_ms = max(
                0.0,
                (int(valid_rows[0]["monotonic_timestamp_ns"]) - self.task_started_monotonic_ns)
                / 1_000_000.0,
            )

        recording_duration_ms = 0.0

        if len(self._records) >= 2:
            recording_duration_ms = max(
                0.0,
                (
                    int(self._records[-1]["monotonic_timestamp_ns"])
                    - int(self._records[0]["monotonic_timestamp_ns"])
                )
                / 1_000_000.0,
            )

        question_ids = {
            str(row["question_id"]) for row in self._records if row["question_id"] is not None
        }

        return {
            "sample_count": sample_count,
            "valid_sample_count": valid_count,
            "invalid_sample_count": (invalid_count),
            "valid_sample_ratio": (valid_count / sample_count if sample_count else 0.0),
            "recording_duration_ms": (recording_duration_ms),
            "first_valid_reaction_ms": (first_valid_reaction_ms),
            "question_count": len(question_ids),
            "role_switch_count": (role_switch_count),
            "dwell_by_role_ms": {
                key: round(value, 3) for key, value in sorted(dwell_by_role.items())
            },
            "dwell_by_aoi_ms": {
                key: round(value, 3) for key, value in sorted(dwell_by_aoi.items())
            },
        }

    def _write_events(self) -> None:
        content = "".join(
            _json_text(
                event,
                pretty=False,
            )
            + "\n"
            for event in self._events
        )
        _atomic_write_text(
            self.task_events_path,
            content,
        )

    def finish(
        self,
        *,
        reason: str,
        result: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        self._require_recording()

        normalized_reason = self._required_text(
            reason,
            "reason",
        )
        ended_at_utc = datetime.now(UTC)

        _atomic_write_parquet(
            self.gaze_events_path,
            self._records,
        )
        self._write_events()
        self._write_question_layouts()

        summary = self._build_summary()

        result_document = {
            "schema_version": self.schema_version,
            "patient_id": self.patient_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "task_kind": self.task_kind,
            "created_at_utc": (self.created_at_utc),
            "ended_at_utc": ended_at_utc,
            "end_reason": normalized_reason,
            "summary": summary,
            "result": dict(result or {}),
        }

        _atomic_write_json(
            self.result_path,
            result_document,
        )
        _atomic_write_json(
            self.run_directory / "run_manifest.json",
            self._manifest(
                status=(RecorderState.FINISHED.value),
                ended_at_utc=ended_at_utc,
                end_reason=normalized_reason,
            ),
        )

        self.state = RecorderState.FINISHED

        return summary

    def abort(
        self,
        *,
        reason: str = "aborted",
        result: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return self.finish(
            reason=reason,
            result=result,
        )
