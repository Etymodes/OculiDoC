from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from oculidoc.application import (
    CreateExperimentSessionRequest,
    RegisterPatientRequest,
    RegisterSessionArtifactRequest,
)
from oculidoc.application.patient_transfer import (
    import_patient_records,
    patient_transfer_document,
    read_patient_transfer,
    write_patient_transfer,
)
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
    SessionArtifactKind,
)
from oculidoc.infrastructure.database import initialize_database


def _completed_session(runtime, patient_id):
    session = runtime.experiment_session_service.create_session(
        CreateExperimentSessionRequest(
            patient_id=patient_id,
            module_id="instruction_fixation",
        )
    )
    runtime.experiment_session_service.start_session(
        session.session_id,
        monotonic_timestamp_ns=123_456_789,
    )
    return runtime.experiment_session_service.complete_session(session.session_id)


def test_complete_patient_transfer_round_trip_skips_existing_patient(tmp_path: Path) -> None:
    source_runtime = initialize_database(
        tmp_path / "source.sqlite3",
        data_root=tmp_path / "source-data",
    )
    destination_runtime = initialize_database(
        tmp_path / "destination.sqlite3",
        data_root=tmp_path / "destination-data",
    )

    try:
        patient = source_runtime.patient_service.register_patient(
            RegisterPatientRequest(
                patient_code="DOC-TRANSFER-001",
                family_name="转入",
                etiology="TBI",
                notes="完整资料",
            )
        )
        session = _completed_session(source_runtime, patient.patient_id)
        session_directory = source_runtime.experiment_session_service.resolve_session_directory(
            session.session_id
        )
        gaze_bytes = bytes(range(256)) * 220
        gaze_path = session_directory / "raw" / "gaze.parquet"
        gaze_path.parent.mkdir(parents=True)
        gaze_path.write_bytes(gaze_bytes)
        (session_directory / "notes.json").write_text(
            json.dumps({"结论": "保留"}, ensure_ascii=False),
            encoding="utf-8",
        )
        source_runtime.experiment_session_service.register_artifact(
            RegisterSessionArtifactRequest(
                session_id=session.session_id,
                kind=SessionArtifactKind.GAZE,
                relative_path="raw/gaze.parquet",
                source="sensor",
                size_bytes=len(gaze_bytes),
            )
        )
        source_runtime.patient_service.deactivate_patient(patient.patient_id)

        transfer_path = write_patient_transfer(
            tmp_path / "complete.csv",
            source_runtime.patient_service.list_patients(),
            source_runtime.experiment_session_service,
        )
        assert transfer_path.read_bytes().startswith(b"\xef\xbb\xbf")

        with read_patient_transfer(transfer_path) as bundle:
            assert len(bundle.patients) == 1
            assert len(bundle.sessions) == 1
            assert {file.relative_path for file in bundle.files} == {
                "notes.json",
                "raw/gaze.parquet",
                "session.json",
            }

            first = import_patient_records(
                destination_runtime.patient_service,
                bundle,
                destination_runtime.experiment_session_service,
            )
            second = import_patient_records(
                destination_runtime.patient_service,
                bundle,
                destination_runtime.experiment_session_service,
            )

        assert first.imported_count == 1
        assert first.imported_session_count == 1
        assert first.imported_file_count == 3
        assert second.imported_count == 0
        assert second.imported_session_count == 0
        assert second.skipped_duplicate_count == 1

        imported_patient = destination_runtime.patient_service.list_patients()[0]
        assert imported_patient.patient_id == patient.patient_id
        assert imported_patient.is_active is False
        imported_session = destination_runtime.experiment_session_service.get_session(
            session.session_id
        )
        assert imported_session.status is ExperimentSessionStatus.COMPLETED
        assert imported_session.clock_origin_monotonic_ns == 123_456_789
        imported_directory = (
            destination_runtime.experiment_session_service.resolve_session_directory(
                session.session_id
            )
        )
        assert (imported_directory / "raw" / "gaze.parquet").read_bytes() == gaze_bytes
        assert json.loads((imported_directory / "notes.json").read_text(encoding="utf-8")) == {
            "结论": "保留"
        }
    finally:
        source_runtime.dispose()
        destination_runtime.dispose()


def test_csv_is_fully_validated_before_import(tmp_path: Path) -> None:
    source_runtime = initialize_database(
        tmp_path / "source.sqlite3",
        data_root=tmp_path / "source-data",
    )
    destination_runtime = initialize_database(
        tmp_path / "destination.sqlite3",
        data_root=tmp_path / "destination-data",
    )
    try:
        patient = source_runtime.patient_service.register_patient(
            RegisterPatientRequest(patient_code="DOC-CORRUPT-001", family_name="校验")
        )
        session = _completed_session(source_runtime, patient.patient_id)
        directory = source_runtime.experiment_session_service.resolve_session_directory(
            session.session_id
        )
        (directory / "payload.bin").write_bytes(b"experiment-data")
        path = write_patient_transfer(
            tmp_path / "corrupt.csv",
            [patient],
            source_runtime.experiment_session_service,
        )

        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            assert reader.fieldnames is not None
            fieldnames = tuple(reader.fieldnames)
        file_row = next(row for row in rows if row["record_type"] == "file")
        file_row["payload"] = "AAAA" + file_row["payload"][4:]
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        with pytest.raises(ValueError, match="校验失败"):
            read_patient_transfer(path)
        assert destination_runtime.patient_service.list_patients() == []
    finally:
        source_runtime.dispose()
        destination_runtime.dispose()


def test_active_experiment_blocks_export(tmp_path: Path) -> None:
    runtime = initialize_database(tmp_path / "source.sqlite3", data_root=tmp_path / "data")
    try:
        patient = runtime.patient_service.register_patient(
            RegisterPatientRequest(patient_code="DOC-ACTIVE-001", family_name="运行中")
        )
        runtime.experiment_session_service.create_session(
            CreateExperimentSessionRequest(
                patient_id=patient.patient_id,
                module_id="tracking_ball",
            )
        )

        with pytest.raises(RuntimeError, match="正在进行"):
            write_patient_transfer(
                tmp_path / "blocked.csv",
                [patient],
                runtime.experiment_session_service,
            )
        assert not (tmp_path / "blocked.csv").exists()
    finally:
        runtime.dispose()


def test_import_destination_is_validated_before_patient_is_written(tmp_path: Path) -> None:
    source_runtime = initialize_database(
        tmp_path / "source.sqlite3",
        data_root=tmp_path / "source-data",
    )
    destination_root = tmp_path / "destination-data"
    destination_runtime = initialize_database(
        tmp_path / "destination.sqlite3",
        data_root=destination_root,
    )
    try:
        patient = source_runtime.patient_service.register_patient(
            RegisterPatientRequest(patient_code="DOC-CONFLICT-001", family_name="冲突")
        )
        session = _completed_session(source_runtime, patient.patient_id)
        path = write_patient_transfer(
            tmp_path / "conflict.csv",
            [patient],
            source_runtime.experiment_session_service,
        )
        conflicting_directory = destination_root.joinpath(*Path(session.data_directory).parts)
        conflicting_directory.mkdir(parents=True)
        (conflicting_directory / "existing.bin").write_bytes(b"keep")

        with read_patient_transfer(path) as bundle:
            with pytest.raises(FileExistsError, match="already contains data"):
                import_patient_records(
                    destination_runtime.patient_service,
                    bundle,
                    destination_runtime.experiment_session_service,
                )

        assert destination_runtime.patient_service.list_patients() == []
        assert (conflicting_directory / "existing.bin").read_bytes() == b"keep"
    finally:
        source_runtime.dispose()
        destination_runtime.dispose()


def test_legacy_demographic_json_can_still_be_imported(tmp_path: Path) -> None:
    source_runtime = initialize_database(":memory:")
    destination_runtime = initialize_database(":memory:")
    try:
        patient = source_runtime.patient_service.register_patient(
            RegisterPatientRequest(patient_code="DOC-LEGACY-001", family_name="旧版")
        )
        path = tmp_path / "legacy.json"
        path.write_text(
            json.dumps(patient_transfer_document([patient]), ensure_ascii=False),
            encoding="utf-8",
        )

        with read_patient_transfer(path) as bundle:
            summary = import_patient_records(destination_runtime.patient_service, bundle)

        assert summary.imported_count == 1
        assert summary.imported_session_count == 0
    finally:
        source_runtime.dispose()
        destination_runtime.dispose()
