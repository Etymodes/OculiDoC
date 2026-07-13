"""Patient registration and selection dialogs."""

from datetime import date
from uuid import UUID

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from oculidoc.application import (
    DuplicatePatientCodeError,
    PatientService,
    RegisterPatientRequest,
)
from oculidoc.domain import ClinicalDiagnosis, Patient, Sex

SEX_LABELS = {
    Sex.UNKNOWN: "\u672a\u77e5",
    Sex.MALE: "\u7537",
    Sex.FEMALE: "\u5973",
}

DIAGNOSIS_LABELS = {
    ClinicalDiagnosis.UNKNOWN: "\u672a\u77e5",
    ClinicalDiagnosis.COMA: "Coma",
    ClinicalDiagnosis.UWS: "UWS",
    ClinicalDiagnosis.MCS_MINUS: "MCS-",
    ClinicalDiagnosis.MCS_PLUS: "MCS+",
    ClinicalDiagnosis.EMCS: "EMCS",
    ClinicalDiagnosis.OTHER: "\u5176\u4ed6",
}


def diagnosis_display_name(
    diagnosis: ClinicalDiagnosis,
) -> str:
    """Return the administrator-facing diagnosis label."""
    return DIAGNOSIS_LABELS[diagnosis]


def _qdate_to_date(value: QDate) -> date:
    """Convert a Qt date into a Python date."""
    return date(
        value.year(),
        value.month(),
        value.day(),
    )


class RegisterPatientDialog(QDialog):
    """Collect the minimum patient registration information."""

    def __init__(
        self,
        patient_service: PatientService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.patient_service = patient_service
        self.registered_patient: Patient | None = None

        self.setWindowTitle("\u767b\u8bb0\u65b0\u60a3\u8005")
        self.setMinimumWidth(540)

        self.patient_code_edit = QLineEdit()
        self.patient_code_edit.setObjectName("patientCodeEdit")
        self.patient_code_edit.setPlaceholderText("\u4f8b\uff1aDOC-001")

        self.family_name_edit = QLineEdit()
        self.family_name_edit.setObjectName("familyNameEdit")
        self.family_name_edit.setPlaceholderText("\u59d3\u6c0f\u6216\u663e\u793a\u79f0\u547c")

        self.sex_combo = QComboBox()
        self.sex_combo.setObjectName("sexCombo")

        for sex in Sex:
            self.sex_combo.addItem(
                SEX_LABELS[sex],
                sex.value,
            )

        self.birth_unknown_checkbox = QCheckBox("\u51fa\u751f\u65e5\u671f\u672a\u77e5")
        self.birth_unknown_checkbox.setObjectName("birthUnknownCheckBox")
        self.birth_unknown_checkbox.setChecked(True)

        self.birth_date_edit = QDateEdit()
        self.birth_date_edit.setObjectName("birthDateEdit")
        self.birth_date_edit.setCalendarPopup(True)
        self.birth_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.birth_date_edit.setDate(QDate.currentDate())
        self.birth_date_edit.setDisabled(True)

        self.birth_unknown_checkbox.toggled.connect(self.birth_date_edit.setDisabled)

        birth_container = QWidget()
        birth_layout = QHBoxLayout(birth_container)
        birth_layout.setContentsMargins(0, 0, 0, 0)
        birth_layout.addWidget(self.birth_date_edit)
        birth_layout.addWidget(self.birth_unknown_checkbox)

        self.etiology_edit = QLineEdit()
        self.etiology_edit.setObjectName("etiologyEdit")
        self.etiology_edit.setPlaceholderText(
            "\u4f8b\uff1aTBI\u3001\u7f3a\u6c27\u7f3a\u8840\u6027\u635f\u4f24"
        )

        self.diagnosis_combo = QComboBox()
        self.diagnosis_combo.setObjectName("diagnosisCombo")

        for diagnosis in ClinicalDiagnosis:
            self.diagnosis_combo.addItem(
                diagnosis_display_name(diagnosis),
                diagnosis.value,
            )

        self.diagnosis_details_edit = QTextEdit()
        self.diagnosis_details_edit.setObjectName("diagnosisDetailsEdit")
        self.diagnosis_details_edit.setMaximumHeight(72)

        self.enrollment_date_edit = QDateEdit()
        self.enrollment_date_edit.setObjectName("enrollmentDateEdit")
        self.enrollment_date_edit.setCalendarPopup(True)
        self.enrollment_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.enrollment_date_edit.setDate(QDate.currentDate())

        self.notes_edit = QTextEdit()
        self.notes_edit.setObjectName("notesEdit")
        self.notes_edit.setMaximumHeight(90)

        form = QFormLayout()
        form.addRow(
            "\u533b\u9662\u533f\u540d\u7f16\u53f7\uff1a",
            self.patient_code_edit,
        )
        form.addRow(
            "\u59d3\u6c0f/\u663e\u793a\u79f0\u547c\uff1a",
            self.family_name_edit,
        )
        form.addRow(
            "\u6027\u522b\uff1a",
            self.sex_combo,
        )
        form.addRow(
            "\u51fa\u751f\u65e5\u671f\uff1a",
            birth_container,
        )
        form.addRow(
            "\u75c5\u56e0\uff1a",
            self.etiology_edit,
        )
        form.addRow(
            "\u6807\u51c6\u4e34\u5e8a\u8bca\u65ad\uff1a",
            self.diagnosis_combo,
        )
        form.addRow(
            "\u8bca\u65ad\u8865\u5145\uff1a",
            self.diagnosis_details_edit,
        )
        form.addRow(
            "\u5165\u7ec4\u65e5\u671f\uff1a",
            self.enrollment_date_edit,
        )
        form.addRow(
            "\u5907\u6ce8\uff1a",
            self.notes_edit,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.setObjectName("registrationButtonBox")
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("\u4fdd\u5b58")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("\u53d6\u6d88")

        buttons.accepted.connect(self._register_patient)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

    def _register_patient(self) -> None:
        """Validate the form and register one patient."""
        patient_code = self.patient_code_edit.text().strip()
        family_name = self.family_name_edit.text().strip()

        if not patient_code or not family_name:
            QMessageBox.warning(
                self,
                "\u4fe1\u606f\u4e0d\u5b8c\u6574",
                "\u60a3\u8005\u7f16\u53f7\u548c"
                "\u59d3\u6c0f/\u663e\u793a\u79f0\u547c"
                "\u4e0d\u80fd\u4e3a\u7a7a\u3002",
            )
            return

        date_of_birth = None

        if not self.birth_unknown_checkbox.isChecked():
            date_of_birth = _qdate_to_date(self.birth_date_edit.date())

        request = RegisterPatientRequest(
            patient_code=patient_code,
            family_name=family_name,
            sex=Sex(self.sex_combo.currentData()),
            date_of_birth=date_of_birth,
            etiology=self.etiology_edit.text(),
            clinical_diagnosis=ClinicalDiagnosis(self.diagnosis_combo.currentData()),
            diagnosis_details=(self.diagnosis_details_edit.toPlainText()),
            enrollment_date=_qdate_to_date(self.enrollment_date_edit.date()),
            notes=self.notes_edit.toPlainText(),
        )

        try:
            patient = self.patient_service.register_patient(request)
        except DuplicatePatientCodeError:
            QMessageBox.warning(
                self,
                "\u7f16\u53f7\u91cd\u590d",
                "\u8be5\u60a3\u8005\u7f16\u53f7\u5df2\u7ecf\u5b58\u5728\u3002",
            )
            return
        except ValueError as error:
            QMessageBox.warning(
                self,
                "\u767b\u8bb0\u5931\u8d25",
                str(error),
            )
            return

        self.registered_patient = patient
        self.accept()


class PatientManagementDialog(QDialog):
    """List patients, register patients, and choose one patient."""

    def __init__(
        self,
        patient_service: PatientService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.patient_service = patient_service
        self.selected_patient: Patient | None = None

        self.setWindowTitle("\u60a3\u8005\u7ba1\u7406")
        self.resize(720, 480)

        title = QLabel("\u5df2\u767b\u8bb0\u60a3\u8005")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")

        self.patient_list = QListWidget()
        self.patient_list.setObjectName("patientList")
        self.patient_list.itemDoubleClicked.connect(self._select_patient)

        self.new_button = QPushButton("\u767b\u8bb0\u65b0\u60a3\u8005")
        self.new_button.setObjectName("newPatientButton")
        self.new_button.clicked.connect(self._open_registration_dialog)

        self.select_button = QPushButton("\u9009\u4e3a\u5f53\u524d\u60a3\u8005")
        self.select_button.setObjectName("selectPatientButton")
        self.select_button.clicked.connect(self._select_patient)

        close_button = QPushButton("\u5173\u95ed")
        close_button.clicked.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(self.new_button)
        actions.addStretch(1)
        actions.addWidget(self.select_button)
        actions.addWidget(close_button)

        root = QVBoxLayout(self)
        root.addWidget(title)
        root.addWidget(self.patient_list, 1)
        root.addLayout(actions)

        self.refresh_patients()

    def refresh_patients(self) -> None:
        """Reload the visible patient list."""
        self.patient_list.clear()

        for patient in self.patient_service.list_patients():
            status = "\u542f\u7528" if patient.is_active else "\u5df2\u505c\u7528"
            label = (
                f"{patient.patient_code} \u00b7 "
                f"{patient.family_name}\u60a3\u8005 \u00b7 "
                f"{diagnosis_display_name(patient.clinical_diagnosis)} "
                f"\u00b7 {status}"
            )

            item = QListWidgetItem(label)
            item.setData(
                Qt.ItemDataRole.UserRole,
                str(patient.patient_id),
            )
            self.patient_list.addItem(item)

    def _open_registration_dialog(
        self,
        checked: bool = False,
    ) -> None:
        """Open the new-patient registration form."""
        del checked

        dialog = RegisterPatientDialog(
            self.patient_service,
            self,
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_patients()

            if dialog.registered_patient is not None:
                patient_id = str(dialog.registered_patient.patient_id)

                for row in range(self.patient_list.count()):
                    item = self.patient_list.item(row)

                    if item.data(Qt.ItemDataRole.UserRole) == patient_id:
                        self.patient_list.setCurrentRow(row)
                        break

    def _select_patient(
        self,
        item: QListWidgetItem | None = None,
        checked: bool = False,
    ) -> None:
        """Choose the highlighted patient."""
        del checked

        selected_item = item or self.patient_list.currentItem()

        if selected_item is None:
            QMessageBox.information(
                self,
                "\u672a\u9009\u62e9\u60a3\u8005",
                "\u8bf7\u5148\u5728\u5217\u8868\u4e2d\u9009\u62e9\u4e00\u540d\u60a3\u8005\u3002",
            )
            return

        patient_id = UUID(selected_item.data(Qt.ItemDataRole.UserRole))

        self.selected_patient = self.patient_service.get_patient(patient_id)
        self.accept()
