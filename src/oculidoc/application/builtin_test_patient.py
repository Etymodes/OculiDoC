"""Built-in virtual patient for demonstrations and device testing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from secrets import choice
from tempfile import NamedTemporaryFile

from oculidoc.application.patient_service import PatientService, RegisterPatientRequest
from oculidoc.domain import ClinicalDiagnosis, Patient, Sex

BUILTIN_TEST_PATIENT_CODE = "Beta00"
BUILTIN_TEST_PATIENT_BIRTH_DATE = date(2026, 7, 15)
INSTALL_DATE_FILENAME = "install_date.txt"

BUILTIN_DIAGNOSIS_DETAILS = (
    "虚拟测试对象。主要表现为过度加班后注意力波动、反应变慢，"
    "休息和补水后通常改善。此记录仅用于软件流程与设备联调，"
    "不代表真实临床诊断。"
)


@dataclass(frozen=True, slots=True)
class VirtualPatientIdentity:
    """One persistent role-based alias and its matching sex."""

    role: str
    name: str
    sex: Sex


VIRTUAL_PATIENT_IDENTITIES = (
    VirtualPatientIdentity("测试员", "策世元", Sex.MALE),
    VirtualPatientIdentity("康复师", "康芙诗", Sex.FEMALE),
    VirtualPatientIdentity("程序员", "程旭媛", Sex.FEMALE),
    VirtualPatientIdentity("护士长", "胡世璋", Sex.MALE),
    VirtualPatientIdentity("规培生", "桂培生", Sex.MALE),
    VirtualPatientIdentity("工程师", "龚程诗", Sex.FEMALE),
    VirtualPatientIdentity("实习生", "石习笙", Sex.MALE),
)


def load_or_create_install_date(
    data_root: str | Path,
    *,
    installation_root: str | Path | None = None,
    current_date: date | None = None,
) -> date:
    """Return the device install date, persisting it on first initialization."""
    marker = Path(data_root).expanduser().resolve() / "runtime" / INSTALL_DATE_FILENAME

    if marker.is_file():
        try:
            return date.fromisoformat(marker.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise ValueError(f"OculiDoC 安装日期记录无效：{marker}") from error

    installed_on = current_date
    if installed_on is None and installation_root is not None:
        root_stat = Path(installation_root).expanduser().resolve().stat()
        created_at = getattr(root_stat, "st_birthtime", root_stat.st_ctime)
        installed_on = datetime.fromtimestamp(created_at).date()
    if installed_on is None:
        installed_on = date.today()

    # Repositories installed before the fixed virtual birth date predate this
    # built-in record. Treat the first upgrade carrying Beta00 as its install.
    if installed_on < BUILTIN_TEST_PATIENT_BIRTH_DATE:
        installed_on = date.today()

    marker.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=marker.parent,
        prefix=f".{marker.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary_path = Path(stream.name)
        stream.write(f"{installed_on.isoformat()}\n")

    temporary_path.replace(marker)
    return installed_on


def ensure_builtin_test_patient(
    patient_service: PatientService,
    *,
    enrollment_date: date,
    identity: VirtualPatientIdentity | None = None,
) -> Patient:
    """Create Beta00 once and preserve its stored identity on later starts."""
    existing = next(
        (
            patient
            for patient in patient_service.list_patients()
            if patient.patient_code.casefold() == BUILTIN_TEST_PATIENT_CODE.casefold()
        ),
        None,
    )
    if existing is not None:
        return existing

    selected = identity or choice(VIRTUAL_PATIENT_IDENTITIES)
    notes = (
        f"系统内置虚拟测试员（本次人设：{selected.role}）。"
        "可用于演示、培训、回归测试和眼动设备联调；"
        "其记录不得纳入真实患者统计、科研分析或临床决策。"
        "若其再次“病倒”，请依次检查设备、配置、程序日志"
        "和下班时间。"
    )

    return patient_service.register_patient(
        RegisterPatientRequest(
            patient_code=BUILTIN_TEST_PATIENT_CODE,
            family_name=selected.name,
            sex=selected.sex,
            date_of_birth=BUILTIN_TEST_PATIENT_BIRTH_DATE,
            etiology="过度加班",
            clinical_diagnosis=ClinicalDiagnosis.OTHER,
            diagnosis_details=BUILTIN_DIAGNOSIS_DETAILS,
            enrollment_date=enrollment_date,
            notes=notes,
        )
    )
