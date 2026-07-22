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


def _xlsx_question(
    number: int,
    question: str,
    option_1: object,
    option_2: object,
    *,
    correct_answer: object | None = None,
    question_type: BinaryQuestionType = BinaryQuestionType.INQUIRY,
) -> CommonQuestionTemplate:
    """Preserve one row from the administrator-provided question workbook."""
    first = str(option_1)
    second = str(option_2)
    correct = str(correct_answer) if correct_answer is not None else None

    if correct is not None:
        question_type = BinaryQuestionType.QUESTION_ANSWER

        if correct not in {first, second}:
            raise ValueError(f"Question {number} correct answer is not one of its options.")

    return CommonQuestionTemplate(
        template_id=f"xlsx-{number:03d}",
        question_type=question_type,
        question=question,
        option_1=first,
        option_2=second,
        correct_option_id=(
            "option_1" if correct == first else "option_2" if correct == second else None
        ),
        built_in=True,
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
    _xlsx_question(1, "你能听见我说话吗？", "能", "不能"),
    _xlsx_question(2, "你能看到我吗？", "能", "不能"),
    _xlsx_question(3, "你现在醒着吗？", "醒着", "不清楚"),
    _xlsx_question(4, "我现在在跟你说话对不对？", "对", "不对"),
    _xlsx_question(5, "你现在有感觉吗？", "有", "没有"),
    _xlsx_question(6, "你知道我在你身边吗？", "知道", "不知道"),
    _xlsx_question(7, "你现在是在床上吗？", "是", "不是"),
    _xlsx_question(8, "你现在难受吗？", "难受", "不难受"),
    _xlsx_question(9, "你现在是在家里还是医院？", "家里", "医院"),
    _xlsx_question(10, "现在是白天还是晚上？", "白天", "晚上"),
    _xlsx_question(11, "你现在是躺着还是坐着？", "躺着", "坐着"),
    _xlsx_question(12, "我是在你左边还是右边？", "左边", "右边"),
    _xlsx_question(13, "你现在疼不疼？", "疼", "不疼"),
    _xlsx_question(14, "你现在冷吗？", "冷", "不冷"),
    _xlsx_question(15, "你现在热吗？", "热", "不热"),
    _xlsx_question(16, "你现在想不想喝水？", "想", "不想"),
    _xlsx_question(17, "我现在是在照顾你对不对？", "对", "不对"),
    _xlsx_question(18, "我是你的家人还是陌生人？", "家人", "陌生人"),
    _xlsx_question(19, "你现在是一个人还是有人陪？一个人", "一个人", "有人陪"),
    _xlsx_question(20, "你现在需要休息还是活动？", "休息", "活动"),
    _xlsx_question(21, "你想不想说话？", "想", "不想"),
    _xlsx_question(22, "你现在是刚醒还是醒了一会？刚醒", "刚醒", "醒了一会"),
    _xlsx_question(23, "杯子是用来喝水的还是穿的？", "喝水", "穿", correct_answer="喝水"),
    _xlsx_question(24, "你现在有没有力气？", "有", "没有"),
    _xlsx_question(25, "你现在想不想翻身？", "想", "不想"),
    _xlsx_question(26, "你现在头清不清楚？", "清楚", "不清楚"),
    _xlsx_question(
        27,
        "你看我一眼",
        "做到",
        "做不到",
        question_type=BinaryQuestionType.OTHER,
    ),
    _xlsx_question(
        28,
        "你把头轻轻转向我这边",
        "做到",
        "做不到",
        question_type=BinaryQuestionType.OTHER,
    ),
    _xlsx_question(
        29,
        "你看一下天花板",
        "做到",
        "做不到",
        question_type=BinaryQuestionType.OTHER,
    ),
    _xlsx_question(
        30,
        "你动一下手指",
        "做到",
        "做不到",
        question_type=BinaryQuestionType.OTHER,
    ),
    _xlsx_question(
        31,
        "你把手轻轻抬一下",
        "做到",
        "做不到",
        question_type=BinaryQuestionType.OTHER,
    ),
    _xlsx_question(
        32,
        "你握一下我的手",
        "做到",
        "做不到",
        question_type=BinaryQuestionType.OTHER,
    ),
    _xlsx_question(
        33,
        "你点一下头",
        "做到",
        "做不到",
        question_type=BinaryQuestionType.OTHER,
    ),
    _xlsx_question(34, "现在是哪一年？", "2025年", "2026年", correct_answer="2026年"),
    _xlsx_question(35, "你在哪个城市？", "北京", "上海", correct_answer="北京"),
    _xlsx_question(36, "你在哪个城市？", "上海", "北京", correct_answer="北京"),
    _xlsx_question(37, "现在是白天还是黑夜？", "白天", "黑夜"),
    _xlsx_question(38, "这是什么地方？", "医院", "家里", correct_answer="医院"),
    _xlsx_question(39, "这是什么地方？", "家里", "医院", correct_answer="医院"),
    _xlsx_question(40, "草的颜色是什么？", "绿色", "红色", correct_answer="绿色"),
    _xlsx_question(41, "草的颜色是什么？", "红色", "绿色", correct_answer="绿色"),
    _xlsx_question(42, "天空通常是什么颜色？", "蓝色", "黑色", correct_answer="蓝色"),
    _xlsx_question(43, "天空通常是什么颜色？", "黑色", "蓝色", correct_answer="蓝色"),
    _xlsx_question(44, "你喝水时会用什么？", "鞋子", "杯子", correct_answer="杯子"),
    _xlsx_question(45, "你喝水时会用什么？", "杯子", "鞋子", correct_answer="杯子"),
    _xlsx_question(46, "太阳从哪边升起？", "西方", "东方", correct_answer="东方"),
    _xlsx_question(47, "鱼生活在？", "水里", "天上", correct_answer="水里"),
    _xlsx_question(48, "夏天通常是冷还是热？", "冷", "热", correct_answer="热"),
    _xlsx_question(49, "汽车是跑在什么上？", "公路", "河流", correct_answer="公路"),
    _xlsx_question(50, "早上吃的饭叫？", "早餐", "晚餐", correct_answer="早餐"),
    _xlsx_question(51, "医生在什么地方工作？", "学校", "医院", correct_answer="医院"),
    _xlsx_question(52, "一加二等于几？", "三", "五", correct_answer="三"),
    _xlsx_question(53, "二加二等于几？", "三", "四", correct_answer="四"),
    _xlsx_question(54, "一乘二等于几？", "三", "二", correct_answer="二"),
    _xlsx_question(55, "二乘三等于几？", "六", "八", correct_answer="六"),
    _xlsx_question(56, "下面哪一个不是动物？", "大象", "桌子", correct_answer="桌子"),
    _xlsx_question(57, "5比3大还是小？", "大", "小", correct_answer="大"),
    _xlsx_question(58, "春天之后是？", "冬天", "夏天", correct_answer="夏天"),
    _xlsx_question(59, "如果今天是星期五，明天是？", "星期六", "星期三", correct_answer="星期六"),
    _xlsx_question(60, "下面哪个可以飞？", "火车", "飞机", correct_answer="飞机"),
    _xlsx_question(61, "你想做眼动训练吗？", "想", "不想"),
    _xlsx_question(62, "你想做眼动训练吗？", "不想", "想"),
    _xlsx_question(63, "你有没有用心做吗？", "用心了", "没用心"),
    _xlsx_question(64, "你有没有用心做吗？", "没用心", "用心了"),
    _xlsx_question(65, "5乘以6等于多少", 30, 25, correct_answer=30),
    _xlsx_question(66, "5乘以6等于多少", 25, 30, correct_answer=30),
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

    def update(
        self,
        template_id: str,
        template: CommonQuestionTemplate,
    ) -> CommonQuestionTemplate:
        """Update one user template while preserving its identity."""
        normalized_id = template_id.strip()

        if not normalized_id:
            raise ValueError("template_id cannot be empty.")

        existing = list(self._load_user_templates())

        for index, item in enumerate(existing):
            if item.template_id != normalized_id:
                continue

            updated = CommonQuestionTemplate(
                template_id=normalized_id,
                question_type=template.question_type,
                question=template.question,
                option_1=template.option_1,
                option_2=template.option_2,
                correct_option_id=template.correct_option_id,
            )
            existing[index] = updated
            self._write_user_templates(existing)
            return updated

        if any(item.template_id == normalized_id for item in BUILT_IN_QUESTION_TEMPLATES):
            override = CommonQuestionTemplate(
                template_id=normalized_id,
                question_type=template.question_type,
                question=template.question,
                option_1=template.option_1,
                option_2=template.option_2,
                correct_option_id=template.correct_option_id,
            )
            existing.append(override)
            self._write_user_templates(existing)
            return override

        raise KeyError(f"Unknown question template: {normalized_id}")

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
