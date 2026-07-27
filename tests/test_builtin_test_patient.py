"""Built-in Beta00 patient tests."""

from datetime import date
from pathlib import Path

import pytest

from oculidoc.application.builtin_test_patient import (
    BUILTIN_DIAGNOSIS_DETAILS,
    BUILTIN_TEST_PATIENT_BIRTH_DATE,
    BUILTIN_TEST_PATIENT_CODE,
    VIRTUAL_PATIENT_IDENTITIES,
    ensure_builtin_test_patient,
    load_or_create_install_date,
)
from oculidoc.domain import ClinicalDiagnosis, Sex
from oculidoc.infrastructure.database import initialize_database


def test_install_date_is_written_once(
    tmp_path: Path,
) -> None:
    first_date = date(2026, 7, 23)

    assert (
        load_or_create_install_date(
            tmp_path,
            current_date=first_date,
        )
        == first_date
    )
    assert (
        load_or_create_install_date(
            tmp_path,
            current_date=date(2026, 8, 1),
        )
        == first_date
    )
    assert (tmp_path / "runtime" / "install_date.txt").read_text(encoding="utf-8") == "2026-07-23\n"


def test_invalid_install_date_is_rejected(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "runtime" / "install_date.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("not-a-date\n", encoding="utf-8")

    with pytest.raises(ValueError, match="安装日期记录无效"):
        load_or_create_install_date(tmp_path)


def test_virtual_identity_pool_covers_all_requested_roles() -> None:
    assert {identity.role for identity in VIRTUAL_PATIENT_IDENTITIES} == {
        "测试员",
        "康复师",
        "程序员",
        "护士长",
        "规培生",
        "工程师",
        "实习生",
    }
    assert all(identity.sex in {Sex.MALE, Sex.FEMALE} for identity in VIRTUAL_PATIENT_IDENTITIES)
    assert len({identity.name for identity in VIRTUAL_PATIENT_IDENTITIES}) == 7


def test_builtin_patient_uses_selected_identity_and_requested_profile(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(tmp_path / "oculidoc.sqlite3")
    identity = VIRTUAL_PATIENT_IDENTITIES[2]
    enrollment_date = date(2026, 7, 23)

    patient = ensure_builtin_test_patient(
        runtime.patient_service,
        enrollment_date=enrollment_date,
        identity=identity,
    )

    assert patient.patient_code == BUILTIN_TEST_PATIENT_CODE
    assert patient.family_name == identity.name
    assert patient.sex is identity.sex
    assert patient.date_of_birth == BUILTIN_TEST_PATIENT_BIRTH_DATE
    assert patient.enrollment_date == enrollment_date
    assert patient.etiology == "过度加班"
    assert patient.clinical_diagnosis is ClinicalDiagnosis.OTHER
    assert patient.diagnosis_details == BUILTIN_DIAGNOSIS_DETAILS
    assert identity.role in patient.notes
    assert "不得纳入真实患者统计" in patient.notes

    runtime.dispose()


def test_builtin_patient_is_not_rerandomized_or_overwritten(
    tmp_path: Path,
) -> None:
    runtime = initialize_database(tmp_path / "oculidoc.sqlite3")
    first = ensure_builtin_test_patient(
        runtime.patient_service,
        enrollment_date=date(2026, 7, 23),
        identity=VIRTUAL_PATIENT_IDENTITIES[0],
    )
    runtime.patient_service.deactivate_patient(first.patient_id)

    second = ensure_builtin_test_patient(
        runtime.patient_service,
        enrollment_date=date(2026, 8, 1),
        identity=VIRTUAL_PATIENT_IDENTITIES[1],
    )

    assert second.patient_id == first.patient_id
    assert second.family_name == VIRTUAL_PATIENT_IDENTITIES[0].name
    assert second.enrollment_date == date(2026, 7, 23)
    assert second.is_active is False
    assert len(runtime.patient_service.list_patients()) == 1

    runtime.dispose()
