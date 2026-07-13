"""Patient management user-interface tests."""

from pytestqt.qtbot import QtBot

from oculidoc.application import RegisterPatientRequest
from oculidoc.config import Settings
from oculidoc.domain import ClinicalDiagnosis, Sex
from oculidoc.infrastructure.database import (
    initialize_database,
)
from oculidoc.ui.main_window import AdminMainWindow
from oculidoc.ui.patient_management import (
    PatientManagementDialog,
    RegisterPatientDialog,
)


def test_registration_dialog_creates_patient(
    qtbot: QtBot,
) -> None:
    runtime = initialize_database(":memory:")

    dialog = RegisterPatientDialog(runtime.patient_service)
    qtbot.addWidget(dialog)

    dialog.patient_code_edit.setText(" DOC-FORM-001 ")
    dialog.family_name_edit.setText("\u738b")

    sex_index = dialog.sex_combo.findData(Sex.FEMALE.value)
    dialog.sex_combo.setCurrentIndex(sex_index)

    diagnosis_index = dialog.diagnosis_combo.findData(ClinicalDiagnosis.MCS_PLUS.value)
    dialog.diagnosis_combo.setCurrentIndex(diagnosis_index)

    dialog.diagnosis_details_edit.setPlainText(" \u4e34\u5e8a\u8865\u5145 ")

    dialog._register_patient()

    assert dialog.registered_patient is not None
    assert dialog.registered_patient.patient_code == "DOC-FORM-001"
    assert dialog.registered_patient.family_name == "\u738b"
    assert dialog.registered_patient.sex is Sex.FEMALE
    assert dialog.registered_patient.clinical_diagnosis is ClinicalDiagnosis.MCS_PLUS
    assert dialog.registered_patient.diagnosis_details == "\u4e34\u5e8a\u8865\u5145"

    runtime.dispose()


def test_patient_management_lists_and_selects_patient(
    qtbot: QtBot,
) -> None:
    runtime = initialize_database(":memory:")

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-LIST-UI-001",
            family_name="\u674e",
            clinical_diagnosis=(ClinicalDiagnosis.UWS),
        )
    )

    dialog = PatientManagementDialog(runtime.patient_service)
    qtbot.addWidget(dialog)

    assert dialog.patient_list.count() == 1
    assert "DOC-LIST-UI-001" in dialog.patient_list.item(0).text()

    dialog.patient_list.setCurrentRow(0)
    dialog._select_patient()

    assert dialog.selected_patient is not None
    assert dialog.selected_patient.patient_id == patient.patient_id

    runtime.dispose()


def test_main_window_displays_current_patient(
    qtbot: QtBot,
    tmp_path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    runtime = initialize_database(settings.database_path)

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-CURRENT-001",
            family_name="\u5f20",
            clinical_diagnosis=(ClinicalDiagnosis.MCS_PLUS),
        )
    )

    window = AdminMainWindow(
        settings,
        runtime.patient_service,
    )
    qtbot.addWidget(window)

    window._set_current_patient(patient)

    assert "DOC-CURRENT-001" in window.patient_label.text()
    assert "\u5f20\u60a3\u8005" in window.patient_label.text()
    assert "MCS+" in window.patient_label.text()

    runtime.dispose()
