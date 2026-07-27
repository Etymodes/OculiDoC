"""Registry of OculiDoC experimental modules."""

from dataclasses import dataclass
from typing import Literal

ModuleStatus = Literal["planned", "prototype", "available"]


@dataclass(frozen=True, slots=True)
class ModuleDefinition:
    module_id: str
    title: str
    description: str
    status: ModuleStatus = "planned"


DEFAULT_MODULES: tuple[ModuleDefinition, ...] = (
    ModuleDefinition(
        "eye_observation",
        "眼动采集与复核",
        "为当前患者创建实验会话，采集摄像头画面并人工复核双眼区域。",
        "available",
    ),
    ModuleDefinition(
        "visual_preference",
        "视觉偏好",
        "成对换边呈现图片，分别记录图片关注与固定左右侧偏。",
        "available",
    ),
    ModuleDefinition(
        "tracking_ball",
        "追踪球",
        "记录注视时长、注视比例和视线—目标轨迹匹配度。",
        "available",
    ),
    ModuleDefinition(
        "gaze_games",
        "眼动游戏",
        "在点亮花园和视觉寻宝之间选择，并记录注视联动或视觉搜索表现。",
        "available",
    ),
    ModuleDefinition(
        "instruction_fixation",
        "随指令注视",
        "根据语音或文字指令观察指定区域和目标。",
        "available",
    ),
    ModuleDefinition(
        "image_choice",
        "语音图片选择",
        "播报图片内容，从左右图片中选择正确目标。",
        "available",
    ),
    ModuleDefinition(
        "binary_horizontal",
        "左右二分问答",
        "左右排列两个答案，支持停留确认和评分。",
        "available",
    ),
    ModuleDefinition(
        "binary_vertical",
        "上下二分问答",
        "上下排列两个答案，支持停留确认和评分。",
        "available",
    ),
    ModuleDefinition(
        "multiple_choice",
        "多选项问答",
        "显示 2–6 个文字选项，支持多选、再次选择取消和手动结束。",
        "available",
    ),
    ModuleDefinition(
        "screen_keyboard",
        "屏幕打字",
        "支持高频需求词句直观直选，以及分步拼音进阶输入。",
        "available",
    ),
)
