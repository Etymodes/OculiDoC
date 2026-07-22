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
    category: str = "自定义"
    built_in: bool = False

    def __post_init__(self) -> None:
        normalized_id = self.template_id.strip()
        normalized_question = self.question.strip()
        normalized_option_1 = self.option_1.strip()
        normalized_option_2 = self.option_2.strip()
        normalized_category = self.category.strip()
        normalized_type = BinaryQuestionType(self.question_type)

        if not normalized_id:
            raise ValueError("template_id cannot be empty.")

        if not normalized_category:
            raise ValueError("category cannot be empty.")

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
        object.__setattr__(self, "category", normalized_category)

    @classmethod
    def create(
        cls,
        *,
        question_type: BinaryQuestionType,
        question: str,
        option_1: str,
        option_2: str,
        correct_option_id: str | None = None,
        category: str = "自定义",
    ) -> CommonQuestionTemplate:
        return cls(
            template_id=str(uuid4()),
            question_type=question_type,
            question=question,
            option_1=option_1,
            option_2=option_2,
            correct_option_id=correct_option_id,
            category=category,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "question_type": self.question_type.value,
            "question": self.question,
            "option_1": self.option_1,
            "option_2": self.option_2,
            "correct_option_id": self.correct_option_id,
            "category": self.category,
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
            category=str(value.get("category", "自定义")),
        )


QUESTION_CATEGORY_DATA: tuple[
    tuple[str, str, BinaryQuestionType, tuple[tuple[str, str, str, str | None], ...]], ...
] = (
    (
        "fact",
        "基础事实",
        BinaryQuestionType.YES_NO,
        (
            ("北京是中国的首都吗？", "是", "否", "option_1"),
            ("太阳是恒星吗？", "是", "否", "option_1"),
            ("水在常温下通常是液体吗？", "是", "否", "option_1"),
            ("一年有十二个月吗？", "是", "否", "option_1"),
            ("一周有七天吗？", "是", "否", "option_1"),
            ("人通常有两只眼睛吗？", "是", "否", "option_1"),
            ("鱼通常生活在水里吗？", "是", "否", "option_1"),
            ("医生通常在医院工作吗？", "是", "否", "option_1"),
            ("飞机可以在空中飞行吗？", "是", "否", "option_1"),
            ("汽车通常在公路上行驶吗？", "是", "否", "option_1"),
        ),
    ),
    (
        "function",
        "物品功能",
        BinaryQuestionType.QUESTION_ANSWER,
        (
            ("杯子通常用来做什么？", "喝水", "穿衣", "option_1"),
            ("鞋子通常用来做什么？", "穿在脚上", "盛水", "option_1"),
            ("筷子通常用来做什么？", "吃饭", "写字", "option_1"),
            ("枕头通常用来做什么？", "睡觉时垫头", "盛水", "option_1"),
            ("剪刀通常用来做什么？", "剪东西", "煮饭", "option_1"),
            ("雨伞通常用来做什么？", "挡雨", "照明", "option_1"),
            ("牙刷通常用来做什么？", "刷牙", "写字", "option_1"),
            ("电话通常用来做什么？", "通话", "切菜", "option_1"),
            ("钥匙通常用来做什么？", "开锁", "喝水", "option_1"),
            ("毛巾通常用来做什么？", "擦拭", "开门", "option_1"),
        ),
    ),
    (
        "category",
        "类别辨别",
        BinaryQuestionType.QUESTION_ANSWER,
        (
            ("下面哪一个不是动物？", "大象", "桌子", "option_2"),
            ("下面哪一个是水果？", "苹果", "汽车", "option_1"),
            ("下面哪一个是交通工具？", "公交车", "香蕉", "option_1"),
            ("下面哪一个是衣物？", "上衣", "杯子", "option_1"),
            ("下面哪一个是家具？", "床", "小鸟", "option_1"),
            ("下面哪一个是食物？", "米饭", "铅笔", "option_1"),
            ("下面哪一个不是水果？", "梨", "椅子", "option_2"),
            ("下面哪一个是动物？", "猫", "飞机", "option_1"),
            ("下面哪一个是饮品？", "牛奶", "鞋子", "option_1"),
            ("下面哪一个是书写工具？", "铅笔", "勺子", "option_1"),
        ),
    ),
    (
        "attribute",
        "属性辨别",
        BinaryQuestionType.QUESTION_ANSWER,
        (
            ("草通常是什么颜色？", "绿色", "红色", "option_1"),
            ("晴朗的天空通常是什么颜色？", "蓝色", "黑色", "option_1"),
            ("雪通常是什么颜色？", "白色", "紫色", "option_1"),
            ("火通常是热的还是冷的？", "热的", "冷的", "option_1"),
            ("冰通常是冷的还是热的？", "冷的", "热的", "option_1"),
            ("大象通常是大还是小？", "大", "小", "option_1"),
            ("羽毛通常是轻还是重？", "轻", "重", "option_1"),
            ("柠檬通常是什么味道？", "酸", "咸", "option_1"),
            ("糖通常是什么味道？", "甜", "苦", "option_1"),
            ("夜晚通常是明亮还是黑暗？", "黑暗", "明亮", "option_1"),
        ),
    ),
    (
        "quantity",
        "基础数量",
        BinaryQuestionType.QUESTION_ANSWER,
        (
            ("一加一等于几？", "二", "三", "option_1"),
            ("一加二等于几？", "三", "五", "option_1"),
            ("二加二等于几？", "四", "三", "option_1"),
            ("二乘三等于几？", "六", "八", "option_1"),
            ("五乘六等于几？", "三十", "二十五", "option_1"),
            ("五比三大还是小？", "大", "小", "option_1"),
            ("一只手通常有几根手指？", "五根", "八根", "option_1"),
            ("人通常有几只眼睛？", "两只", "四只", "option_1"),
            ("十减三等于几？", "七", "六", "option_1"),
            ("三加四等于几？", "七", "九", "option_1"),
        ),
    ),
    (
        "sequence",
        "自然与顺序",
        BinaryQuestionType.QUESTION_ANSWER,
        (
            ("春天之后通常是什么季节？", "夏天", "冬天", "option_1"),
            ("如果今天是星期五，明天是星期几？", "星期六", "星期三", "option_1"),
            ("早晨之后通常是下午还是深夜？", "下午", "深夜", "option_1"),
            ("星期一之后是星期几？", "星期二", "星期日", "option_1"),
            ("一、二之后通常是几？", "三", "五", "option_1"),
            ("早餐之后通常是午餐还是晚餐？", "午餐", "晚餐", "option_1"),
            ("秋天之后通常是什么季节？", "冬天", "夏天", "option_1"),
            ("白天之后通常是夜晚还是清晨？", "夜晚", "清晨", "option_1"),
            ("一年中的第一个月是一月还是二月？", "一月", "二月", "option_1"),
            ("一年通常有四个季节还是三个季节？", "四个", "三个", "option_1"),
        ),
    ),
    (
        "sensory",
        "感知与交流",
        BinaryQuestionType.INQUIRY,
        (
            ("你现在能听见我说话吗？", "能", "不能", None),
            ("你现在能看清屏幕吗？", "能", "不能", None),
            ("你现在是清醒的吗？", "是", "不确定", None),
            ("你知道现在有人在和你说话吗？", "知道", "不知道", None),
            ("你现在头脑清楚吗？", "清楚", "不清楚", None),
            ("你现在想表达自己的意思吗？", "想", "不想", None),
            ("你现在用目光回答容易吗？", "容易", "困难", None),
            ("现在说话的声音大小合适吗？", "合适", "太小", None),
            ("屏幕亮度让你舒服吗？", "舒服", "不舒服", None),
            ("你需要我重复这个问题吗？", "需要", "不需要", None),
        ),
    ),
    (
        "orientation",
        "现场定向",
        BinaryQuestionType.INQUIRY,
        (
            ("你现在是在床上吗？", "是", "不是", None),
            ("你现在是在医院还是家里？", "医院", "家里", None),
            ("现在是白天还是晚上？", "白天", "晚上", None),
            ("你现在是躺着还是坐着？", "躺着", "坐着", None),
            ("我现在是在你左边还是右边？", "左边", "右边", None),
            ("你现在是刚醒还是醒了一会儿？", "刚醒", "醒了一会儿", None),
            ("你认识现在照护你的人吗？", "认识", "不确定", None),
            ("你知道今天的日期吗？", "知道", "不知道", None),
            ("你知道自己现在在哪个城市吗？", "知道", "不知道", None),
            ("你知道自己为什么在这里吗？", "知道", "不知道", None),
        ),
    ),
    (
        "symptom",
        "疼痛与不适",
        BinaryQuestionType.INQUIRY,
        (
            ("你现在难受吗？", "难受", "不难受", None),
            ("你现在疼吗？", "疼", "不疼", None),
            ("你现在觉得冷吗？", "冷", "不冷", None),
            ("你现在觉得热吗？", "热", "不热", None),
            ("你现在呼吸顺畅吗？", "顺畅", "不顺畅", None),
            ("你现在口渴或口干吗？", "是", "否", None),
            ("你现在恶心吗？", "恶心", "不恶心", None),
            ("你现在头痛吗？", "头痛", "不头痛", None),
            ("你现在身体有麻木感吗？", "有", "没有", None),
            ("你现在觉得疲倦吗？", "疲倦", "不疲倦", None),
        ),
    ),
    (
        "care",
        "护理需求",
        BinaryQuestionType.INQUIRY,
        (
            ("你现在想喝水吗？", "想", "不想", None),
            ("你现在需要吸痰吗？", "需要", "不需要", None),
            ("你现在想翻身吗？", "想", "不想", None),
            ("你需要调整枕头吗？", "需要", "不需要", None),
            ("你现在需要如厕帮助吗？", "需要", "不需要", None),
            ("你需要清洁口腔吗？", "需要", "不需要", None),
            ("你需要擦脸吗？", "需要", "不需要", None),
            ("你需要加盖被子吗？", "需要", "不需要", None),
            ("你需要我呼叫医护人员吗？", "需要", "不需要", None),
            ("你需要暂停检查吗？", "需要", "不需要", None),
        ),
    ),
    (
        "activity",
        "康复与活动",
        BinaryQuestionType.INQUIRY,
        (
            ("你现在想休息还是活动？", "休息", "活动", None),
            ("你现在想睡觉还是保持清醒？", "睡觉", "保持清醒", None),
            ("你现在想做眼动训练吗？", "想", "不想", None),
            ("你现在想做康复训练吗？", "想", "不想", None),
            ("你现在想坐起来还是继续躺着？", "坐起来", "继续躺着", None),
            ("你现在想活动上肢还是下肢？", "上肢", "下肢", None),
            ("你现在想听音乐还是保持安静？", "听音乐", "保持安静", None),
            ("你现在想看屏幕还是闭眼休息？", "看屏幕", "闭眼休息", None),
            ("你想继续当前任务还是停止？", "继续", "停止", None),
            ("你现在想自己选择接下来的活动吗？", "想", "不想", None),
        ),
    ),
    (
        "emotion",
        "情绪与陪伴",
        BinaryQuestionType.INQUIRY,
        (
            ("你现在感到舒服吗？", "舒服", "不舒服", None),
            ("你现在是一个人还是有人陪伴？", "一个人", "有人陪伴", None),
            ("你现在心情平静还是紧张？", "平静", "紧张", None),
            ("你现在感到开心还是难过？", "开心", "难过", None),
            ("你希望家人陪在身边吗？", "希望", "不希望", None),
            ("你希望有人和你说话吗？", "希望", "不希望", None),
            ("你现在希望周围安静吗？", "希望", "不希望", None),
            ("你认得现在陪伴你的人吗？", "认得", "不确定", None),
            ("你现在感到孤单吗？", "孤单", "不孤单", None),
            ("你现在需要安慰吗？", "需要", "不需要", None),
        ),
    ),
)


BUILT_IN_QUESTION_TEMPLATES = tuple(
    CommonQuestionTemplate(
        template_id=f"{prefix}-{index:02d}",
        question_type=question_type,
        question=question,
        option_1=option_1,
        option_2=option_2,
        correct_option_id=correct_option_id,
        category=category,
        built_in=True,
    )
    for prefix, category, question_type, questions in QUESTION_CATEGORY_DATA
    for index, (question, option_1, option_2, correct_option_id) in enumerate(
        questions,
        start=1,
    )
)


FIXED_BINARY_QUESTION_FORMS: dict[int, tuple[str, ...]] = {
    6: (
        "fact-01",
        "function-01",
        "quantity-01",
        "symptom-02",
        "care-02",
        "emotion-06",
    ),
    8: (
        "fact-04",
        "function-07",
        "category-01",
        "quantity-02",
        "symptom-01",
        "care-03",
        "activity-03",
        "emotion-05",
    ),
    10: (
        "fact-01",
        "function-01",
        "category-01",
        "attribute-01",
        "quantity-01",
        "sensory-01",
        "symptom-02",
        "care-02",
        "activity-04",
        "emotion-06",
    ),
}


_LEGACY_CANONICAL_IDS = (
    "sensory-01",
    "sensory-02",
    "sensory-03",
    "sensory-04",
    "sensory-05",
    "sensory-04",
    "orientation-01",
    "symptom-01",
    "orientation-02",
    "orientation-03",
    "orientation-04",
    "orientation-05",
    "symptom-02",
    "symptom-03",
    "symptom-04",
    "care-01",
    "care-09",
    "emotion-08",
    "emotion-02",
    "activity-01",
    "sensory-06",
    "orientation-06",
    "function-01",
    "symptom-10",
    "care-03",
    "sensory-05",
    "sensory-02",
    "activity-05",
    "sensory-02",
    "activity-06",
    "activity-06",
    "activity-06",
    "activity-06",
    "orientation-08",
    "orientation-09",
    "orientation-09",
    "orientation-03",
    "orientation-02",
    "orientation-02",
    "attribute-01",
    "attribute-01",
    "attribute-02",
    "attribute-02",
    "function-01",
    "function-01",
    "fact-02",
    "fact-07",
    "attribute-04",
    "fact-10",
    "sequence-06",
    "fact-08",
    "quantity-02",
    "quantity-03",
    "quantity-01",
    "quantity-04",
    "category-01",
    "quantity-06",
    "sequence-01",
    "sequence-02",
    "fact-09",
    "activity-03",
    "activity-03",
    "activity-09",
    "activity-09",
    "quantity-05",
    "quantity-05",
)

LEGACY_QUESTION_ALIASES = {
    **{
        f"xlsx-{index:03d}": canonical_id
        for index, canonical_id in enumerate(_LEGACY_CANONICAL_IDS, start=1)
    },
    "builtin-comfort": "emotion-01",
    "builtin-hearing": "sensory-01",
    "builtin-capital": "fact-01",
    "builtin-arithmetic": "quantity-01",
}


class CommonQuestionStore:
    """Load and atomically save user question templates."""

    schema_version = "1.1"

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
                category=template.category,
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
                category=template.category,
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
