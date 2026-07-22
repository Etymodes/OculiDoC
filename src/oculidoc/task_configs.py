"""Versioned task settings shared by the mobile and desktop processes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from oculidoc.lan_control import utc_now_text

TASK_CONFIG_MODULE_IDS = frozenset(
    {
        "tracking_ball",
        "binary_horizontal",
        "binary_vertical",
        "screen_keyboard",
        "multiple_choice",
        "image_choice",
    }
)


def _config_type(module_id: str) -> Any:
    if module_id == "tracking_ball":
        from oculidoc.tasks.tracking_ball import TrackingBallConfig

        return TrackingBallConfig

    if module_id in {"binary_horizontal", "binary_vertical"}:
        from oculidoc.tasks.binary_question import BinaryQuestionConfig

        return BinaryQuestionConfig

    if module_id == "screen_keyboard":
        from oculidoc.tasks.screen_keyboard import ScreenKeyboardConfig

        return ScreenKeyboardConfig

    if module_id == "multiple_choice":
        from oculidoc.tasks.multiple_choice import MultipleChoiceConfig

        return MultipleChoiceConfig

    if module_id == "image_choice":
        from oculidoc.tasks.image_choice import ImageChoiceConfig

        return ImageChoiceConfig

    raise KeyError(f"Unsupported task configuration module: {module_id}")


def task_config_to_dict(config: object) -> dict[str, object]:
    """Serialize one supported task dataclass to JSON-compatible values."""
    values: dict[str, object] = {}

    for field in fields(config):  # type: ignore[arg-type]
        value = getattr(config, field.name)
        if isinstance(value, Enum):
            values[field.name] = value.value
        elif isinstance(value, tuple):
            values[field.name] = list(value)
        else:
            values[field.name] = value

    return values


def task_config_from_dict(module_id: str, value: object) -> object:
    """Validate and construct a task config for one module."""
    if not isinstance(value, dict):
        raise TypeError("Task config must be an object.")

    normalized = {str(key): item for key, item in value.items()}

    for name in {
        "show_gaze_cursor",
        "randomize_sides",
        "randomize_positions",
        "enable_tone_step",
    } & normalized.keys():
        if not isinstance(normalized[name], bool):
            raise TypeError(f"{name} must be a boolean.")

    seed = normalized.get("randomization_seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise TypeError("randomization_seed must be an integer or null.")

    for name in {"question_template_ids", "question_ids"} & normalized.keys():
        identifiers = normalized[name]

        if not isinstance(identifiers, (list, tuple)) or any(
            not isinstance(value, str) for value in identifiers
        ):
            raise TypeError(f"{name} must be a list of strings.")

    return _config_type(module_id)(**normalized)


def default_task_config(module_id: str) -> object:
    """Return the existing desktop defaults for one supported module."""
    config_type = _config_type(module_id)

    if module_id in {"binary_horizontal", "binary_vertical"}:
        return config_type(question="你现在感到舒服吗？")

    return config_type()


@dataclass(frozen=True, slots=True)
class TaskConfigRecord:
    module_id: str
    revision: int
    config: dict[str, object]
    updated_at_utc: str

    @classmethod
    def default(cls, module_id: str) -> TaskConfigRecord:
        return cls(
            module_id=module_id,
            revision=0,
            config=task_config_to_dict(default_task_config(module_id)),
            updated_at_utc=utc_now_text(),
        )

    @classmethod
    def from_dict(cls, value: object) -> TaskConfigRecord:
        if not isinstance(value, dict):
            raise TypeError("Task config record must be an object.")

        module_id = str(value["module_id"])
        revision = int(value["revision"])

        if revision < 0:
            raise ValueError("Task config revision cannot be negative.")

        config = task_config_from_dict(module_id, value["config"])
        return cls(
            module_id=module_id,
            revision=revision,
            config=task_config_to_dict(config),
            updated_at_utc=str(value["updated_at_utc"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "module_id": self.module_id,
            "revision": self.revision,
            "config": dict(self.config),
            "updated_at_utc": self.updated_at_utc,
        }


class TaskConfigConflict(RuntimeError):
    """An optimistic save used a stale task-config revision."""

    def __init__(self, current: TaskConfigRecord) -> None:
        super().__init__("Task config revision conflict.")
        self.current = current


class TaskConfigStore:
    """Atomically persist versioned settings in one task_configs.json file."""

    schema_version = "1.0"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self, module_id: str) -> TaskConfigRecord:
        normalized_module = module_id.strip()

        if normalized_module not in TASK_CONFIG_MODULE_IDS:
            raise KeyError(f"Unsupported task configuration module: {normalized_module}")

        document = self._load_document()
        value = document["modules"].get(normalized_module)

        if value is None:
            return TaskConfigRecord.default(normalized_module)

        record = TaskConfigRecord.from_dict(value)

        if record.module_id != normalized_module:
            raise ValueError("Task config record module does not match its key.")

        return record

    def save(
        self,
        module_id: str,
        config: object,
        *,
        expected_revision: int,
    ) -> TaskConfigRecord:
        normalized_module = module_id.strip()
        validated = task_config_from_dict(normalized_module, config)
        document = self._load_document()
        stored = document["modules"].get(normalized_module)
        current = (
            TaskConfigRecord.from_dict(stored)
            if stored is not None
            else TaskConfigRecord.default(normalized_module)
        )

        if current.revision != int(expected_revision):
            raise TaskConfigConflict(current)

        updated = TaskConfigRecord(
            module_id=normalized_module,
            revision=current.revision + 1,
            config=task_config_to_dict(validated),
            updated_at_utc=utc_now_text(),
        )
        document["modules"][normalized_module] = updated.to_dict()
        self._write(document)
        return updated

    def _load_document(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": self.schema_version, "modules": {}}

        payload = json.loads(self.path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict):
            raise ValueError("Task config root must be an object.")

        if payload.get("schema_version") != self.schema_version:
            raise ValueError("Unsupported task config schema.")

        modules = payload.get("modules")

        if not isinstance(modules, dict):
            raise ValueError("Task config modules must be an object.")

        return {
            "schema_version": self.schema_version,
            "modules": dict(modules),
        }

    def _write(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        try:
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)
