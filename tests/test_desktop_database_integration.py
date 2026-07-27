"""Desktop and patient database integration tests."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from oculidoc.app import create_admin_window
from oculidoc.application import RegisterPatientRequest
from oculidoc.application.builtin_test_patient import BUILTIN_TEST_PATIENT_CODE
from oculidoc.config import Settings
from oculidoc.infrastructure.database import initialize_database
from oculidoc.ui.main_window import AdminMainWindow


def test_settings_exposes_database_path(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)

    assert settings.database_path == (tmp_path / "oculidoc.sqlite3").resolve()
    assert settings.database_url.endswith("/oculidoc.sqlite3")


def test_create_admin_window_initializes_database(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)

    window, database_runtime = create_admin_window(settings)
    qtbot.addWidget(window)

    assert settings.database_path.exists()
    assert window.patient_service is (database_runtime.patient_service)
    assert window.experiment_session_service is (database_runtime.experiment_session_service)
    patients = database_runtime.patient_service.list_patients()

    assert [patient.patient_code for patient in patients] == [BUILTIN_TEST_PATIENT_CODE]
    assert window.patient_label.text() == ("已登记 1 名患者，其中 1 名启用；尚未选择当前患者。")

    window.close()
    database_runtime.dispose()


def test_admin_window_displays_patient_counts(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    database_runtime = initialize_database(settings.database_path)

    active_patient = database_runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-UI-001",
            family_name="\u542f\u7528",
        )
    )
    inactive_patient = database_runtime.patient_service.register_patient(
        RegisterPatientRequest(
            patient_code="DOC-UI-002",
            family_name="\u505c\u7528",
        )
    )

    assert active_patient.is_active is True

    database_runtime.patient_service.deactivate_patient(inactive_patient.patient_id)

    window = AdminMainWindow(
        settings,
        database_runtime.patient_service,
    )
    qtbot.addWidget(window)

    assert "\u5df2\u767b\u8bb0 2 \u540d\u60a3\u8005" in window.patient_label.text()
    assert "\u5176\u4e2d 1 \u540d\u542f\u7528" in window.patient_label.text()

    window.close()
    database_runtime.dispose()
