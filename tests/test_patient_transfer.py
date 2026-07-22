from __future__ import annotations

from pathlib import Path

from oculidoc.application import RegisterPatientRequest
from oculidoc.application.patient_transfer import (
    import_patient_records,
    read_patient_transfer,
    write_patient_transfer,
)
from oculidoc.infrastructure.database import initialize_database


def test_patient_transfer_round_trip_skips_existing_codes(tmp_path: Path) -> None:
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
                notes="仅基本资料",
            )
        )
        source_runtime.patient_service.deactivate_patient(patient.patient_id)
        path = write_patient_transfer(
            tmp_path / "patients.json",
            source_runtime.patient_service.list_patients(),
        )
        records = read_patient_transfer(path)

        first = import_patient_records(destination_runtime.patient_service, records)
        second = import_patient_records(destination_runtime.patient_service, records)

        assert first.imported_count == 1
        assert first.skipped_duplicate_count == 0
        assert second.imported_count == 0
        assert second.skipped_duplicate_count == 1
        imported = destination_runtime.patient_service.list_patients()[0]
        assert imported.patient_code == "DOC-TRANSFER-001"
        assert imported.is_active is False
    finally:
        source_runtime.dispose()
        destination_runtime.dispose()
