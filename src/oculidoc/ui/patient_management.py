"""Patient registration, editing, and selection dialogs."""

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
    UpdatePatientRequest,
)
from oculidoc.domain import ClinicalDiagnosis, Patient, Sex
from oculidoc.domain.patient_audit import PatientAuditAction

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


def _date_to_qdate(value: date) -> QDate:
    """Convert a Python date into a Qt date."""
    return QDate(
        value.year,
        value.month,
        value.day,
    )


class RegisterPatientDialog(QDialog):
    """Register a new patient or edit an existing patient."""

    def __init__(
        self,
        patient_service: PatientService,
        parent: QWidget | None = None,
        *,
        patient: Patient | None = None,
    ) -> None:
        super().__init__(parent)

        self.patient_service = patient_service
        self.patient = patient
        self.saved_patient: Patient | None = None
        self.registered_patient: Patient | None = None
        self.edited_patient: Patient | None = None

        if patient is None:
            self.setWindowTitle("\u767b\u8bb0\u65b0\u60a3\u8005")
        else:
            self.setWindowTitle("\u7f16\u8f91\u60a3\u8005\u8d44\u6599")

        self.setMinimumWidth(560)

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

        if patient is not None:
            self._populate_patient(patient)

    def _populate_patient(
        self,
        patient: Patient,
    ) -> None:
        """Populate the form with existing patient information."""
        self.patient_code_edit.setText(patient.patient_code)
        self.family_name_edit.setText(patient.family_name)

        sex_index = self.sex_combo.findData(patient.sex.value)
        self.sex_combo.setCurrentIndex(sex_index)

        if patient.date_of_birth is None:
            self.birth_unknown_checkbox.setChecked(True)
        else:
            self.birth_unknown_checkbox.setChecked(False)
            self.birth_date_edit.setDate(_date_to_qdate(patient.date_of_birth))

        self.etiology_edit.setText(patient.etiology or "")

        diagnosis_index = self.diagnosis_combo.findData(patient.clinical_diagnosis.value)
        self.diagnosis_combo.setCurrentIndex(diagnosis_index)

        self.diagnosis_details_edit.setPlainText(patient.diagnosis_details or "")
        self.enrollment_date_edit.setDate(_date_to_qdate(patient.enrollment_date))
        self.notes_edit.setPlainText(patient.notes or "")

    def _register_patient(self) -> None:
        """Validate and save the patient form."""
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

        common_fields = {
            "patient_code": patient_code,
            "family_name": family_name,
            "sex": Sex(self.sex_combo.currentData()),
            "date_of_birth": date_of_birth,
            "etiology": self.etiology_edit.text(),
            "clinical_diagnosis": ClinicalDiagnosis(self.diagnosis_combo.currentData()),
            "diagnosis_details": (self.diagnosis_details_edit.toPlainText()),
            "enrollment_date": _qdate_to_date(self.enrollment_date_edit.date()),
            "notes": self.notes_edit.toPlainText(),
        }

        try:
            if self.patient is None:
                saved_patient = self.patient_service.register_patient(
                    RegisterPatientRequest(**common_fields)
                )
                self.registered_patient = saved_patient
            else:
                saved_patient = self.patient_service.update_patient(
                    UpdatePatientRequest(
                        patient_id=self.patient.patient_id,
                        **common_fields,
                    )
                )
                self.edited_patient = saved_patient

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
                "\u4fdd\u5b58\u5931\u8d25",
                str(error),
            )
            return

        self.saved_patient = saved_patient
        self.patient = saved_patient
        self.accept()


class EditPatientDialog(RegisterPatientDialog):
    """Edit one existing patient."""

    def __init__(
        self,
        patient_service: PatientService,
        patient: Patient,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            patient_service,
            parent,
            patient=patient,
        )


class PatientManagementDialog(QDialog):
    """Manage registered patients and choose the active patient."""

    def __init__(
        self,
        patient_service: PatientService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.patient_service = patient_service
        self.selected_patient: Patient | None = None

        self.setWindowTitle("\u60a3\u8005\u7ba1\u7406")
        self.resize(820, 500)

        title = QLabel("\u5df2\u767b\u8bb0\u60a3\u8005")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")

        self.patient_list = QListWidget()
        self.patient_list.setObjectName("patientList")
        self.patient_list.itemDoubleClicked.connect(self._select_patient)

        self.patient_list.currentItemChanged.connect(self._refresh_detail)

        self.patient_detail_label = QLabel(
            "\u8bf7\u9009\u62e9\u4e00\u540d\u60a3\u8005\u67e5\u770b\u8be6\u60c5\u3002"
        )
        self.patient_detail_label.setObjectName("patientDetailLabel")
        self.patient_detail_label.setWordWrap(True)
        self.patient_detail_label.setMinimumHeight(145)
        self.patient_detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.patient_detail_label.setStyleSheet(
            "background: #f5f8fb; border: 1px solid #d9e3ec; border-radius: 8px; padding: 10px;"
        )

        self.new_button = QPushButton("\u767b\u8bb0\u65b0\u60a3\u8005")
        self.new_button.setObjectName("newPatientButton")
        self.new_button.clicked.connect(self._open_registration_dialog)

        self.edit_button = QPushButton("\u7f16\u8f91\u60a3\u8005\u8d44\u6599")
        self.edit_button.setObjectName("editPatientButton")
        self.edit_button.clicked.connect(self._open_edit_dialog)

        self.status_button = QPushButton("\u505c\u7528/\u6062\u590d")
        self.status_button.setObjectName("togglePatientStatusButton")
        self.status_button.clicked.connect(self._toggle_patient_status)

        self.select_button = QPushButton("\u9009\u4e3a\u5f53\u524d\u60a3\u8005")
        self.select_button.setObjectName("selectPatientButton")
        self.select_button.clicked.connect(self._select_patient)

        close_button = QPushButton("\u5173\u95ed")
        close_button.clicked.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(self.new_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.status_button)
        actions.addStretch(1)
        actions.addWidget(self.select_button)
        actions.addWidget(close_button)

        root = QVBoxLayout(self)
        root.addWidget(title)
        root.addWidget(self.patient_list, 1)
        root.addWidget(self.patient_detail_label)
        root.addLayout(actions)

        self.refresh_patients()

    def refresh_patients(
        self,
        select_patient_id: UUID | None = None,
    ) -> None:
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

            if not patient.is_active:
                item.setForeground(Qt.GlobalColor.gray)

            self.patient_list.addItem(item)

            if patient.patient_id == select_patient_id:
                self.patient_list.setCurrentItem(item)

    def _current_patient(self) -> Patient | None:
        """Return the patient highlighted in the list."""
        item = self.patient_list.currentItem()

        if item is None:
            return None

        patient_id = UUID(item.data(Qt.ItemDataRole.UserRole))
        return self.patient_service.get_patient(patient_id)

    def _require_current_patient(
        self,
    ) -> Patient | None:
        """Return the highlighted patient or show guidance."""
        patient = self._current_patient()

        if patient is None:
            QMessageBox.information(
                self,
                "\u672a\u9009\u62e9\u60a3\u8005",
                "\u8bf7\u5148\u5728\u5217\u8868\u4e2d\u9009\u62e9\u4e00\u540d\u60a3\u8005\u3002",
            )

        return patient

    def _refresh_detail(
        self,
        current: QListWidgetItem | None = None,
        previous: QListWidgetItem | None = None,
    ) -> None:
        """Display information for the highlighted patient."""
        del current, previous

        patient = self._current_patient()

        if patient is None:
            self.patient_detail_label.setText(
                "\u8bf7\u9009\u62e9\u4e00\u540d\u60a3\u8005\u67e5\u770b\u8be6\u60c5\u3002"
            )
            return

        status = "\u542f\u7528" if patient.is_active else "\u5df2\u505c\u7528"
        birth_date = (
            patient.date_of_birth.isoformat()
            if patient.date_of_birth is not None
            else "\u672a\u77e5"
        )

        action_labels = {
            PatientAuditAction.REGISTERED: ("\u767b\u8bb0\u60a3\u8005"),
            PatientAuditAction.UPDATED: ("\u4fee\u6539\u8d44\u6599"),
            PatientAuditAction.DEACTIVATED: ("\u505c\u7528\u60a3\u8005"),
            PatientAuditAction.ACTIVATED: ("\u6062\u590d\u60a3\u8005"),
        }

        field_labels = {
            "patient_code": "\u60a3\u8005\u7f16\u53f7",
            "family_name": "\u663e\u793a\u79f0\u547c",
            "sex": "\u6027\u522b",
            "date_of_birth": "\u51fa\u751f\u65e5\u671f",
            "etiology": "\u75c5\u56e0",
            "clinical_diagnosis": "\u4e34\u5e8a\u8bca\u65ad",
            "diagnosis_details": "\u8bca\u65ad\u8865\u5145",
            "enrollment_date": "\u5165\u7ec4\u65e5\u671f",
            "notes": "\u5907\u6ce8",
            "is_active": "\u542f\u7528\u72b6\u6001",
        }

        events = self.patient_service.list_patient_audit(
            patient.patient_id,
            limit=5,
        )
        audit_lines: list[str] = []

        for event in events:
            changed = "\u3001".join(
                field_labels.get(field_name, field_name) for field_name in event.changed_fields
            )
            occurred_at = event.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")

            audit_lines.append(
                f"{occurred_at}  "
                f"{action_labels[event.action]}" + (f" [{changed}]" if changed else "")
            )

        if not audit_lines:
            audit_lines.append("\u6682\u65e0\u53d8\u66f4\u8bb0\u5f55")

        details = [
            f"\u60a3\u8005\uff1a{patient.display_label}",
            f"\u72b6\u6001\uff1a{status}",
            f"\u6027\u522b\uff1a{SEX_LABELS[patient.sex]}",
            f"\u51fa\u751f\u65e5\u671f\uff1a{birth_date}",
            f"\u75c5\u56e0\uff1a{patient.etiology or '-'}",
            (f"\u4e34\u5e8a\u8bca\u65ad\uff1a{diagnosis_display_name(patient.clinical_diagnosis)}"),
            (f"\u8bca\u65ad\u8865\u5145\uff1a{patient.diagnosis_details or '-'}"),
            (f"\u5165\u7ec4\u65e5\u671f\uff1a{patient.enrollment_date.isoformat()}"),
            f"\u5907\u6ce8\uff1a{patient.notes or '-'}",
            "",
            "\u6700\u8fd1\u53d8\u66f4\uff1a",
            *audit_lines,
        ]

        self.patient_detail_label.setText("\n".join(details))

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
            if dialog.saved_patient is not None:
                self.refresh_patients(dialog.saved_patient.patient_id)

    def _open_edit_dialog(
        self,
        checked: bool = False,
    ) -> None:
        """Edit the highlighted patient."""
        del checked

        patient = self._require_current_patient()

        if patient is None:
            return

        dialog = EditPatientDialog(
            self.patient_service,
            patient,
            self,
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.saved_patient is not None:
                self.refresh_patients(dialog.saved_patient.patient_id)

    def _toggle_patient_status(
        self,
        checked: bool = False,
    ) -> None:
        """Deactivate or reactivate the highlighted patient."""
        del checked

        patient = self._require_current_patient()

        if patient is None:
            return

        if patient.is_active:
            result = QMessageBox.question(
                self,
                "\u505c\u7528\u60a3\u8005",
                "\u505c\u7528\u540e\u5c06\u4fdd\u7559"
                "\u6240\u6709\u5386\u53f2\u6570\u636e\uff0c"
                "\u4f46\u4e0d\u80fd\u5c06\u5176"
                "\u9009\u4e3a\u5f53\u524d\u60a3\u8005\u3002"
                "\u786e\u5b9a\u505c\u7528\u5417\uff1f",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )

            if result != QMessageBox.StandardButton.Yes:
                return

            updated_patient = self.patient_service.deactivate_patient(patient.patient_id)
        else:
            updated_patient = self.patient_service.activate_patient(patient.patient_id)

        self.refresh_patients(updated_patient.patient_id)

    def _select_patient(
        self,
        item: QListWidgetItem | bool | None = None,
        checked: bool = False,
    ) -> None:
        """Choose the highlighted active patient."""
        del checked

        if isinstance(item, QListWidgetItem):
            self.patient_list.setCurrentItem(item)

        patient = self._require_current_patient()

        if patient is None:
            return

        if not patient.is_active:
            QMessageBox.information(
                self,
                "\u60a3\u8005\u5df2\u505c\u7528",
                "\u8bf7\u5148\u6062\u590d\u8be5\u60a3\u8005\uff0c"
                "\u518d\u5c06\u5176\u9009\u4e3a"
                "\u5f53\u524d\u60a3\u8005\u3002",
            )
            return

        self.selected_patient = patient
        self.accept()
