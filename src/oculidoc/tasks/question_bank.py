"""Persistent common-question templates for gaze tasks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


class BinaryQuestionType(StrEnum):
    """Clinical intent of a two-option question."""

    YES_NO = "yes_no"
    QUESTION_ANSWER = "question_answer"
    INQUIRY = "inquiry"
    OTHER = "other"

    @property
    def is_scored(self) -> bool:
        return self in {
            BinaryQuestionType.YES_NO,
            BinaryQuestionType.QUESTION_ANSWER,
        }

    @property
    def display_label(self) -> str:
        return {
            BinaryQuestionType.YES_NO: "是否题",
            BinaryQuestionType.QUESTION_ANSWER: "问答题",
            BinaryQuestionType.INQUIRY: "询问题",
            BinaryQuestionType.OTHER: "其他",
        }[self]


@dataclass(frozen=True, slots=True)
class CommonQuestionTemplate:
    """One reusable two-option question template."""

    template_id: str
    question_type: BinaryQuestionType
    question: str
    option_1: str
    option_2: str
    correct_option_id: str | None = None
    built_in: bool = False

    def __post_init__(self) -> None:
        normalized_id = self.template_id.strip()
        normalized_question = self.question.strip()
        normalized_option_1 = self.option_1.strip()
        normalized_option_2 = self.option_2.strip()
        normalized_type = BinaryQuestionType(self.question_type)

        if not normalized_id:
            raise ValueError("template_id cannot be empty.")

        for name, value in (
            ("question", normalized_question),
            ("option_1", normalized_option_1),
            ("option_2", normalized_option_2),
        ):
            if not value:
                raise ValueError(f"{name} cannot be empty.")

        correct_option_id = self.correct_option_id

        if normalized_type.is_scored:
            correct_option_id = correct_option_id or "option_1"

            if correct_option_id not in {"option_1", "option_2"}:
                raise ValueError("A scored template must identify option_1 or option_2 as correct.")
        else:
            correct_option_id = None

        object.__setattr__(self, "template_id", normalized_id)
        object.__setattr__(self, "question_type", normalized_type)
        object.__setattr__(self, "question", normalized_question)
        object.__setattr__(self, "option_1", normalized_option_1)
        object.__setattr__(self, "option_2", normalized_option_2)
        object.__setattr__(self, "correct_option_id", correct_option_id)

    @classmethod
    def create(
        cls,
        *,
        question_type: BinaryQuestionType,
        question: str,
        option_1: str,
        option_2: str,
        correct_option_id: str | None = None,
    ) -> CommonQuestionTemplate:
        return cls(
            template_id=str(uuid4()),
            question_type=question_type,
            question=question,
            option_1=option_1,
            option_2=option_2,
            correct_option_id=correct_option_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "question_type": self.question_type.value,
            "question": self.question,
            "option_1": self.option_1,
            "option_2": self.option_2,
            "correct_option_id": self.correct_option_id,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> CommonQuestionTemplate:
        if not isinstance(value, dict):
            raise TypeError("Question template must be an object.")

        return cls(
            template_id=str(value["template_id"]),
            question_type=BinaryQuestionType(str(value["question_type"])),
            question=str(value["question"]),
            option_1=str(value["option_1"]),
            option_2=str(value["option_2"]),
            correct_option_id=(
                str(value["correct_option_id"])
                if value.get("correct_option_id") is not None
                else None
            ),
        )


BUILT_IN_QUESTION_TEMPLATES = (
    CommonQuestionTemplate(
        template_id="builtin-comfort",
        question_type=BinaryQuestionType.INQUIRY,
        question="你现在感到舒服吗？",
        option_1="是",
        option_2="否",
        built_in=True,
    ),
    CommonQuestionTemplate(
        template_id="builtin-hearing",
        question_type=BinaryQuestionType.INQUIRY,
        question="你能听到我说话吗？",
        option_1="能",
        option_2="不能",
        built_in=True,
    ),
    CommonQuestionTemplate(
        template_id="builtin-capital",
        question_type=BinaryQuestionType.YES_NO,
        question="北京是中国的首都吗？",
        option_1="是",
        option_2="否",
        correct_option_id="option_1",
        built_in=True,
    ),
    CommonQuestionTemplate(
        template_id="builtin-arithmetic",
        question_type=BinaryQuestionType.QUESTION_ANSWER,
        question="一加一等于几？",
        option_1="二",
        option_2="三",
        correct_option_id="option_1",
        built_in=True,
    ),
)


class CommonQuestionStore:
    """Load and atomically save user question templates."""

    schema_version = "1.0"

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path).expanduser().resolve()

    def _load_user_templates(
        self,
    ) -> tuple[CommonQuestionTemplate, ...]:
        if not self.path.is_file():
            return ()

        payload = json.loads(self.path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict):
            raise ValueError("Question bank root must be an object.")

        raw_questions = payload.get("questions", [])

        if not isinstance(raw_questions, list):
            raise ValueError("Question bank questions must be a list.")

        return tuple(CommonQuestionTemplate.from_dict(item) for item in raw_questions)

    def load(
        self,
    ) -> tuple[CommonQuestionTemplate, ...]:
        """Return built-in and user templates."""

        combined = {template.template_id: template for template in BUILT_IN_QUESTION_TEMPLATES}

        for template in self._load_user_templates():
            combined[template.template_id] = template

        return tuple(combined.values())

    def add(
        self,
        template: CommonQuestionTemplate,
    ) -> CommonQuestionTemplate:
        """Add or replace a matching user template."""

        existing = list(self._load_user_templates())
        identity = (
            template.question_type,
            template.question.casefold(),
        )
        replaced = False

        for index, item in enumerate(existing):
            item_identity = (
                item.question_type,
                item.question.casefold(),
            )

            if item_identity == identity:
                existing[index] = template
                replaced = True
                break

        if not replaced:
            existing.append(template)

        self._write_user_templates(existing)
        return template

    def _write_user_templates(
        self,
        templates: list[CommonQuestionTemplate],
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "questions": [template.to_dict() for template in templates if not template.built_in],
        }

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
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        try:
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)
