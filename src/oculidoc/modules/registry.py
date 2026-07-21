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
        "tracking_ball",
        "追踪球",
        "记录注视时长、注视比例和视线—目标轨迹匹配度。",
        "available",
    ),
    ModuleDefinition(
        "screen_keyboard",
        "屏幕打字",
        "通过大字声母、韵母、韵尾和可选声调分步停留输入拼音。",
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
    ),
    ModuleDefinition(
        "multiple_choice",
        "多选项问答",
        "显示多个文字选项并保存回答、反应时间和评分。",
    ),
    ModuleDefinition(
        "image_choice",
        "语音图片选择",
        "播报图片内容，从左右图片中选择正确目标。",
    ),
    ModuleDefinition(
        "instruction_fixation",
        "随指令注视",
        "根据语音或文字指令观察指定区域和目标。",
    ),
)
