"""Single-CSV transfer of patient records and complete experiment workspaces."""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import TextIO, cast
from uuid import UUID

from oculidoc.application.experiment_session_service import (
    ExperimentSessionService,
)
from oculidoc.application.patient_service import PatientService, RegisterPatientRequest
from oculidoc.domain import ClinicalDiagnosis, Patient, Sex
from oculidoc.domain.experiment_session import (
    ExperimentSession,
    ExperimentSessionStatus,
    SessionArtifact,
    SessionArtifactKind,
)

PATIENT_TRANSFER_SCHEMA = "2.0"
LEGACY_PATIENT_TRANSFER_SCHEMA = "1.0"
FILE_CHUNK_BYTES = 24_000
MAX_TRANSFER_ROWS = 2_000_000
CSV_FIELDS = (
    "schema_version",
    "record_type",
    "patient_id",
    "patient_code",
    "session_id",
    "relative_path",
    "chunk_index",
    "chunk_count",
    "size_bytes",
    "sha256",
    "payload",
)


@dataclass(frozen=True, slots=True)
class PatientImportRecord:
    patient: Patient

    @property
    def request(self) -> RegisterPatientRequest:
        return RegisterPatientRequest(
            patient_code=self.patient.patient_code,
            family_name=self.patient.family_name,
            sex=self.patient.sex,
            date_of_birth=self.patient.date_of_birth,
            etiology=self.patient.etiology,
            clinical_diagnosis=self.patient.clinical_diagnosis,
            diagnosis_details=self.patient.diagnosis_details,
            enrollment_date=self.patient.enrollment_date,
            notes=self.patient.notes,
        )

    @property
    def is_active(self) -> bool:
        return self.patient.is_active


@dataclass(frozen=True, slots=True)
class SessionImportRecord:
    session: ExperimentSession
    artifacts: tuple[SessionArtifact, ...]


@dataclass(frozen=True, slots=True)
class TransferFileRecord:
    session_id: UUID
    relative_path: str
    size_bytes: int
    sha256: str
    staged_path: Path


@dataclass(slots=True)
class PatientTransferBundle:
    patients: tuple[PatientImportRecord, ...]
    sessions: tuple[SessionImportRecord, ...] = ()
    files: tuple[TransferFileRecord, ...] = ()
    _temporary_directory: TemporaryDirectory[str] | None = field(
        default=None,
        repr=False,
    )

    def close(self) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None

    def __enter__(self) -> PatientTransferBundle:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class PatientImportSummary:
    imported_count: int
    skipped_duplicate_count: int
    imported_session_count: int = 0
    imported_file_count: int = 0


@dataclass(frozen=True, slots=True)
class _SessionExport:
    patient: Patient
    session: ExperimentSession
    artifacts: tuple[SessionArtifact, ...]
    directory: Path
    files: tuple[Path, ...]


def _datetime_text(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _patient_payload(patient: Patient) -> dict[str, object]:
    return {
        "patient_id": str(patient.patient_id),
        "patient_code": patient.patient_code,
        "family_name": patient.family_name,
        "sex": patient.sex.value,
        "date_of_birth": (
            patient.date_of_birth.isoformat() if patient.date_of_birth is not None else None
        ),
        "etiology": patient.etiology,
        "clinical_diagnosis": patient.clinical_diagnosis.value,
        "diagnosis_details": patient.diagnosis_details,
        "enrollment_date": patient.enrollment_date.isoformat(),
        "notes": patient.notes,
        "is_active": patient.is_active,
        "created_at": _datetime_text(patient.created_at),
        "updated_at": _datetime_text(patient.updated_at),
    }


def _session_payload(session: ExperimentSession) -> dict[str, object]:
    return {
        "session_id": str(session.session_id),
        "patient_id": str(session.patient_id),
        "module_id": session.module_id,
        "status": session.status.value,
        "data_directory": session.data_directory,
        "schema_version": session.schema_version,
        "clock_origin_monotonic_ns": session.clock_origin_monotonic_ns,
        "clock_origin_utc": _datetime_text(session.clock_origin_utc),
        "started_at": _datetime_text(session.started_at),
        "ended_at": _datetime_text(session.ended_at),
        "failure_reason": session.failure_reason,
        "created_at": _datetime_text(session.created_at),
        "updated_at": _datetime_text(session.updated_at),
    }


def _artifact_payload(artifact: SessionArtifact) -> dict[str, object]:
    return {
        "artifact_id": str(artifact.artifact_id),
        "session_id": str(artifact.session_id),
        "kind": artifact.kind.value,
        "relative_path": artifact.relative_path,
        "source": artifact.source,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "created_at": _datetime_text(artifact.created_at),
    }


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _transfer_row(
    record_type: str,
    *,
    patient_id: UUID | None = None,
    patient_code: str = "",
    session_id: UUID | None = None,
    relative_path: str = "",
    chunk_index: int | None = None,
    chunk_count: int | None = None,
    size_bytes: int | None = None,
    sha256: str = "",
    payload: str = "",
) -> dict[str, object]:
    return {
        "schema_version": PATIENT_TRANSFER_SCHEMA,
        "record_type": record_type,
        "patient_id": str(patient_id) if patient_id is not None else "",
        "patient_code": patient_code,
        "session_id": str(session_id) if session_id is not None else "",
        "relative_path": relative_path,
        "chunk_index": "" if chunk_index is None else chunk_index,
        "chunk_count": "" if chunk_count is None else chunk_count,
        "size_bytes": "" if size_bytes is None else size_bytes,
        "sha256": sha256,
        "payload": payload,
    }


def patient_transfer_document(patients: list[Patient]) -> dict[str, object]:
    """Return the former demographic-only shape for legacy integrations."""
    return {
        "schema_version": LEGACY_PATIENT_TRANSFER_SCHEMA,
        "exported_at_utc": datetime.now(UTC).isoformat(),
        "contains_session_data": False,
        "patients": [_patient_payload(patient) for patient in patients],
    }


def _safe_relative_path(value: str, *, field_name: str = "relative_path") -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise ValueError(f"{field_name} 不是安全的相对路径。")
    return path.as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _session_files(directory: Path) -> tuple[Path, ...]:
    resolved_directory = directory.resolve()
    if not resolved_directory.is_dir():
        raise FileNotFoundError(f"实验会话目录不存在：{resolved_directory}")

    files: list[Path] = []
    for candidate in sorted(resolved_directory.rglob("*")):
        if candidate.is_symlink():
            raise ValueError(f"实验会话目录包含符号链接，无法安全导出：{candidate}")
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(resolved_directory)
        except ValueError as error:
            raise ValueError(f"实验文件指向会话目录之外：{candidate}") from error
        files.append(resolved)
    return tuple(files)


def _collect_session_exports(
    patients: list[Patient],
    service: ExperimentSessionService | None,
) -> tuple[_SessionExport, ...]:
    if service is None:
        return ()

    exports: list[_SessionExport] = []
    for patient in patients:
        for session in service.list_sessions_for_patient(patient.patient_id):
            if not session.is_terminal:
                raise RuntimeError(
                    f"患者 {patient.patient_code} 有正在进行的实验；请先结束实验再导出。"
                )
            directory = service.resolve_session_directory(session.session_id)
            files = _session_files(directory)
            file_paths = {path.relative_to(directory.resolve()).as_posix() for path in files}
            artifacts = tuple(service.list_artifacts(session.session_id))
            for artifact in artifacts:
                if artifact.relative_path not in file_paths:
                    raise FileNotFoundError(
                        f"实验会话文件清单所指文件不存在：{directory / artifact.relative_path}"
                    )
            exports.append(
                _SessionExport(
                    patient=patient,
                    session=session,
                    artifacts=artifacts,
                    directory=directory,
                    files=files,
                )
            )
    return tuple(exports)


@contextmanager
def _atomic_csv_stream(destination: Path) -> Iterator[TextIO]:
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8-sig",
            newline="",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            yield cast(TextIO, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_patient_transfer(
    path: str | Path,
    patients: list[Patient],
    experiment_session_service: ExperimentSessionService | None = None,
) -> Path:
    """Write all patient and experiment data to one atomic UTF-8 CSV file."""
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    session_exports = _collect_session_exports(patients, experiment_session_service)
    artifact_count = sum(len(item.artifacts) for item in session_exports)
    file_count = sum(len(item.files) for item in session_exports)
    total_bytes = sum(path.stat().st_size for item in session_exports for path in item.files)

    with _atomic_csv_stream(destination) as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow(
            _transfer_row(
                "manifest",
                payload=_compact_json(
                    {
                        "exported_at_utc": datetime.now(UTC).isoformat(),
                        "contains_session_data": experiment_session_service is not None,
                        "patient_count": len(patients),
                        "session_count": len(session_exports),
                        "artifact_count": artifact_count,
                        "file_count": file_count,
                        "total_file_bytes": total_bytes,
                        "file_encoding": "base64",
                        "file_chunk_bytes": FILE_CHUNK_BYTES,
                    }
                ),
            )
        )

        sessions_by_patient: dict[UUID, list[_SessionExport]] = {}
        for item in session_exports:
            sessions_by_patient.setdefault(item.patient.patient_id, []).append(item)

        for patient in patients:
            writer.writerow(
                _transfer_row(
                    "patient",
                    patient_id=patient.patient_id,
                    patient_code=patient.patient_code,
                    payload=_compact_json(_patient_payload(patient)),
                )
            )

            for item in sessions_by_patient.get(patient.patient_id, []):
                session = item.session
                writer.writerow(
                    _transfer_row(
                        "session",
                        patient_id=patient.patient_id,
                        patient_code=patient.patient_code,
                        session_id=session.session_id,
                        payload=_compact_json(_session_payload(session)),
                    )
                )

                for artifact in item.artifacts:
                    writer.writerow(
                        _transfer_row(
                            "artifact",
                            patient_id=patient.patient_id,
                            patient_code=patient.patient_code,
                            session_id=session.session_id,
                            relative_path=artifact.relative_path,
                            payload=_compact_json(_artifact_payload(artifact)),
                        )
                    )

                for file_path in item.files:
                    relative_path = file_path.relative_to(item.directory.resolve()).as_posix()
                    size_bytes = file_path.stat().st_size
                    sha256 = _sha256(file_path)
                    chunk_count = max(1, (size_bytes + FILE_CHUNK_BYTES - 1) // FILE_CHUNK_BYTES)
                    verification = hashlib.sha256()

                    with file_path.open("rb") as source:
                        for chunk_index in range(chunk_count):
                            chunk = source.read(FILE_CHUNK_BYTES)
                            if size_bytes and not chunk:
                                raise OSError(f"实验文件在导出时发生变化：{file_path}")
                            verification.update(chunk)
                            writer.writerow(
                                _transfer_row(
                                    "file",
                                    patient_id=patient.patient_id,
                                    patient_code=patient.patient_code,
                                    session_id=session.session_id,
                                    relative_path=relative_path,
                                    chunk_index=chunk_index,
                                    chunk_count=chunk_count,
                                    size_bytes=size_bytes,
                                    sha256=sha256,
                                    payload=base64.b64encode(chunk).decode("ascii"),
                                )
                            )

                        if source.read(1) or verification.hexdigest() != sha256:
                            raise OSError(f"实验文件在导出时发生变化：{file_path}")
    return destination


def _optional_date(value: object, name: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是 ISO 日期或空值。")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} 不是有效的 ISO 日期。") from error


def _required_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是 ISO 时间。")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} 不是有效的 ISO 时间。") from error
    if result.tzinfo is None:
        raise ValueError(f"{name} 必须包含时区。")
    return result.astimezone(UTC)


def _optional_datetime(value: object, name: str) -> datetime | None:
    return None if value is None or value == "" else _required_datetime(value, name)


def _payload_dict(row: dict[str, str], row_number: int) -> dict[str, object]:
    try:
        value = json.loads(row["payload"])
    except json.JSONDecodeError as error:
        raise ValueError(f"CSV 第 {row_number} 行 payload 不是有效 JSON。") from error
    if not isinstance(value, dict):
        raise TypeError(f"CSV 第 {row_number} 行 payload 必须是对象。")
    return value


def _patient_from_payload(value: dict[str, object]) -> Patient:
    active = value.get("is_active", True)
    if not isinstance(active, bool):
        raise TypeError("is_active 必须是布尔值。")
    enrollment_date = _optional_date(value.get("enrollment_date"), "enrollment_date")
    if enrollment_date is None:
        raise ValueError("患者资料缺少 enrollment_date。")
    return Patient(
        patient_id=UUID(str(value["patient_id"])),
        patient_code=str(value.get("patient_code", "")),
        family_name=str(value.get("family_name", "")),
        sex=Sex(str(value.get("sex", Sex.UNKNOWN.value))),
        date_of_birth=_optional_date(value.get("date_of_birth"), "date_of_birth"),
        etiology=(str(value["etiology"]) if value.get("etiology") is not None else None),
        clinical_diagnosis=ClinicalDiagnosis(
            str(value.get("clinical_diagnosis", ClinicalDiagnosis.UNKNOWN.value))
        ),
        diagnosis_details=(
            str(value["diagnosis_details"]) if value.get("diagnosis_details") is not None else None
        ),
        enrollment_date=enrollment_date,
        notes=str(value.get("notes", "")),
        is_active=active,
        created_at=_required_datetime(value["created_at"], "created_at"),
        updated_at=_required_datetime(value["updated_at"], "updated_at"),
    )


def _session_from_payload(value: dict[str, object]) -> ExperimentSession:
    return ExperimentSession(
        session_id=UUID(str(value["session_id"])),
        patient_id=UUID(str(value["patient_id"])),
        module_id=str(value["module_id"]),
        status=ExperimentSessionStatus(str(value["status"])),
        data_directory=str(value["data_directory"]),
        schema_version=str(value.get("schema_version", "1.0")),
        clock_origin_monotonic_ns=(
            int(str(value["clock_origin_monotonic_ns"]))
            if value.get("clock_origin_monotonic_ns") is not None
            else None
        ),
        clock_origin_utc=_optional_datetime(
            value.get("clock_origin_utc"),
            "clock_origin_utc",
        ),
        started_at=_optional_datetime(value.get("started_at"), "started_at"),
        ended_at=_optional_datetime(value.get("ended_at"), "ended_at"),
        failure_reason=(
            str(value["failure_reason"]) if value.get("failure_reason") is not None else None
        ),
        created_at=_required_datetime(value["created_at"], "created_at"),
        updated_at=_required_datetime(value["updated_at"], "updated_at"),
    )


def _artifact_from_payload(value: dict[str, object]) -> SessionArtifact:
    return SessionArtifact(
        artifact_id=UUID(str(value["artifact_id"])),
        session_id=UUID(str(value["session_id"])),
        kind=SessionArtifactKind(str(value["kind"])),
        relative_path=str(value["relative_path"]),
        source=str(value["source"]),
        mime_type=(str(value["mime_type"]) if value.get("mime_type") is not None else None),
        size_bytes=(int(str(value["size_bytes"])) if value.get("size_bytes") is not None else None),
        sha256=(str(value["sha256"]) if value.get("sha256") is not None else None),
        created_at=_required_datetime(value["created_at"], "created_at"),
    )


def _read_legacy_json(source: Path) -> PatientTransferBundle:
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != LEGACY_PATIENT_TRANSFER_SCHEMA
    ):
        raise ValueError("患者资料文件版本不受支持。")
    raw_patients = payload.get("patients")
    if not isinstance(raw_patients, list):
        raise TypeError("患者资料文件缺少 patients 列表。")
    if len(raw_patients) > 10_000:
        raise ValueError("患者资料文件记录过多。")

    records: list[PatientImportRecord] = []
    for index, value in enumerate(raw_patients, start=1):
        if not isinstance(value, dict):
            raise TypeError(f"第 {index} 条患者资料不是对象。")
        enrollment_date = _optional_date(value.get("enrollment_date"), "enrollment_date")
        if enrollment_date is None:
            raise ValueError(f"第 {index} 条患者资料缺少 enrollment_date。")
        active = value.get("is_active", True)
        if not isinstance(active, bool):
            raise TypeError(f"第 {index} 条患者资料的 is_active 必须是布尔值。")
        records.append(
            PatientImportRecord(
                Patient(
                    patient_code=str(value.get("patient_code", "")),
                    family_name=str(value.get("family_name", "")),
                    sex=Sex(str(value.get("sex", Sex.UNKNOWN.value))),
                    date_of_birth=_optional_date(value.get("date_of_birth"), "date_of_birth"),
                    etiology=(
                        str(value["etiology"]) if value.get("etiology") is not None else None
                    ),
                    clinical_diagnosis=ClinicalDiagnosis(
                        str(
                            value.get(
                                "clinical_diagnosis",
                                ClinicalDiagnosis.UNKNOWN.value,
                            )
                        )
                    ),
                    diagnosis_details=(
                        str(value["diagnosis_details"])
                        if value.get("diagnosis_details") is not None
                        else None
                    ),
                    enrollment_date=enrollment_date,
                    notes=str(value.get("notes", "")),
                    is_active=active,
                )
            )
        )
    return PatientTransferBundle(tuple(records))


def _validated_int(row: dict[str, str], name: str, row_number: int) -> int:
    try:
        result = int(row[name])
    except (TypeError, ValueError) as error:
        raise ValueError(f"CSV 第 {row_number} 行 {name} 必须是整数。") from error
    if result < 0:
        raise ValueError(f"CSV 第 {row_number} 行 {name} 不能为负数。")
    return result


def _manifest_int(manifest: dict[str, object], name: str) -> int:
    try:
        result = int(str(manifest[name]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"CSV manifest 缺少有效的 {name}。") from error
    if result < 0:
        raise ValueError(f"CSV manifest 的 {name} 不能为负数。")
    return result


def _validate_manifest_count(
    manifest: dict[str, object],
    name: str,
    actual: int,
) -> None:
    expected = _manifest_int(manifest, name)
    if expected != actual:
        raise ValueError(f"CSV {name} 不完整：声明 {expected}，实际 {actual}。")


def _read_csv_transfer(source: Path) -> PatientTransferBundle:
    temporary = TemporaryDirectory(prefix="OculiDoC_patient_import_")
    staging_root = Path(temporary.name)
    patients: list[PatientImportRecord] = []
    sessions: dict[UUID, ExperimentSession] = {}
    artifacts: dict[UUID, list[SessionArtifact]] = {}
    files: list[TransferFileRecord] = []
    manifest: dict[str, object] | None = None
    declared_total_file_bytes: int | None = None
    staged_total_file_bytes = 0

    current_key: tuple[UUID, str] | None = None
    current_path: Path | None = None
    current_digest = hashlib.sha256()
    current_size = 0
    current_expected_size = 0
    current_expected_sha = ""
    current_next_index = 0
    current_chunk_count = 0
    finished_file_keys: set[tuple[UUID, str]] = set()

    def finish_file() -> None:
        nonlocal current_key, current_path, current_digest, current_size
        nonlocal current_expected_size, current_expected_sha
        nonlocal current_next_index, current_chunk_count, staged_total_file_bytes
        if current_key is None or current_path is None:
            return
        if current_next_index != current_chunk_count:
            raise ValueError(f"CSV 文件分块不完整：{current_key[1]}")
        if current_size != current_expected_size:
            raise ValueError(f"CSV 文件大小校验失败：{current_key[1]}")
        if current_digest.hexdigest() != current_expected_sha:
            raise ValueError(f"CSV 文件 SHA-256 校验失败：{current_key[1]}")
        files.append(
            TransferFileRecord(
                session_id=current_key[0],
                relative_path=current_key[1],
                size_bytes=current_size,
                sha256=current_expected_sha,
                staged_path=current_path,
            )
        )
        staged_total_file_bytes += current_size
        finished_file_keys.add(current_key)
        current_key = None
        current_path = None
        current_digest = hashlib.sha256()
        current_size = 0
        current_expected_size = 0
        current_expected_sha = ""
        current_next_index = 0
        current_chunk_count = 0

    try:
        csv.field_size_limit(16 * 1024 * 1024)
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or not set(CSV_FIELDS).issubset(reader.fieldnames):
                raise ValueError("不是受支持的 OculiDoC 患者数据 CSV。")

            for row_number, row in enumerate(reader, start=2):
                if row_number > MAX_TRANSFER_ROWS + 1:
                    raise ValueError("患者数据 CSV 记录过多。")
                if row["schema_version"] != PATIENT_TRANSFER_SCHEMA:
                    raise ValueError(f"CSV 第 {row_number} 行版本不受支持。")

                record_type = row["record_type"].strip()
                if record_type != "file":
                    finish_file()

                if record_type == "manifest":
                    if manifest is not None or row_number != 2:
                        raise ValueError("CSV 必须且只能以一个 manifest 开始。")
                    manifest = _payload_dict(row, row_number)
                    declared_total_file_bytes = _manifest_int(
                        manifest,
                        "total_file_bytes",
                    )
                    if declared_total_file_bytes >= shutil.disk_usage(staging_root).free:
                        raise OSError("磁盘剩余空间不足，无法暂存该患者数据 CSV。")
                elif record_type == "patient":
                    patient = _patient_from_payload(_payload_dict(row, row_number))
                    if (
                        row["patient_id"] != str(patient.patient_id)
                        or row["patient_code"] != patient.patient_code
                    ):
                        raise ValueError(f"CSV 第 {row_number} 行患者索引与内容不一致。")
                    patients.append(PatientImportRecord(patient))
                elif record_type == "session":
                    session = _session_from_payload(_payload_dict(row, row_number))
                    if row["patient_id"] != str(session.patient_id) or row["session_id"] != str(
                        session.session_id
                    ):
                        raise ValueError(f"CSV 第 {row_number} 行会话索引与内容不一致。")
                    if session.session_id in sessions:
                        raise ValueError(f"CSV 会话编号重复：{session.session_id}")
                    sessions[session.session_id] = session
                elif record_type == "artifact":
                    artifact = _artifact_from_payload(_payload_dict(row, row_number))
                    if (
                        row["session_id"] != str(artifact.session_id)
                        or _safe_relative_path(row["relative_path"]) != artifact.relative_path
                    ):
                        raise ValueError(f"CSV 第 {row_number} 行文件清单索引与内容不一致。")
                    referenced_session = sessions.get(artifact.session_id)
                    if referenced_session is None or row["patient_id"] != str(
                        referenced_session.patient_id
                    ):
                        raise ValueError(f"CSV 第 {row_number} 行文件清单引用无效。")
                    artifacts.setdefault(artifact.session_id, []).append(artifact)
                elif record_type == "file":
                    session_id = UUID(row["session_id"])
                    referenced_session = sessions.get(session_id)
                    if referenced_session is None or row["patient_id"] != str(
                        referenced_session.patient_id
                    ):
                        raise ValueError(f"CSV 第 {row_number} 行实验文件引用无效。")
                    relative_path = _safe_relative_path(row["relative_path"])
                    key = (session_id, relative_path)
                    chunk_index = _validated_int(row, "chunk_index", row_number)
                    chunk_count = _validated_int(row, "chunk_count", row_number)
                    size_bytes = _validated_int(row, "size_bytes", row_number)
                    sha256 = row["sha256"].strip().lower()
                    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
                        raise ValueError(f"CSV 第 {row_number} 行 SHA-256 无效。")

                    if current_key != key:
                        finish_file()
                        if key in finished_file_keys:
                            raise ValueError(f"CSV 实验文件路径重复：{relative_path}")
                        expected_chunk_count = max(
                            1,
                            (size_bytes + FILE_CHUNK_BYTES - 1) // FILE_CHUNK_BYTES,
                        )
                        if chunk_index != 0 or chunk_count != expected_chunk_count:
                            raise ValueError(f"CSV 文件首个分块编号无效：{relative_path}")
                        current_key = key
                        current_expected_size = size_bytes
                        current_expected_sha = sha256
                        current_chunk_count = chunk_count
                        current_path = staging_root / "sessions" / str(session_id)
                        current_path = current_path.joinpath(*PurePosixPath(relative_path).parts)
                        current_path.parent.mkdir(parents=True, exist_ok=True)
                        current_path.write_bytes(b"")
                    elif (
                        size_bytes != current_expected_size
                        or sha256 != current_expected_sha
                        or chunk_count != current_chunk_count
                    ):
                        raise ValueError(f"CSV 文件分块元数据不一致：{relative_path}")

                    if chunk_index != current_next_index:
                        raise ValueError(f"CSV 文件分块顺序错误：{relative_path}")
                    try:
                        chunk = base64.b64decode(row["payload"], validate=True)
                    except (binascii.Error, ValueError) as error:
                        raise ValueError(
                            f"CSV 第 {row_number} 行文件分块不是有效 Base64。"
                        ) from error
                    if len(chunk) > FILE_CHUNK_BYTES:
                        raise ValueError(f"CSV 第 {row_number} 行文件分块过大。")
                    expected_chunk_size = min(
                        FILE_CHUNK_BYTES,
                        max(0, size_bytes - chunk_index * FILE_CHUNK_BYTES),
                    )
                    if len(chunk) != expected_chunk_size:
                        raise ValueError(f"CSV 第 {row_number} 行文件分块长度错误。")
                    assert current_path is not None
                    with current_path.open("ab") as output:
                        output.write(chunk)
                    current_digest.update(chunk)
                    current_size += len(chunk)
                    current_next_index += 1
                    if (
                        declared_total_file_bytes is not None
                        and staged_total_file_bytes + current_size > declared_total_file_bytes
                    ):
                        raise ValueError("CSV 实验文件总大小超过 manifest 声明。")
                else:
                    raise ValueError(f"CSV 第 {row_number} 行记录类型不受支持：{record_type}")

        finish_file()
        if manifest is None:
            raise ValueError("CSV 缺少 manifest。")

        patient_ids = {record.patient.patient_id for record in patients}
        patient_codes = {record.patient.patient_code.casefold() for record in patients}
        if len(patient_ids) != len(patients) or len(patient_codes) != len(patients):
            raise ValueError("CSV 内患者编号重复。")

        for session in sessions.values():
            if session.patient_id not in patient_ids:
                raise ValueError(f"会话引用了不存在的患者：{session.session_id}")
        session_paths = [PurePosixPath(session.data_directory) for session in sessions.values()]
        for index, path in enumerate(session_paths):
            if any(
                path == other or path in other.parents or other in path.parents
                for other in session_paths[index + 1 :]
            ):
                raise ValueError("CSV 内实验会话目录重复或互相嵌套。")
        artifact_ids = [
            artifact.artifact_id for values in artifacts.values() for artifact in values
        ]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("CSV 内文件清单 UUID 重复。")
        for session_id, values in artifacts.items():
            if session_id not in sessions:
                raise ValueError(f"文件清单引用了不存在的会话：{session_id}")
            paths = [artifact.relative_path for artifact in values]
            if len(paths) != len(set(paths)):
                raise ValueError(f"会话文件清单路径重复：{session_id}")
        for file in files:
            if file.session_id not in sessions:
                raise ValueError(f"实验文件引用了不存在的会话：{file.session_id}")

        _validate_manifest_count(manifest, "patient_count", len(patients))
        _validate_manifest_count(manifest, "session_count", len(sessions))
        _validate_manifest_count(
            manifest,
            "artifact_count",
            sum(len(values) for values in artifacts.values()),
        )
        _validate_manifest_count(manifest, "file_count", len(files))
        _validate_manifest_count(
            manifest,
            "total_file_bytes",
            sum(file.size_bytes for file in files),
        )

        return PatientTransferBundle(
            patients=tuple(patients),
            sessions=tuple(
                SessionImportRecord(session, tuple(artifacts.get(session_id, ())))
                for session_id, session in sessions.items()
            ),
            files=tuple(files),
            _temporary_directory=temporary,
        )
    except Exception:
        temporary.cleanup()
        raise


def read_patient_transfer(path: str | Path) -> PatientTransferBundle:
    source = Path(path).expanduser().resolve()
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        first_character = ""
        while character := stream.read(1):
            if not character.isspace():
                first_character = character
                break
    if first_character == "{":
        return _read_legacy_json(source)
    try:
        return _read_csv_transfer(source)
    except csv.Error as error:
        raise ValueError("患者数据 CSV 结构无效或字段过大。") from error


def _copy_staged_file(file: TransferFileRecord, session_directory: Path) -> None:
    destination = session_directory.joinpath(*PurePosixPath(file.relative_path).parts).resolve()
    try:
        destination.relative_to(session_directory.resolve())
    except ValueError as error:
        raise ValueError(f"导入文件越出会话目录：{file.relative_path}") from error
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.importing")
    try:
        with file.staged_path.open("rb") as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size != file.size_bytes or _sha256(temporary) != file.sha256:
            raise ValueError(f"导入文件复核失败：{file.relative_path}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def import_patient_records(
    patient_service: PatientService,
    records: PatientTransferBundle | tuple[PatientImportRecord, ...],
    experiment_session_service: ExperimentSessionService | None = None,
) -> PatientImportSummary:
    bundle = (
        records
        if isinstance(records, PatientTransferBundle)
        else PatientTransferBundle(tuple(records))
    )
    existing_patients = patient_service.list_patients()
    existing_codes = {patient.patient_code.casefold() for patient in existing_patients}
    existing_ids = {patient.patient_id for patient in existing_patients}
    patients_to_import: list[Patient] = []
    skipped_ids: set[UUID] = set()

    for patient_record in bundle.patients:
        patient = patient_record.patient
        if patient.patient_code.casefold() in existing_codes:
            skipped_ids.add(patient.patient_id)
            continue
        if patient.patient_id in existing_ids:
            raise ValueError(f"患者 UUID 已存在但编号不同：{patient.patient_id}")
        patients_to_import.append(patient)
        existing_codes.add(patient.patient_code.casefold())
        existing_ids.add(patient.patient_id)

    imported_patient_ids = {patient.patient_id for patient in patients_to_import}
    sessions_to_import = [
        record for record in bundle.sessions if record.session.patient_id in imported_patient_ids
    ]
    if sessions_to_import and experiment_session_service is None:
        raise ValueError("该 CSV 包含实验数据，但实验会话服务未连接。")

    if experiment_session_service is not None:
        for session_record in sessions_to_import:
            experiment_session_service.validate_restore_destination(
                session_record.session,
                session_record.artifacts,
            )

    for patient in patients_to_import:
        patient_service.restore_patient(patient)

    files_by_session: dict[UUID, list[TransferFileRecord]] = {}
    for file in bundle.files:
        files_by_session.setdefault(file.session_id, []).append(file)

    imported_session_count = 0
    imported_file_count = 0
    if experiment_session_service is not None:
        for session_record in sessions_to_import:
            session = session_record.session
            session_directory = experiment_session_service.restore_session(
                session,
                session_record.artifacts,
            )
            for file in files_by_session.get(session.session_id, []):
                if file.relative_path != "session.json":
                    _copy_staged_file(file, session_directory)
                imported_file_count += 1
            imported_session_count += 1

    return PatientImportSummary(
        imported_count=len(patients_to_import),
        skipped_duplicate_count=len(skipped_ids),
        imported_session_count=imported_session_count,
        imported_file_count=imported_file_count,
    )
