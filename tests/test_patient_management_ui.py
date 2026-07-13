"""Patient management user-interface tests."""

from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from oculidoc.application import (
    RegisterPatientRequest,
    UpdatePatientRequest,
)
from oculidoc.config import Settings
from oculidoc.domain import ClinicalDiagnosis, Sex
from oculidoc.infrastructure.database import initialize_database
from oculidoc.ui.main_window import AdminMainWindow
from oculidoc.ui.patient_management import (
    EditPatientDialog,
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

    diagnosis_index = dialog.diagnosis_combo.findData(ClinicalDiagnosis.MCS_PLUS.value)
    dialog.diagnosis_combo.setCurrentIndex(diagnosis_index)

    dialog._register_patient()

    assert dialog.registered_patient is not None
    assert dialog.registered_patient.patient_code == "DOC-FORM-001"

    runtime.dispose()


def test_edit_dialog_updates_existing_patient(
    qtbot: QtBot,
) -> None:
    runtime = initialize_database(":memory:")

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-EDIT-001",
            family_name="\u65e7",
        )
    )

    dialog = EditPatientDialog(
        runtime.patient_service,
        patient,
    )
    qtbot.addWidget(dialog)

    assert dialog.patient_code_edit.text() == "DOC-EDIT-001"
    assert dialog.family_name_edit.text() == "\u65e7"

    dialog.patient_code_edit.setText("DOC-EDIT-002")
    dialog.family_name_edit.setText("\u65b0")

    sex_index = dialog.sex_combo.findData(Sex.FEMALE.value)
    dialog.sex_combo.setCurrentIndex(sex_index)

    dialog._register_patient()

    assert dialog.edited_patient is not None
    assert dialog.edited_patient.patient_id == patient.patient_id
    assert dialog.edited_patient.patient_code == "DOC-EDIT-002"
    assert dialog.edited_patient.family_name == "\u65b0"

    runtime.dispose()


def test_inactive_patient_cannot_be_selected(
    qtbot: QtBot,
    monkeypatch,
) -> None:
    runtime = initialize_database(":memory:")

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-INACTIVE-001",
            family_name="\u505c\u7528",
        )
    )
    runtime.patient_service.deactivate_patient(patient.patient_id)

    dialog = PatientManagementDialog(runtime.patient_service)
    qtbot.addWidget(dialog)
    dialog.patient_list.setCurrentRow(0)

    messages = []

    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append(args),
    )

    dialog._select_patient()

    assert dialog.selected_patient is None
    assert messages

    runtime.dispose()


def test_main_window_refreshes_and_clears_current_patient(
    qtbot: QtBot,
    tmp_path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    runtime = initialize_database(settings.database_path)

    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-CURRENT-001",
            family_name="\u65e7",
            clinical_diagnosis=(ClinicalDiagnosis.UWS),
        )
    )

    window = AdminMainWindow(
        settings,
        runtime.patient_service,
    )
    qtbot.addWidget(window)
    window._set_current_patient(patient)

    runtime.patient_service.update_patient(
        UpdatePatientRequest(
            patient_id=patient.patient_id,
            patient_code="DOC-CURRENT-002",
            family_name="\u65b0",
            clinical_diagnosis=(ClinicalDiagnosis.MCS_PLUS),
        )
    )

    window._reload_current_patient()
    window._refresh_patient_summary()

    assert "DOC-CURRENT-002" in window.patient_label.text()
    assert "\u65b0\u60a3\u8005" in window.patient_label.text()
    assert "MCS+" in window.patient_label.text()

    runtime.patient_service.deactivate_patient(patient.patient_id)
    window._reload_current_patient()
    window._refresh_patient_summary()

    assert window.current_patient is None
    assert "\u5c1a\u672a\u9009\u62e9" in window.patient_label.text()

    runtime.dispose()
