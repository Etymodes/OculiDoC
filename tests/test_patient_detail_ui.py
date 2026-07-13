"""Patient detail panel tests."""

from pytestqt.qtbot import QtBot

from oculidoc.application import RegisterPatientRequest
from oculidoc.domain import ClinicalDiagnosis
from oculidoc.infrastructure.database import initialize_database
from oculidoc.ui.patient_management import (
    PatientManagementDialog,
)


def test_patient_detail_panel_shows_record_and_audit(
    qtbot: QtBot,
) -> None:
    runtime = initialize_database(":memory:")

    runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-DETAIL-001",
            family_name="Detail",
            etiology="TBI",
            clinical_diagnosis=(ClinicalDiagnosis.MCS_PLUS),
            notes="Clinical note",
        )
    )

    dialog = PatientManagementDialog(runtime.patient_service)
    qtbot.addWidget(dialog)

    dialog.patient_list.setCurrentRow(0)
    dialog._refresh_detail()

    detail_text = dialog.patient_detail_label.text()

    assert "DOC-DETAIL-001" in detail_text
    assert "TBI" in detail_text
    assert "MCS+" in detail_text
    assert "Clinical note" in detail_text

    runtime.dispose()
