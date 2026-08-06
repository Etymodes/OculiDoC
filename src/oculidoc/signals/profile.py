"""Patient-scoped long-term signal preferences."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TypedDict

from oculidoc.lan_control import utc_now_text
from oculidoc.signals.models import SignalParadigm


class _ProfileDocument(TypedDict):
    schema_version: str
    profiles: dict[str, object]


def _normalized_paradigms(values: tuple[SignalParadigm | str, ...]) -> tuple[SignalParadigm, ...]:
    result: list[SignalParadigm] = []
    for value in values:
        paradigm = SignalParadigm(value)
        if paradigm not in result:
            result.append(paradigm)
    if not result:
        raise ValueError("At least one default signal paradigm is required.")
    return tuple(result)


@dataclass(frozen=True, slots=True)
class PatientSignalProfile:
    """Long-term defaults; one session receives an immutable snapshot."""

    patient_id: str
    default_paradigms: tuple[SignalParadigm, ...] = (SignalParadigm.GAZE,)
    available_devices: tuple[str, ...] = ()
    visual_limitations: str = ""
    reachable_region: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    eeg_sample_rate_hz: float = 250.0
    eeg_channel_names: tuple[str, ...] = ("O1", "Oz", "O2")
    ssvep_frequency_history_hz: tuple[float, ...] = ()
    calibration_models: tuple[str, ...] = ()
    algorithm_history: tuple[str, ...] = ()
    updated_at_utc: str = ""
    revision: int = 0

    def __post_init__(self) -> None:
        patient_id = self.patient_id.strip()
        if not patient_id:
            raise ValueError("patient_id cannot be empty.")
        if isinstance(self.revision, bool) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer.")
        if self.eeg_sample_rate_hz <= 0:
            raise ValueError("eeg_sample_rate_hz must be positive.")
        channels = tuple(name.strip() for name in self.eeg_channel_names)
        if (
            not channels
            or any(not name for name in channels)
            or len(set(channels)) != len(channels)
        ):
            raise ValueError("eeg_channel_names must contain unique non-empty names.")
        region = tuple(float(value) for value in self.reachable_region)
        if len(region) != 4 or any(not 0.0 <= value <= 1.0 for value in region):
            raise ValueError("reachable_region must contain four normalized values.")
        if region[0] >= region[2] or region[1] >= region[3]:
            raise ValueError("reachable_region must have positive width and height.")
        frequencies = tuple(
            dict.fromkeys(float(value) for value in self.ssvep_frequency_history_hz)
        )
        if any(value <= 0 for value in frequencies):
            raise ValueError("SSVEP frequency history must be positive.")

        object.__setattr__(self, "patient_id", patient_id)
        object.__setattr__(self, "default_paradigms", _normalized_paradigms(self.default_paradigms))
        object.__setattr__(
            self,
            "available_devices",
            tuple(dict.fromkeys(item.strip() for item in self.available_devices if item.strip())),
        )
        object.__setattr__(self, "visual_limitations", self.visual_limitations.strip())
        object.__setattr__(self, "reachable_region", region)
        object.__setattr__(self, "eeg_channel_names", channels)
        object.__setattr__(self, "ssvep_frequency_history_hz", frequencies[-24:])
        object.__setattr__(
            self,
            "calibration_models",
            tuple(dict.fromkeys(item.strip() for item in self.calibration_models if item.strip()))[
                -24:
            ],
        )
        object.__setattr__(
            self,
            "algorithm_history",
            tuple(dict.fromkeys(item.strip() for item in self.algorithm_history if item.strip()))[
                -24:
            ],
        )
        object.__setattr__(self, "updated_at_utc", self.updated_at_utc.strip() or utc_now_text())

    def with_session_defaults(
        self,
        *,
        paradigms: tuple[SignalParadigm | str, ...],
        frequencies_hz: tuple[float, ...] = (),
        algorithm: str | None = None,
        device_id: str | None = None,
        calibration_model: str | None = None,
    ) -> PatientSignalProfile:
        devices = self.available_devices
        if device_id is not None and device_id.strip() and device_id.strip() not in devices:
            devices += (device_id.strip(),)
        algorithms = self.algorithm_history
        if algorithm is not None and algorithm.strip() and algorithm.strip() not in algorithms:
            algorithms += (algorithm.strip(),)
        frequencies = self.ssvep_frequency_history_hz + tuple(
            float(item) for item in frequencies_hz
        )
        models = self.calibration_models
        if (
            calibration_model is not None
            and calibration_model.strip()
            and calibration_model.strip() not in models
        ):
            models += (calibration_model.strip(),)
        return replace(
            self,
            default_paradigms=_normalized_paradigms(paradigms),
            available_devices=devices,
            ssvep_frequency_history_hz=tuple(dict.fromkeys(frequencies))[-24:],
            algorithm_history=algorithms[-24:],
            calibration_models=models[-24:],
            updated_at_utc=utc_now_text(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "patient_id": self.patient_id,
            "default_paradigms": [item.value for item in self.default_paradigms],
            "available_devices": list(self.available_devices),
            "visual_limitations": self.visual_limitations,
            "reachable_region": list(self.reachable_region),
            "eeg_sample_rate_hz": self.eeg_sample_rate_hz,
            "eeg_channel_names": list(self.eeg_channel_names),
            "ssvep_frequency_history_hz": list(self.ssvep_frequency_history_hz),
            "calibration_models": list(self.calibration_models),
            "algorithm_history": list(self.algorithm_history),
            "updated_at_utc": self.updated_at_utc,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: object) -> PatientSignalProfile:
        if not isinstance(value, dict):
            raise TypeError("Patient signal profile must be an object.")
        return cls(
            patient_id=str(value["patient_id"]),
            default_paradigms=tuple(
                SignalParadigm(str(item)) for item in value.get("default_paradigms", ["gaze"])
            ),
            available_devices=tuple(str(item) for item in value.get("available_devices", [])),
            visual_limitations=str(value.get("visual_limitations") or ""),
            reachable_region=tuple(
                float(item) for item in value.get("reachable_region", [0, 0, 1, 1])
            ),  # type: ignore[arg-type]
            eeg_sample_rate_hz=float(value.get("eeg_sample_rate_hz", 250.0)),
            eeg_channel_names=tuple(
                str(item) for item in value.get("eeg_channel_names", ["O1", "Oz", "O2"])
            ),
            ssvep_frequency_history_hz=tuple(
                float(item) for item in value.get("ssvep_frequency_history_hz", [])
            ),
            calibration_models=tuple(str(item) for item in value.get("calibration_models", [])),
            algorithm_history=tuple(str(item) for item in value.get("algorithm_history", [])),
            updated_at_utc=str(value.get("updated_at_utc") or ""),
            revision=int(value.get("revision", 0)),
        )


class SignalProfileConflict(RuntimeError):
    """The stored profile changed after it was read."""

    def __init__(self, current: PatientSignalProfile) -> None:
        super().__init__("Patient signal profile revision conflict.")
        self.current = current


class PatientSignalProfileStore:
    """Atomic JSON store for patient signal defaults."""

    schema_version = "1.0"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self, patient_id: str) -> PatientSignalProfile:
        patient_id = patient_id.strip()
        if not patient_id:
            raise ValueError("patient_id cannot be empty.")
        value = self._load_document()["profiles"].get(patient_id)
        if value is None:
            return PatientSignalProfile(patient_id=patient_id)
        profile = PatientSignalProfile.from_dict(value)
        if profile.patient_id != patient_id:
            raise ValueError("Stored signal profile patient does not match its key.")
        return profile

    def save(
        self,
        profile: PatientSignalProfile,
        *,
        expected_revision: int,
    ) -> PatientSignalProfile:
        document = self._load_document()
        stored = document["profiles"].get(profile.patient_id)
        current = (
            PatientSignalProfile.from_dict(stored)
            if stored is not None
            else PatientSignalProfile(patient_id=profile.patient_id)
        )
        if current.revision != int(expected_revision):
            raise SignalProfileConflict(current)
        updated = replace(
            profile,
            revision=current.revision + 1,
            updated_at_utc=utc_now_text(),
        )
        document["profiles"][profile.patient_id] = updated.to_dict()
        self._write(document)
        return updated

    def _load_document(self) -> _ProfileDocument:
        if not self.path.is_file():
            return {"schema_version": self.schema_version, "profiles": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Patient signal profile file is invalid: {self.path}") from error
        if not isinstance(payload, dict) or payload.get("schema_version") != self.schema_version:
            raise ValueError("Unsupported patient signal profile schema.")
        profiles = payload.get("profiles")
        if not isinstance(profiles, dict):
            raise ValueError("Patient signal profiles must be an object.")
        return {"schema_version": self.schema_version, "profiles": dict(profiles)}

    def _write(self, document: _ProfileDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)
