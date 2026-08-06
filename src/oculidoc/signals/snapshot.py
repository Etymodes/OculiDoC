"""Immutable per-session signal configuration snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import MappingProxyType

from oculidoc.lan_control import utc_now_text
from oculidoc.signals.models import SignalParadigm, SignalSourceKind


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class SessionSignalSnapshot:
    """Configuration consumed by one task and its report."""

    patient_id: str
    profile_revision: int
    paradigms: tuple[SignalParadigm, ...]
    task_kind: str
    source_kind: SignalSourceKind
    device_id: str
    sample_rate_hz: float
    channel_names: tuple[str, ...]
    task_configuration: Mapping[str, object]
    algorithm_versions: Mapping[str, str]
    simulated: bool
    created_at_utc: str
    config_sha256: str
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_configuration", _freeze_json(self.task_configuration))
        object.__setattr__(self, "algorithm_versions", _freeze_json(self.algorithm_versions))

    @classmethod
    def create(
        cls,
        *,
        patient_id: str,
        profile_revision: int,
        paradigms: tuple[SignalParadigm | str, ...],
        task_kind: str,
        source_kind: SignalSourceKind | str,
        device_id: str,
        sample_rate_hz: float,
        channel_names: tuple[str, ...],
        task_configuration: Mapping[str, object],
        algorithm_versions: Mapping[str, str],
        simulated: bool,
        created_at_utc: str | None = None,
    ) -> SessionSignalSnapshot:
        created = (created_at_utc or utc_now_text()).strip()
        normalized_patient_id = patient_id.strip()
        normalized_profile_revision = int(profile_revision)
        normalized_paradigms = tuple(SignalParadigm(item) for item in paradigms)
        normalized_task_kind = task_kind.strip()
        normalized_source_kind = SignalSourceKind(source_kind)
        normalized_device_id = device_id.strip()
        normalized_sample_rate_hz = float(sample_rate_hz)
        normalized_channels = tuple(name.strip() for name in channel_names)
        payload = {
            "schema_version": "1.0",
            "patient_id": normalized_patient_id,
            "profile_revision": normalized_profile_revision,
            "paradigms": [item.value for item in normalized_paradigms],
            "task_kind": normalized_task_kind,
            "source_kind": normalized_source_kind.value,
            "device_id": normalized_device_id,
            "sample_rate_hz": normalized_sample_rate_hz,
            "channel_names": list(normalized_channels),
            "task_configuration": _thaw_json(_freeze_json(task_configuration)),
            "algorithm_versions": _thaw_json(_freeze_json(algorithm_versions)),
            "simulated": bool(simulated),
            "created_at_utc": created,
        }
        if not payload["patient_id"] or not payload["task_kind"] or not payload["device_id"]:
            raise ValueError("Signal snapshot identity fields cannot be empty.")
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        return cls(
            patient_id=normalized_patient_id,
            profile_revision=normalized_profile_revision,
            paradigms=normalized_paradigms,
            task_kind=normalized_task_kind,
            source_kind=normalized_source_kind,
            device_id=normalized_device_id,
            sample_rate_hz=normalized_sample_rate_hz,
            channel_names=normalized_channels,
            task_configuration=dict(task_configuration),
            algorithm_versions=dict(algorithm_versions),
            simulated=bool(payload["simulated"]),
            created_at_utc=created,
            config_sha256=digest,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "patient_id": self.patient_id,
            "profile_revision": self.profile_revision,
            "paradigms": [item.value for item in self.paradigms],
            "task_kind": self.task_kind,
            "source_kind": self.source_kind.value,
            "device_id": self.device_id,
            "sample_rate_hz": self.sample_rate_hz,
            "channel_names": list(self.channel_names),
            "task_configuration": _thaw_json(self.task_configuration),
            "algorithm_versions": _thaw_json(self.algorithm_versions),
            "simulated": self.simulated,
            "created_at_utc": self.created_at_utc,
            "config_sha256": self.config_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> SessionSignalSnapshot:
        if not isinstance(value, dict):
            raise TypeError("Session signal snapshot must be an object.")
        created = cls.create(
            patient_id=str(value["patient_id"]),
            profile_revision=int(value["profile_revision"]),
            paradigms=tuple(str(item) for item in value["paradigms"]),  # type: ignore[arg-type]
            task_kind=str(value["task_kind"]),
            source_kind=str(value["source_kind"]),
            device_id=str(value["device_id"]),
            sample_rate_hz=float(value["sample_rate_hz"]),
            channel_names=tuple(str(item) for item in value["channel_names"]),  # type: ignore[arg-type]
            task_configuration=dict(value["task_configuration"]),  # type: ignore[arg-type]
            algorithm_versions=dict(value["algorithm_versions"]),  # type: ignore[arg-type]
            simulated=bool(value["simulated"]),
            created_at_utc=str(value["created_at_utc"]),
        )
        if value.get("schema_version") != created.schema_version:
            raise ValueError("Unsupported session signal snapshot schema.")
        if str(value.get("config_sha256")) != created.config_sha256:
            raise ValueError("Session signal snapshot hash mismatch.")
        return created

    def write(self, path: str | Path) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(self.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary_path.replace(target)
        finally:
            temporary_path.unlink(missing_ok=True)
        return target

    @classmethod
    def read(cls, path: str | Path) -> SessionSignalSnapshot:
        target = Path(path).expanduser().resolve()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Session signal snapshot is invalid: {target}") from error
        return cls.from_dict(payload)
