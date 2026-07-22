"""Configurable multi-option gaze task with toggleable selections."""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass
from time import monotonic_ns

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.devices.contracts import EyeTrackerSample

MULTIPLE_CHOICE_LAYOUTS = frozenset({"grid", "ring"})
MULTIPLE_CHOICE_GRID_SHAPES = {
    "2x2": (2, 2),
    "2x3": (2, 3),
    "2x4": (2, 4),
    "3x2": (3, 2),
    "3x3": (3, 3),
    "3x4": (3, 4),
}


@dataclass(frozen=True, slots=True)
class MultipleChoiceConfig:
    question: str = "请选择符合您意思的选项"
    option_count: int = 4
    option_1: str = "是"
    option_2: str = "否"
    option_3: str = "不确定"
    option_4: str = "不知道"
    option_5: str = "需要帮助"
    option_6: str = "暂不回答"
    option_7: str = "选项 7"
    option_8: str = "选项 8"
    option_9: str = "选项 9"
    option_10: str = "选项 10"
    option_11: str = "选项 11"
    option_12: str = "选项 12"
    layout: str = "grid"
    grid_shape: str = "auto"
    dwell_time_ms: int = 900
    duration_seconds: int = 600
    question_font_size_pt: int = 36
    option_font_size_pt: int = 34
    randomize_positions: bool = True
    randomization_seed: int | None = None
    template_id: str | None = None

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("question cannot be empty.")

        if isinstance(self.option_count, bool) or not 2 <= self.option_count <= 12:
            raise ValueError("option_count must be between 2 and 12.")

        if self.layout not in MULTIPLE_CHOICE_LAYOUTS:
            raise ValueError("layout must be grid or ring.")

        if self.grid_shape != "auto" and self.grid_shape not in MULTIPLE_CHOICE_GRID_SHAPES:
            raise ValueError("grid_shape must be auto or one of the supported row/column shapes.")

        if self.layout == "ring" and self.option_count > 6:
            raise ValueError("Ring layout supports at most 6 options.")

        if self.layout == "grid":
            rows, columns = self.resolved_grid_shape

            if self.option_count > rows * columns:
                raise ValueError("Selected grid_shape does not have enough option cells.")

        if not 250 <= self.dwell_time_ms <= 10_000:
            raise ValueError("dwell_time_ms must be between 250 and 10000.")

        if not 5 <= self.duration_seconds <= 3_600:
            raise ValueError("duration_seconds must be between 5 and 3600.")

        for name in ("question_font_size_pt", "option_font_size_pt"):
            if not 20 <= getattr(self, name) <= 120:
                raise ValueError(f"{name} must be between 20 and 120.")

        if not isinstance(self.randomize_positions, bool):
            raise TypeError("randomize_positions must be a boolean.")

        if self.randomization_seed is not None and (
            not isinstance(self.randomization_seed, int)
            or isinstance(self.randomization_seed, bool)
        ):
            raise TypeError("randomization_seed must be an integer or null.")

        if self.template_id is not None and not self.template_id.strip():
            raise ValueError("template_id cannot be empty when provided.")

        if any(not label.strip() for _, label in self.options):
            raise ValueError("Enabled option labels cannot be empty.")

    @property
    def options(self) -> tuple[tuple[str, str], ...]:
        values = (
            self.option_1,
            self.option_2,
            self.option_3,
            self.option_4,
            self.option_5,
            self.option_6,
            self.option_7,
            self.option_8,
            self.option_9,
            self.option_10,
            self.option_11,
            self.option_12,
        )
        return tuple(
            (f"option_{index}", values[index - 1].strip())
            for index in range(1, self.option_count + 1)
        )

    @property
    def resolved_grid_shape(self) -> tuple[int, int]:
        if self.grid_shape != "auto":
            return MULTIPLE_CHOICE_GRID_SHAPES[self.grid_shape]

        if self.option_count <= 4:
            return 2, 2

        if self.option_count <= 6:
            return 2, 3

        if self.option_count <= 8:
            return 2, 4

        if self.option_count == 9:
            return 3, 3

        return 3, 4

    @property
    def resolved_grid_shape_name(self) -> str:
        rows, columns = self.resolved_grid_shape
        return f"{rows}x{columns}"


@dataclass(frozen=True, slots=True)
class MultipleChoiceTemplate:
    template_id: str
    category: str
    question: str
    options: tuple[str, ...]
    grid_shape: str


BUILT_IN_MULTIPLE_CHOICE_TEMPLATES = (
    MultipleChoiceTemplate(
        "immediate-care",
        "即时护理",
        "你现在最需要哪项帮助？（可多选；选择不会自动执行护理）",
        ("喝水", "吸痰", "翻身", "调整枕头", "如厕", "擦脸", "呼叫医护", "暂停任务"),
        "2x4",
    ),
    MultipleChoiceTemplate(
        "current-action",
        "当前意愿",
        "你现在想做什么？（可多选）",
        ("睡觉", "吸痰", "康复训练", "眼动训练", "听音乐", "看视频", "和家人交流", "安静休息"),
        "2x4",
    ),
    MultipleChoiceTemplate(
        "discomfort-location",
        "不适部位",
        "你哪里不舒服？（可多选）",
        (
            "头",
            "眼睛",
            "口腔",
            "咽喉",
            "胸部",
            "腹部",
            "背部",
            "左臂",
            "右臂",
            "左腿",
            "右腿",
            "全身",
        ),
        "3x4",
    ),
    MultipleChoiceTemplate(
        "position-adjustment",
        "体位调整",
        "你希望怎样调整体位？（可多选）",
        ("左侧卧", "右侧卧", "平躺", "坐起", "抬高床头", "保持不变"),
        "3x2",
    ),
    MultipleChoiceTemplate(
        "drink-choice",
        "饮品选择",
        "你想喝什么？（须由医护确认能否饮用）",
        ("温水", "凉水", "牛奶", "果汁", "茶", "暂时不喝"),
        "2x3",
    ),
    MultipleChoiceTemplate(
        "fruit-choice",
        "水果选择",
        "你想选择哪些水果？（可多选）",
        ("苹果", "香蕉", "橙子", "葡萄", "西瓜", "梨", "桃", "草莓"),
        "2x4",
    ),
    MultipleChoiceTemplate(
        "city-choice",
        "城市选择",
        "请选择你想表达的城市（可多选）",
        (
            "北京",
            "上海",
            "广州",
            "深圳",
            "成都",
            "重庆",
            "武汉",
            "西安",
            "南京",
            "杭州",
            "桂林",
            "南宁",
        ),
        "3x4",
    ),
    MultipleChoiceTemplate(
        "transport-choice",
        "交通工具",
        "请选择交通工具（可多选）",
        ("步行", "轮椅", "自行车", "公交车", "地铁", "汽车", "火车", "飞机"),
        "2x4",
    ),
    MultipleChoiceTemplate(
        "rehabilitation-choice",
        "康复训练",
        "你想做哪些康复活动？（可多选）",
        ("眼动训练", "上肢训练", "下肢训练", "坐起训练", "语言训练", "今天暂停"),
        "3x2",
    ),
    MultipleChoiceTemplate(
        "game-choice",
        "游戏活动",
        "你想玩什么游戏或活动？（可多选）",
        ("看图片", "猜颜色", "数字题", "听音乐", "看视频", "讲故事", "棋类", "拼图", "球类"),
        "3x3",
    ),
    MultipleChoiceTemplate(
        "leisure-choice",
        "休闲偏好",
        "你现在想怎样放松？（可多选）",
        ("听音乐", "看电视", "听故事", "看照片", "闭眼休息", "保持安静"),
        "2x3",
    ),
    MultipleChoiceTemplate(
        "company-choice",
        "陪伴交流",
        "你希望谁来陪伴或与你交流？（可多选）",
        ("家人", "朋友", "医生", "护士", "康复师", "暂时独处"),
        "3x2",
    ),
)


class MultipleChoiceTask(QWidget):
    """Toggle two to six independent options by gaze dwell."""

    selection_changed = Signal(str, bool)

    def __init__(
        self,
        config: MultipleChoiceConfig,
        *,
        allow_mouse_fallback: bool = True,
    ) -> None:
        super().__init__()
        self.config = config
        self.allow_mouse_fallback = allow_mouse_fallback
        self.randomization_seed = (
            config.randomization_seed
            if config.randomization_seed is not None
            else secrets.randbits(63)
        )
        displayed_options = list(config.options)

        if config.randomize_positions:
            random.Random(self.randomization_seed).shuffle(displayed_options)

        self._displayed_options = tuple(displayed_options)
        self._labels = dict(config.options)
        self._buttons: dict[str, QPushButton] = {}
        self._position_by_option: dict[str, int] = {}
        self._selected_option_ids: set[str] = set()
        self._active_option_id: str | None = None
        self._latched_option_id: str | None = None
        self._dwell_ms = 0.0
        self._last_timestamp_ns: int | None = None
        self._started_at_ns: int | None = None
        self._first_selection_ns: int | None = None
        self._toggle_count = 0
        self._recording_events: list[dict[str, object]] = []
        self._final_event_recorded = False

        self.setObjectName("multipleChoiceTask")
        self.setMinimumSize(960, 640)
        self.setStyleSheet(
            """
            QWidget#multipleChoiceTask {
                background: #f8fcff;
                color: #12304a;
                font-family: "Microsoft YaHei UI";
            }
            QWidget#multipleChoiceOptions { background: #f8fcff; }
            QLabel#multipleChoiceQuestion { color: #102f4b; font-weight: 800; padding: 12px; }
            QLabel#multipleChoiceSummary { color: #245b78; font-weight: 700; padding: 4px 12px; }
            QLabel#multipleChoiceCenter { color: #52728a; font-weight: 700; }
            QPushButton#multipleChoiceOption {
                background: #ffffff;
                color: #12304a;
                border: 5px solid #78add0;
                border-radius: 22px;
                font-weight: 800;
                padding: 12px;
            }
            QPushButton#multipleChoiceOption[active="true"] {
                background: #fff4b8;
                border-color: #e69700;
            }
            QPushButton#multipleChoiceOption[selected="true"] {
                background: #d9f4df;
                border-color: #2e8b57;
                color: #14532d;
            }
            QProgressBar {
                min-height: 26px;
                border: 2px solid #4e8db8;
                border-radius: 9px;
                background: white;
                text-align: center;
                color: #17324d;
                font-size: 15px;
                font-weight: 700;
            }
            QProgressBar::chunk { background: #f0ad2c; border-radius: 7px; }
            """
        )

        self.question_label = QLabel(config.question)
        self.question_label.setObjectName("multipleChoiceQuestion")
        self.question_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.question_label.setWordWrap(True)
        self.question_label.setMaximumHeight(82)
        self.question_label.setFont(self._font(config.question_font_size_pt))
        self.question_labels = [self.question_label]

        self.summary_label = QLabel("尚未选择 · 可选择多个，再次选择可取消")
        self.summary_label.setObjectName("multipleChoiceSummary")
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary_label.setWordWrap(True)
        self.summary_label.setMaximumHeight(70)
        self.summary_label.setFont(self._font(max(20, config.option_font_size_pt - 8)))

        self.options_widget = QWidget()
        self.options_widget.setObjectName("multipleChoiceOptions")
        self.options_layout = QGridLayout(self.options_widget)
        self.options_layout.setContentsMargins(18, 2, 18, 8)
        self.options_layout.setSpacing(12)
        self._render_options()

        self.dwell_progress = QProgressBar()
        self.dwell_progress.setRange(0, config.dwell_time_ms)
        self.dwell_progress.setFormat("请持续注视选项 · %p%")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 10)
        root.setSpacing(2)

        if config.layout == "ring":
            root.addWidget(self.question_label)

        root.addWidget(self.summary_label)
        root.addWidget(self.options_widget, 1)
        root.addWidget(self.dwell_progress)

        if not allow_mouse_fallback:
            self.setCursor(Qt.CursorShape.BlankCursor)

        self._refresh_selection()

    @staticmethod
    def _font(size: int) -> QFont:
        font = QFont("Microsoft YaHei UI", size)
        font.setBold(True)
        return font

    @property
    def selected_option_ids(self) -> tuple[str, ...]:
        return tuple(
            option_id
            for option_id, _ in self.config.options
            if option_id in self._selected_option_ids
        )

    @property
    def selected_answers(self) -> tuple[str, ...]:
        return tuple(self._labels[option_id] for option_id in self.selected_option_ids)

    @property
    def patient_display_text(self) -> str:
        selected = "、".join(self.selected_answers) or "尚未选择"
        return f"多选项问答\n{self.config.question}\n已选择：{selected}"

    def _render_options(self) -> None:
        if self.config.layout == "ring":
            center = QLabel("可多选\n再次选择可取消")
            center.setObjectName("multipleChoiceCenter")
            center.setAlignment(Qt.AlignmentFlag.AlignCenter)
            center.setFont(self._font(20))
            self.options_layout.addWidget(center, 1, 1)
            positions = self._ring_positions(len(self._displayed_options))
            option_minimum_height = 150
            row_count = 3
            column_count = 3
        else:
            rows, columns = self.config.resolved_grid_shape
            positions = tuple(
                ((index // columns) * 2, index % columns)
                for index in range(len(self._displayed_options))
            )
            option_minimum_height = 180 if rows == 2 else 110
            row_count = rows * 2 - 1
            column_count = columns

            for separator_index in range(rows - 1):
                question_label = (
                    self.question_label if separator_index == 0 else QLabel(self.config.question)
                )

                if separator_index:
                    question_label.setObjectName("multipleChoiceQuestion")
                    question_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    question_label.setWordWrap(True)
                    question_label.setMaximumHeight(82)
                    question_label.setFont(self._font(self.config.question_font_size_pt))
                    self.question_labels.append(question_label)

                self.options_layout.addWidget(
                    question_label,
                    separator_index * 2 + 1,
                    0,
                    1,
                    columns,
                )

        for position_index, ((option_id, label), (row, column)) in enumerate(
            zip(self._displayed_options, positions, strict=True),
            start=1,
        ):
            button = QPushButton(label)
            button.setObjectName("multipleChoiceOption")
            button.setProperty("active", False)
            button.setProperty("selected", False)
            button.setFont(self._font(self.config.option_font_size_pt))
            button.setMinimumHeight(option_minimum_height)
            button.clicked.connect(
                lambda checked=False, selected_id=option_id: self._toggle(
                    selected_id,
                    method="mouse",
                )
            )

            if not self.allow_mouse_fallback:
                button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

            self._buttons[option_id] = button
            self._position_by_option[option_id] = position_index
            self.options_layout.addWidget(button, row, column)

        for index in range(row_count):
            self.options_layout.setRowStretch(index, 1)

        if self.config.layout == "grid":
            for separator_index in range(1, row_count, 2):
                self.options_layout.setRowStretch(separator_index, 0)

        for index in range(column_count):
            self.options_layout.setColumnStretch(index, 1)

    @staticmethod
    def _ring_positions(count: int) -> tuple[tuple[int, int], ...]:
        positions = {
            2: ((1, 0), (1, 2)),
            3: ((0, 1), (2, 0), (2, 2)),
            4: ((0, 1), (1, 2), (2, 1), (1, 0)),
            5: ((0, 1), (0, 2), (2, 2), (2, 0), (0, 0)),
            6: ((0, 0), (0, 1), (0, 2), (2, 2), (2, 1), (2, 0)),
        }
        return positions[count]

    def start(self) -> None:
        self.reset()
        self._started_at_ns = monotonic_ns()
        self._queue_event(
            "question_presented",
            monotonic_timestamp_ns=self._started_at_ns,
            payload={
                "question_id": "multiple-choice-1",
                "question": self.config.question,
                "options": self._option_payloads(),
                "layout": self.config.layout,
                "grid_shape": self.config.resolved_grid_shape_name,
                "template_id": self.config.template_id,
                "randomization_seed": self.randomization_seed,
                "allows_multiple": True,
                "has_fixed_answer": False,
            },
        )

    def stop(self) -> None:
        return None

    def reset(self) -> None:
        self._selected_option_ids.clear()
        self._active_option_id = None
        self._latched_option_id = None
        self._dwell_ms = 0.0
        self._last_timestamp_ns = None
        self._started_at_ns = None
        self._first_selection_ns = None
        self._toggle_count = 0
        self._recording_events.clear()
        self._final_event_recorded = False
        self._refresh_selection()
        self._refresh_dwell()

    def _toggle(
        self,
        option_id: str,
        *,
        method: str,
        monotonic_timestamp_ns: int | None = None,
    ) -> None:
        if option_id not in self._buttons:
            raise ValueError(f"Unknown option: {option_id}")

        timestamp_ns = (
            monotonic_ns() if monotonic_timestamp_ns is None else int(monotonic_timestamp_ns)
        )
        selected = option_id not in self._selected_option_ids

        if selected:
            self._selected_option_ids.add(option_id)

            if self._first_selection_ns is None:
                self._first_selection_ns = timestamp_ns
        else:
            self._selected_option_ids.remove(option_id)

        self._toggle_count += 1
        payload = self._event_payload(option_id)
        payload.update(
            {
                "selected": selected,
                "method": method,
                "selected_option_ids": list(self.selected_option_ids),
                "selected_answers": list(self.selected_answers),
            }
        )
        self._queue_event(
            "option_selected" if selected else "option_cancelled",
            monotonic_timestamp_ns=timestamp_ns,
            payload=payload,
        )
        self._refresh_selection()
        self.selection_changed.emit(option_id, selected)

        if method == "mouse":
            self._reset_dwell()

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        timestamp_ns = sample.timestamp.monotonic_timestamp_ns

        if self._last_timestamp_ns is None or timestamp_ns <= self._last_timestamp_ns:
            elapsed_ms = 0.0
        else:
            elapsed_ms = min(
                250.0,
                (timestamp_ns - self._last_timestamp_ns) / 1_000_000.0,
            )

        self._last_timestamp_ns = timestamp_ns

        if not sample.gaze_valid:
            self.advance_dwell(None, elapsed_ms, monotonic_timestamp_ns=timestamp_ns)
            return

        assert sample.gaze_x_normalized is not None
        assert sample.gaze_y_normalized is not None
        option_id = self._option_at_gaze(
            max(0.0, min(1.0, float(sample.gaze_x_normalized))),
            max(0.0, min(1.0, float(sample.gaze_y_normalized))),
        )
        self.advance_dwell(option_id, elapsed_ms, monotonic_timestamp_ns=timestamp_ns)

    def advance_dwell(
        self,
        option_id: str | None,
        elapsed_ms: float,
        *,
        monotonic_timestamp_ns: int | None = None,
    ) -> None:
        """Advance one option dwell; exposed for deterministic tests."""
        if elapsed_ms < 0:
            raise ValueError("elapsed_ms cannot be negative.")

        if option_id is not None and option_id not in self._buttons:
            raise ValueError(f"Unknown visible option: {option_id}")

        timestamp_ns = (
            monotonic_ns() if monotonic_timestamp_ns is None else int(monotonic_timestamp_ns)
        )

        if option_id != self._latched_option_id:
            self._latched_option_id = None

        if option_id is None or option_id == self._latched_option_id:
            if self._active_option_id is not None:
                self._queue_event(
                    "dwell_cancelled",
                    monotonic_timestamp_ns=timestamp_ns,
                    payload={
                        **self._event_payload(self._active_option_id),
                        "accumulated_dwell_ms": self._dwell_ms,
                    },
                )

            self._active_option_id = None
            self._dwell_ms = 0.0
            self._refresh_dwell()
            return

        if option_id != self._active_option_id:
            if self._active_option_id is not None:
                self._queue_event(
                    "dwell_cancelled",
                    monotonic_timestamp_ns=timestamp_ns,
                    payload={
                        **self._event_payload(self._active_option_id),
                        "accumulated_dwell_ms": self._dwell_ms,
                    },
                )

            self._active_option_id = option_id
            self._dwell_ms = 0.0
            self._queue_event(
                "dwell_started",
                monotonic_timestamp_ns=timestamp_ns,
                payload=self._event_payload(option_id),
            )

        self._dwell_ms += elapsed_ms
        self._refresh_dwell()

        if self._dwell_ms >= self.config.dwell_time_ms:
            self._queue_event(
                "dwell_confirmed",
                monotonic_timestamp_ns=timestamp_ns,
                payload={
                    **self._event_payload(option_id),
                    "dwell_ms": self._dwell_ms,
                },
            )
            self._toggle(
                option_id,
                method="gaze_dwell",
                monotonic_timestamp_ns=timestamp_ns,
            )
            self._latched_option_id = option_id
            self._active_option_id = None
            self._dwell_ms = 0.0
            self._refresh_dwell()

    def _reset_dwell(self) -> None:
        self._active_option_id = None
        self._latched_option_id = None
        self._dwell_ms = 0.0
        self._refresh_dwell()

    def _refresh_selection(self) -> None:
        selected = "、".join(self.selected_answers)
        self.summary_label.setText(
            f"已选择：{selected} · 再次选择可取消"
            if selected
            else "尚未选择 · 可选择多个，再次选择可取消"
        )

        for option_id, button in self._buttons.items():
            is_selected = option_id in self._selected_option_ids
            button.setProperty("selected", is_selected)
            button.setText(
                f"✓ {self._labels[option_id]}" if is_selected else self._labels[option_id]
            )
            button.style().unpolish(button)
            button.style().polish(button)

    def _refresh_dwell(self) -> None:
        self.dwell_progress.setValue(int(min(self._dwell_ms, self.config.dwell_time_ms)))

        for option_id, button in self._buttons.items():
            active = option_id == self._active_option_id

            if button.property("active") != active:
                button.setProperty("active", active)
                button.style().unpolish(button)
                button.style().polish(button)

    def _button_bounds(
        self,
        button: QPushButton,
    ) -> tuple[float, float, float, float] | None:
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        top_left = button.mapTo(self, QPoint(0, 0))
        left = max(0.0, min(1.0, top_left.x() / width))
        top = max(0.0, min(1.0, top_left.y() / height))
        right = max(0.0, min(1.0, (top_left.x() + button.width()) / width))
        bottom = max(0.0, min(1.0, (top_left.y() + button.height()) / height))

        if right <= left or bottom <= top:
            return None

        return left, top, right, bottom

    def _option_at_gaze(self, x: float, y: float) -> str | None:
        for option_id, button in self._buttons.items():
            bounds = self._button_bounds(button)

            if bounds is not None and bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
                return option_id

        return None

    def _option_payloads(self) -> list[dict[str, object]]:
        return [
            {
                "option_id": option_id,
                "answer": self._labels[option_id],
                "position": self._position_by_option[option_id],
            }
            for option_id, _ in self._displayed_options
        ]

    def _event_payload(self, option_id: str) -> dict[str, object]:
        return {
            "question_id": "multiple-choice-1",
            "option_id": option_id,
            "answer": self._labels[option_id],
            "position": self._position_by_option[option_id],
            "layout": self.config.layout,
            "grid_shape": self.config.resolved_grid_shape_name,
        }

    def recording_context_for_sample(self, sample: EyeTrackerSample) -> dict[str, object]:
        del sample
        aois: list[dict[str, object]] = []

        for option_id, button in self._buttons.items():
            bounds = self._button_bounds(button)

            if bounds is None:
                continue

            aois.append(
                {
                    "aoi_id": f"{option_id}_answer",
                    "role": "other",
                    "left": bounds[0],
                    "top": bounds[1],
                    "right": bounds[2],
                    "bottom": bounds[3],
                    "label": self._labels[option_id],
                    "metadata": {
                        **self._event_payload(option_id),
                        "selected": option_id in self._selected_option_ids,
                    },
                }
            )

        phase = (
            f"dwell_{self._active_option_id}" if self._active_option_id is not None else "response"
        )
        return {
            "question_id": "multiple-choice-1",
            "phase": phase,
            "aois": aois,
            "register_layout": bool(aois),
            "question_metadata": {
                "question": self.config.question,
                "options": self._option_payloads(),
                "layout": self.config.layout,
                "grid_shape": self.config.resolved_grid_shape_name,
                "option_count": self.config.option_count,
                "template_id": self.config.template_id,
                "randomize_positions": self.config.randomize_positions,
                "randomization_seed": self.randomization_seed,
                "allows_multiple": True,
                "has_fixed_answer": False,
                "selected_option_ids": list(self.selected_option_ids),
            },
        }

    def _queue_event(
        self,
        event_type: str,
        *,
        monotonic_timestamp_ns: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        self._recording_events.append(
            {
                "event_type": event_type,
                "monotonic_timestamp_ns": (
                    monotonic_ns()
                    if monotonic_timestamp_ns is None
                    else int(monotonic_timestamp_ns)
                ),
                "payload": dict(payload or {}),
            }
        )

    def drain_recording_events(self) -> tuple[dict[str, object], ...]:
        events = tuple(self._recording_events)
        self._recording_events.clear()
        return events

    def recording_result(self, reason: str) -> dict[str, object]:
        reaction_time_ms: float | None = None

        if self._first_selection_ns is not None and self._started_at_ns is not None:
            reaction_time_ms = max(
                0.0,
                (self._first_selection_ns - self._started_at_ns) / 1_000_000.0,
            )

        result = {
            "question_id": "multiple-choice-1",
            "question": self.config.question,
            "selected_option_ids": list(self.selected_option_ids),
            "selected_answers": list(self.selected_answers),
            "selected_count": len(self.selected_option_ids),
            "toggle_count": self._toggle_count,
            "first_selection_reaction_time_ms": reaction_time_ms,
            "option_count": self.config.option_count,
            "options": self._option_payloads(),
            "layout": self.config.layout,
            "grid_shape": self.config.resolved_grid_shape_name,
            "template_id": self.config.template_id,
            "allows_multiple": True,
            "has_fixed_answer": False,
            "is_scored": False,
            "randomization_seed": self.randomization_seed,
            "configured_dwell_ms": self.config.dwell_time_ms,
            "completion_status": ("selected" if self._selected_option_ids else "unanswered"),
            "completion_reason": reason.strip() or "completed",
        }

        if not self._final_event_recorded:
            self._queue_event("task_completed", payload=result)
            self._final_event_recorded = True

        return result


class MultipleChoiceSetupDialog(QDialog):
    """Configure a multi-option task before desktop launch."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        config: MultipleChoiceConfig | None = None,
    ) -> None:
        super().__init__(parent)
        initial = config or MultipleChoiceConfig()
        self.setWindowTitle("多选项问答设置")
        self.resize(760, 860)
        self._applying_template = False
        self._selected_template_id = initial.template_id

        form_widget = QWidget()
        form = QFormLayout()
        form_widget.setLayout(form)

        self.template_combo = QComboBox()
        self.template_combo.setObjectName("multipleChoiceTemplateCombo")
        self.template_combo.addItem("自定义多选题", None)

        for template in BUILT_IN_MULTIPLE_CHOICE_TEMPLATES:
            self.template_combo.addItem(
                f"[{template.category}] {template.question}",
                template.template_id,
            )

        template_index = self.template_combo.findData(initial.template_id)
        self.template_combo.setCurrentIndex(max(0, template_index))
        form.addRow("固定题库：", self.template_combo)

        self.question_edit = QLineEdit(initial.question)
        form.addRow("问题文字：", self.question_edit)

        self.option_count_spin = QSpinBox()
        self.option_count_spin.setRange(2, 12)
        self.option_count_spin.setValue(initial.option_count)
        form.addRow("选项数量：", self.option_count_spin)

        self.layout_combo = QComboBox()
        self.layout_combo.addItem("分区宫格", "grid")
        self.layout_combo.addItem("环形排列", "ring")
        self.layout_combo.setCurrentIndex(self.layout_combo.findData(initial.layout))
        form.addRow("排列方式：", self.layout_combo)

        self.grid_shape_combo = QComboBox()
        self.grid_shape_combo.addItem("按选项数自动选择", "auto")

        for shape in MULTIPLE_CHOICE_GRID_SHAPES:
            self.grid_shape_combo.addItem(shape.replace("x", "×"), shape)

        self.grid_shape_combo.setCurrentIndex(self.grid_shape_combo.findData(initial.grid_shape))
        form.addRow("宫格行列：", self.grid_shape_combo)

        self.option_edits: list[QLineEdit] = []

        for index, (_, value) in enumerate(
            tuple(
                (f"option_{index}", getattr(initial, f"option_{index}")) for index in range(1, 13)
            ),
            start=1,
        ):
            edit = QLineEdit(value)
            self.option_edits.append(edit)
            form.addRow(f"选项 {index}：", edit)

        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(250, 10_000)
        self.dwell_spin.setSingleStep(100)
        self.dwell_spin.setValue(initial.dwell_time_ms)
        form.addRow("停留阈值（ms）：", self.dwell_spin)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(5, 3_600)
        self.duration_spin.setValue(initial.duration_seconds)
        form.addRow("最长任务时长（秒）：", self.duration_spin)

        self.question_font_spin = QSpinBox()
        self.question_font_spin.setRange(20, 120)
        self.question_font_spin.setValue(initial.question_font_size_pt)
        form.addRow("问题字号（pt）：", self.question_font_spin)

        self.option_font_spin = QSpinBox()
        self.option_font_spin.setRange(20, 120)
        self.option_font_spin.setValue(initial.option_font_size_pt)
        form.addRow("选项字号（pt）：", self.option_font_spin)

        self.randomize_check = QCheckBox("每次呈现随机交换选项位置")
        self.randomize_check.setChecked(initial.randomize_positions)
        form.addRow("位置随机化：", self.randomize_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        root.addWidget(scroll, 1)
        root.addWidget(buttons)

        self.option_count_spin.valueChanged.connect(self._refresh_option_fields)
        self.template_combo.currentIndexChanged.connect(self._apply_selected_template)
        self.question_edit.textEdited.connect(self._clear_selected_template)
        self.option_count_spin.valueChanged.connect(self._clear_selected_template)
        self.layout_combo.currentIndexChanged.connect(self._clear_selected_template)
        self.grid_shape_combo.currentIndexChanged.connect(self._clear_selected_template)

        for edit in self.option_edits:
            edit.textEdited.connect(self._clear_selected_template)

        self._refresh_option_fields(initial.option_count)

    def _clear_selected_template(self, *_args: object) -> None:
        if self._applying_template:
            return

        self._selected_template_id = None
        self.template_combo.blockSignals(True)
        self.template_combo.setCurrentIndex(0)
        self.template_combo.blockSignals(False)

    def _apply_selected_template(self, *_args: object) -> None:
        template_id = self.template_combo.currentData()
        template = next(
            (
                item
                for item in BUILT_IN_MULTIPLE_CHOICE_TEMPLATES
                if item.template_id == template_id
            ),
            None,
        )

        if template is None:
            self._selected_template_id = None
            return

        self._applying_template = True

        try:
            self._selected_template_id = template.template_id
            self.question_edit.setText(template.question)
            self.option_count_spin.setValue(len(template.options))
            self.layout_combo.setCurrentIndex(self.layout_combo.findData("grid"))
            self.grid_shape_combo.setCurrentIndex(
                self.grid_shape_combo.findData(template.grid_shape)
            )

            for index, edit in enumerate(self.option_edits):
                edit.setText(template.options[index] if index < len(template.options) else "")
        finally:
            self._applying_template = False

    def _refresh_option_fields(self, count: int) -> None:
        for index, edit in enumerate(self.option_edits, start=1):
            edit.setEnabled(index <= count)

    def build_config(self) -> MultipleChoiceConfig:
        values = [edit.text().strip() for edit in self.option_edits]
        return MultipleChoiceConfig(
            question=self.question_edit.text().strip(),
            option_count=self.option_count_spin.value(),
            option_1=values[0],
            option_2=values[1],
            option_3=values[2],
            option_4=values[3],
            option_5=values[4],
            option_6=values[5],
            option_7=values[6],
            option_8=values[7],
            option_9=values[8],
            option_10=values[9],
            option_11=values[10],
            option_12=values[11],
            layout=str(self.layout_combo.currentData()),
            grid_shape=str(self.grid_shape_combo.currentData()),
            dwell_time_ms=self.dwell_spin.value(),
            duration_seconds=self.duration_spin.value(),
            question_font_size_pt=self.question_font_spin.value(),
            option_font_size_pt=self.option_font_spin.value(),
            randomize_positions=self.randomize_check.isChecked(),
            randomization_seed=None,
            template_id=self._selected_template_id,
        )
