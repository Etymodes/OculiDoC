"""Gaze-driven staged Pinyin keyboard."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic_ns

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.devices.contracts import EyeTrackerSample


class KeyboardStage(StrEnum):
    INITIAL = "initial"
    CONFIRM_INITIAL = "confirm_initial"
    FINAL = "final"
    CONFIRM_FINAL = "confirm_final"
    TAIL = "tail"
    CONFIRM_TAIL = "confirm_tail"
    TONE = "tone"
    CONFIRM_TONE = "confirm_tone"


@dataclass(frozen=True, slots=True)
class ScreenKeyboardConfig:
    dwell_time_ms: int = 900
    duration_seconds: int = 600
    enable_tone_step: bool = True
    output_font_size_pt: int = 48
    instruction_font_size_pt: int = 30
    option_font_size_pt: int = 34

    def __post_init__(self) -> None:
        if not 250 <= self.dwell_time_ms <= 10_000:
            raise ValueError("dwell_time_ms must be between 250 and 10000.")

        if not 5 <= self.duration_seconds <= 3_600:
            raise ValueError("duration_seconds must be between 5 and 3600.")

        for name in (
            "output_font_size_pt",
            "instruction_font_size_pt",
            "option_font_size_pt",
        ):
            if not 20 <= getattr(self, name) <= 120:
                raise ValueError(f"{name} must be between 20 and 120.")


_INITIALS = (
    ("b", "b"),
    ("p", "p"),
    ("m", "m"),
    ("f", "f"),
    ("d", "d"),
    ("t", "t"),
    ("n", "n"),
    ("l", "l"),
    ("g", "g"),
    ("k", "k"),
    ("h", "h"),
    ("j", "j"),
    ("q", "q"),
    ("x", "x"),
    ("zh", "zh"),
    ("ch", "ch"),
    ("sh", "sh"),
    ("r", "r"),
    ("z", "z"),
    ("c", "c"),
    ("s", "s"),
    ("y", "y"),
    ("w", "w"),
    ("空声母", ""),
)

_FINALS = tuple(
    (value, value)
    for value in (
        "a",
        "o",
        "e",
        "i",
        "u",
        "ü",
        "ai",
        "ei",
        "ao",
        "ou",
        "ia",
        "ie",
        "ua",
        "uo",
        "üe",
        "ui",
        "uai",
        "iao",
        "iu",
    )
)

_TAILS = (
    ("无韵尾", ""),
    ("n", "n"),
    ("ng", "ng"),
)

_TONES = (
    ("一声", "1"),
    ("二声", "2"),
    ("三声", "3"),
    ("四声", "4"),
    ("轻声", "5"),
)

_TONE_MARKS = {
    "a": "āáǎà",
    "e": "ēéěè",
    "i": "īíǐì",
    "o": "ōóǒò",
    "u": "ūúǔù",
    "ü": "ǖǘǚǜ",
}

_STAGE_TEXT = {
    KeyboardStage.INITIAL: "第一步：请选择声母",
    KeyboardStage.CONFIRM_INITIAL: "请确认声母是否选对",
    KeyboardStage.FINAL: "第二步：请选择韵腹或组合韵母",
    KeyboardStage.CONFIRM_FINAL: "请确认韵母是否选对",
    KeyboardStage.TAIL: "第三步：请选择韵尾",
    KeyboardStage.CONFIRM_TAIL: "请确认韵尾是否选对",
    KeyboardStage.TONE: "第四步：请选择声调",
    KeyboardStage.CONFIRM_TONE: "请确认声调是否选对",
}


def apply_tone(syllable: str, tone: int) -> str:
    """Apply a standard Pinyin tone mark without a conversion dependency."""
    if tone == 5:
        return syllable

    if tone not in {1, 2, 3, 4}:
        raise ValueError("tone must be between 1 and 5.")

    target_index = -1

    for preferred in ("a", "e"):
        if preferred in syllable:
            target_index = syllable.index(preferred)
            break

    if target_index < 0 and "ou" in syllable:
        target_index = syllable.index("o")

    if target_index < 0:
        for index in range(len(syllable) - 1, -1, -1):
            if syllable[index] in _TONE_MARKS:
                target_index = index
                break

    if target_index < 0:
        return syllable

    vowel = syllable[target_index]
    marked = _TONE_MARKS[vowel][tone - 1]
    return syllable[:target_index] + marked + syllable[target_index + 1 :]


class ScreenKeyboardTask(QWidget):
    """Compose Pinyin with large gaze-dwell choices and explicit confirmation."""

    display_text_changed = Signal(str)
    speech_requested = Signal(str)
    syllable_committed = Signal(str)

    def __init__(
        self,
        config: ScreenKeyboardConfig,
        *,
        allow_mouse_fallback: bool = True,
    ) -> None:
        super().__init__()
        self.config = config
        self.allow_mouse_fallback = allow_mouse_fallback
        self.stage = KeyboardStage.INITIAL
        self.output_text = ""
        self._initial = ""
        self._final = ""
        self._tail = ""
        self._tone = "5"
        self._active_option_id: str | None = None
        self._dwell_ms = 0.0
        self._last_timestamp_ns: int | None = None
        self._buttons: dict[str, QPushButton] = {}
        self._button_values: dict[str, str] = {}
        self._layout_revision = 0
        self._recording_events: list[dict[str, object]] = []
        self._started_at_ns: int | None = None
        self._commit_count = 0
        self._final_event_recorded = False

        self.setStyleSheet(
            """
            QWidget#screenKeyboard {
                background: #fff1bd;
                color: #3f351f;
                font-family: "Microsoft YaHei UI";
            }
            QWidget#outputPanel {
                background: #fff8dc;
                border-bottom: 3px solid #e6cf7a;
            }
            QLabel#outputText { color: #2f291b; font-weight: 800; padding: 10px 24px; }
            QLabel#composeText { color: #785c00; font-weight: 700; padding: 4px 24px; }
            QLabel#instructionText { color: #493b18; font-weight: 800; }
            QPushButton#pinyinOption {
                min-height: 68px;
                background: #fffdf4;
                color: #2f291b;
                border: 4px solid #c6ad52;
                border-radius: 18px;
                font-weight: 800;
                padding: 8px;
            }
            QPushButton#pinyinOption[active="true"] {
                background: #fff0a0;
                border-color: #e28a00;
            }
            QPushButton#keyboardAction {
                min-height: 54px;
                background: #5c6f7d;
                color: white;
                border: 3px solid white;
                border-radius: 14px;
                font-weight: 800;
            }
            QPushButton#keyboardAction[active="true"] {
                background: #c06b00;
                border-color: #fff0a0;
            }
            QProgressBar {
                min-height: 24px;
                border: 2px solid #927823;
                border-radius: 9px;
                background: #fff8dc;
                text-align: center;
                color: #3f351f;
                font-size: 15px;
                font-weight: 700;
            }
            QProgressBar::chunk { background: #efb323; border-radius: 7px; }
            """
        )
        self.setObjectName("screenKeyboard")

        output_panel = QWidget()
        output_panel.setObjectName("outputPanel")
        output_layout = QVBoxLayout(output_panel)
        output_layout.setContentsMargins(18, 10, 18, 8)
        output_layout.setSpacing(2)

        self.output_label = QLabel("尚未输入")
        self.output_label.setObjectName("outputText")
        self.output_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.output_label.setWordWrap(True)
        self.output_label.setFont(self._font(config.output_font_size_pt, bold=True))

        self.compose_label = QLabel("当前拼音：等待选择")
        self.compose_label.setObjectName("composeText")
        self.compose_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.compose_label.setFont(self._font(config.instruction_font_size_pt, bold=True))

        output_layout.addWidget(self.output_label, 1)
        output_layout.addWidget(self.compose_label)

        self.instruction_label = QLabel()
        self.instruction_label.setObjectName("instructionText")
        self.instruction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.instruction_label.setFont(self._font(config.instruction_font_size_pt, bold=True))

        self.options_widget = QWidget()
        self.options_layout = QGridLayout(self.options_widget)
        self.options_layout.setContentsMargins(16, 5, 16, 5)
        self.options_layout.setSpacing(8)

        self.dwell_progress = QProgressBar()
        self.dwell_progress.setRange(0, config.dwell_time_ms)
        self.dwell_progress.setFormat("请持续注视选项 · %p%")

        actions = QHBoxLayout()
        actions.setContentsMargins(16, 2, 16, 10)
        actions.setSpacing(10)

        for action_id, label in (
            ("action:delete", "删除"),
            ("action:space", "空格"),
            ("action:read", "朗读"),
            ("action:clear", "清空"),
        ):
            button = self._create_button(action_id, label, action=True)
            actions.addWidget(button, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(5)
        root.addWidget(output_panel, 1)
        root.addWidget(self.instruction_label)
        root.addWidget(self.options_widget, 1)
        root.addWidget(self.dwell_progress)
        root.addLayout(actions)

        if not allow_mouse_fallback:
            self.setCursor(Qt.CursorShape.BlankCursor)

        self._render_stage()

    @staticmethod
    def _font(size: int, *, bold: bool = False) -> QFont:
        font = QFont("Microsoft YaHei UI", size)
        font.setBold(bold)
        return font

    @property
    def composing_text(self) -> str:
        pieces = [self._initial or "∅"]

        if self._final:
            pieces.append(self._final)

        if self._tail:
            pieces.append(self._tail)

        if self.stage in {KeyboardStage.TONE, KeyboardStage.CONFIRM_TONE}:
            pieces.append(f"{self._tone}声" if self._tone != "5" else "轻声")

        if len(pieces) == 1 and pieces[0] == "∅" and self.stage is KeyboardStage.INITIAL:
            return "等待选择"

        return " + ".join(pieces)

    @property
    def patient_display_text(self) -> str:
        output = self.output_text or "尚未输入"
        return f"屏幕打字\n{output}\n当前拼音：{self.composing_text}"

    def _stage_options(self) -> tuple[tuple[str, str], ...]:
        if self.stage is KeyboardStage.INITIAL:
            return _INITIALS

        if self.stage is KeyboardStage.FINAL:
            return _FINALS

        if self.stage is KeyboardStage.TAIL:
            return _TAILS

        if self.stage is KeyboardStage.TONE:
            return _TONES

        return (("选对了\n继续", "yes"), ("选错了\n返回", "no"))

    def _option_columns(self) -> int:
        count = len(self._stage_options())

        if count <= 3:
            return count

        if count <= 6:
            return 3

        return 6

    def _create_button(self, option_id: str, label: str, *, action: bool = False) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("keyboardAction" if action else "pinyinOption")
        button.setProperty("active", False)
        button.setFont(self._font(self.config.option_font_size_pt, bold=True))
        button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            (QSizePolicy.Policy.Fixed if action else QSizePolicy.Policy.Expanding),
        )
        button.clicked.connect(lambda checked=False, key=option_id: self._activate(key, "mouse"))

        if not self.allow_mouse_fallback:
            button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._buttons[option_id] = button
        return button

    def _render_stage(self) -> None:
        while item := self.options_layout.takeAt(0):
            widget = item.widget()

            if widget is not None:
                self._buttons.pop(str(widget.property("option_id") or ""), None)
                widget.deleteLater()

        self._button_values.clear()
        columns = self._option_columns()

        for index, (label, value) in enumerate(self._stage_options()):
            option_id = f"stage:{index}"
            button = self._create_button(option_id, label)
            button.setProperty("option_id", option_id)
            self._button_values[option_id] = value
            self.options_layout.addWidget(button, index // columns, index % columns)

        self._layout_revision += 1
        self.instruction_label.setText(_STAGE_TEXT[self.stage])
        self._refresh_labels()
        self._reset_dwell()

    def _refresh_labels(self) -> None:
        self.output_label.setText(self.output_text or "尚未输入")
        self.compose_label.setText(f"当前拼音：{self.composing_text}")
        self.display_text_changed.emit(self.patient_display_text)

    def _set_stage(self, stage: KeyboardStage) -> None:
        self.stage = stage
        self._queue_event("stage_changed", payload={"stage": stage.value})
        self._render_stage()
        self.speech_requested.emit(_STAGE_TEXT[stage])

    def start(self) -> None:
        self.reset()
        self._started_at_ns = monotonic_ns()
        self._queue_event(
            "typing_started",
            monotonic_timestamp_ns=self._started_at_ns,
            payload={"tone_step_enabled": self.config.enable_tone_step},
        )
        self.speech_requested.emit(_STAGE_TEXT[self.stage])

    def stop(self) -> None:
        return None

    def reset(self) -> None:
        self.stage = KeyboardStage.INITIAL
        self.output_text = ""
        self._initial = ""
        self._final = ""
        self._tail = ""
        self._tone = "5"
        self._last_timestamp_ns = None
        self._recording_events.clear()
        self._started_at_ns = None
        self._commit_count = 0
        self._final_event_recorded = False
        self._render_stage()

    def _activate(self, option_id: str, method: str) -> None:
        if option_id.startswith("action:"):
            self._run_action(option_id, method)
            return

        value = self._button_values.get(option_id)

        if value is None:
            return

        self._queue_event(
            "option_selected",
            payload={"stage": self.stage.value, "value": value, "method": method},
        )

        if self.stage is KeyboardStage.INITIAL:
            self._initial = value
            self._set_stage(KeyboardStage.CONFIRM_INITIAL)
        elif self.stage is KeyboardStage.FINAL:
            self._final = value
            self._set_stage(KeyboardStage.CONFIRM_FINAL)
        elif self.stage is KeyboardStage.TAIL:
            self._tail = value
            self._set_stage(KeyboardStage.CONFIRM_TAIL)
        elif self.stage is KeyboardStage.TONE:
            self._tone = value
            self._set_stage(KeyboardStage.CONFIRM_TONE)
        elif value == "yes":
            self._confirm_current_stage()
        else:
            self._return_to_selection()

    def _confirm_current_stage(self) -> None:
        if self.stage is KeyboardStage.CONFIRM_INITIAL:
            self._set_stage(KeyboardStage.FINAL)
        elif self.stage is KeyboardStage.CONFIRM_FINAL:
            self._set_stage(KeyboardStage.TAIL)
        elif self.stage is KeyboardStage.CONFIRM_TAIL:
            if self.config.enable_tone_step:
                self._set_stage(KeyboardStage.TONE)
            else:
                self._commit_syllable()
        elif self.stage is KeyboardStage.CONFIRM_TONE:
            self._commit_syllable()

    def _return_to_selection(self) -> None:
        if self.stage is KeyboardStage.CONFIRM_INITIAL:
            self._initial = ""
            self._set_stage(KeyboardStage.INITIAL)
        elif self.stage is KeyboardStage.CONFIRM_FINAL:
            self._final = ""
            self._set_stage(KeyboardStage.FINAL)
        elif self.stage is KeyboardStage.CONFIRM_TAIL:
            self._tail = ""
            self._set_stage(KeyboardStage.TAIL)
        elif self.stage is KeyboardStage.CONFIRM_TONE:
            self._tone = "5"
            self._set_stage(KeyboardStage.TONE)

    def _commit_syllable(self) -> None:
        raw = self._initial + self._final + self._tail
        syllable = apply_tone(raw, int(self._tone))

        if self.output_text and not self.output_text.endswith(" "):
            self.output_text += " "

        self.output_text += syllable
        self._commit_count += 1
        self._queue_event(
            "syllable_committed",
            payload={"raw_pinyin": raw, "syllable": syllable, "final_text": self.output_text},
        )
        self.syllable_committed.emit(syllable)
        self._initial = ""
        self._final = ""
        self._tail = ""
        self._tone = "5"
        self._set_stage(KeyboardStage.INITIAL)

    def _run_action(self, option_id: str, method: str) -> None:
        action = option_id.removeprefix("action:")

        if action == "delete":
            stripped = self.output_text.rstrip()
            self.output_text = stripped.rsplit(" ", 1)[0] if " " in stripped else ""
        elif action == "space":
            if self.output_text:
                self.output_text += " "
        elif action == "read":
            self.speech_requested.emit(self.output_text or "尚未输入文字")
        elif action == "clear":
            self.output_text = ""

        self._queue_event(
            "typing_action",
            payload={"action": action, "method": method, "final_text": self.output_text},
        )
        self._refresh_labels()
        self._reset_dwell()

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        timestamp_ns = sample.timestamp.monotonic_timestamp_ns

        if self._last_timestamp_ns is None or timestamp_ns <= self._last_timestamp_ns:
            elapsed_ms = 0.0
        else:
            elapsed_ms = min(250.0, (timestamp_ns - self._last_timestamp_ns) / 1_000_000.0)

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
        """Advance one gaze option; exposed for deterministic tests."""
        if elapsed_ms < 0:
            raise ValueError("elapsed_ms cannot be negative.")

        if option_id is not None and option_id not in self._buttons:
            raise ValueError(f"Unknown visible option: {option_id}")

        timestamp_ns = (
            monotonic_ns() if monotonic_timestamp_ns is None else int(monotonic_timestamp_ns)
        )

        if option_id != self._active_option_id:
            if self._active_option_id is not None:
                self._queue_event(
                    "dwell_cancelled",
                    monotonic_timestamp_ns=timestamp_ns,
                    payload={
                        "option_id": self._active_option_id,
                        "accumulated_dwell_ms": self._dwell_ms,
                    },
                )

            self._active_option_id = option_id
            self._dwell_ms = 0.0

            if option_id is not None:
                self._queue_event(
                    "dwell_started",
                    monotonic_timestamp_ns=timestamp_ns,
                    payload={"option_id": option_id, "stage": self.stage.value},
                )

        if option_id is not None:
            self._dwell_ms += elapsed_ms

        self._refresh_dwell()

        if option_id is not None and self._dwell_ms >= self.config.dwell_time_ms:
            self._queue_event(
                "dwell_confirmed",
                monotonic_timestamp_ns=timestamp_ns,
                payload={"option_id": option_id, "dwell_ms": self._dwell_ms},
            )
            self._activate(option_id, "gaze")
            self._reset_dwell()

    def visible_option_id(self, value: str) -> str:
        """Return the current option id for a logical value, for tests and adapters."""
        return next(key for key, item in self._button_values.items() if item == value)

    def _reset_dwell(self) -> None:
        self._active_option_id = None
        self._dwell_ms = 0.0
        self._refresh_dwell()

    def _refresh_dwell(self) -> None:
        self.dwell_progress.setValue(int(min(self._dwell_ms, self.config.dwell_time_ms)))

        for option_id, button in self._buttons.items():
            active = option_id == self._active_option_id

            if button.property("active") != active:
                button.setProperty("active", active)
                button.style().unpolish(button)
                button.style().polish(button)

    def _button_bounds(self, button: QPushButton) -> tuple[float, float, float, float] | None:
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

    def recording_context_for_sample(self, sample: EyeTrackerSample) -> dict[str, object]:
        del sample
        aois: list[dict[str, object]] = []

        for option_id, button in self._buttons.items():
            bounds = self._button_bounds(button)

            if bounds is None:
                continue

            aois.append(
                {
                    "aoi_id": option_id.replace(":", "_"),
                    "role": "other",
                    "left": bounds[0],
                    "top": bounds[1],
                    "right": bounds[2],
                    "bottom": bounds[3],
                    "label": button.text(),
                    "metadata": {"stage": self.stage.value, "option_id": option_id},
                }
            )

        return {
            "question_id": f"screen-keyboard-{self._layout_revision}",
            "phase": self.stage.value,
            "aois": aois,
            "register_layout": bool(aois),
            "question_metadata": {"stage": self.stage.value, "layout": "grid"},
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
        result = {
            "final_text": self.output_text,
            "committed_syllable_count": self._commit_count,
            "current_stage": self.stage.value,
            "current_composition": self.composing_text,
            "tone_step_enabled": self.config.enable_tone_step,
            "configured_dwell_ms": self.config.dwell_time_ms,
            "completion_reason": reason.strip() or "completed",
        }

        if not self._final_event_recorded:
            self._queue_event("typing_completed", payload=result)
            self._final_event_recorded = True

        return result


class ScreenKeyboardSetupDialog(QDialog):
    """Configure the staged Pinyin task before a desktop launch."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        config: ScreenKeyboardConfig | None = None,
    ) -> None:
        super().__init__(parent)
        current = config or ScreenKeyboardConfig()
        self.setWindowTitle("屏幕打字设置")
        self.setMinimumWidth(520)

        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(250, 10_000)
        self.dwell_spin.setSingleStep(100)
        self.dwell_spin.setValue(current.dwell_time_ms)
        self.dwell_spin.setSuffix(" ms")

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(5, 3_600)
        self.duration_spin.setValue(current.duration_seconds)
        self.duration_spin.setSuffix(" 秒")

        self.output_font_spin = self._font_spin(current.output_font_size_pt)
        self.instruction_font_spin = self._font_spin(current.instruction_font_size_pt)
        self.option_font_spin = self._font_spin(current.option_font_size_pt)
        self.tone_checkbox = QCheckBox("完成韵尾后选择声调")
        self.tone_checkbox.setChecked(current.enable_tone_step)

        form = QFormLayout()
        form.addRow("停留确认时间：", self.dwell_spin)
        form.addRow("任务总时长：", self.duration_spin)
        form.addRow("上半屏输出字号：", self.output_font_spin)
        form.addRow("指示文字字号：", self.instruction_font_spin)
        form.addRow("下半屏选项字号：", self.option_font_spin)
        form.addRow("声调步骤：", self.tone_checkbox)

        note = QLabel(
            "输入顺序：声母 → 确认 → 韵腹/组合韵母 → 确认 → 韵尾 → 确认"
            " → 可选声调 → 确认。每个音节完成后自动回到声母选择。"
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    @staticmethod
    def _font_spin(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(20, 120)
        spin.setValue(value)
        spin.setSuffix(" pt")
        return spin

    def build_config(self) -> ScreenKeyboardConfig:
        return ScreenKeyboardConfig(
            dwell_time_ms=self.dwell_spin.value(),
            duration_seconds=self.duration_spin.value(),
            enable_tone_step=self.tone_checkbox.isChecked(),
            output_font_size_pt=self.output_font_spin.value(),
            instruction_font_size_pt=self.instruction_font_spin.value(),
            option_font_size_pt=self.option_font_spin.value(),
        )
