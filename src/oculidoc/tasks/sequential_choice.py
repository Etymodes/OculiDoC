"""Shared keyboard-paced sequence controller for scored two-option tasks."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from time import monotonic_ns

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from oculidoc.devices.contracts import EyeTrackerSample
from oculidoc.tasks.binary_question import BinaryQuestionTask


class SequentialChoiceTask(QWidget):
    """Run two-option questions in order and advance only by Space or Enter."""

    question_changed = Signal(str)
    sequence_completed = Signal()

    def __init__(
        self,
        *,
        config: object,
        question_ids: Sequence[str],
        task_factory: Callable[[int], BinaryQuestionTask],
        layout_orientation: str,
    ) -> None:
        super().__init__()
        normalized_ids = tuple(str(value).strip() for value in question_ids)

        if not normalized_ids or any(not value for value in normalized_ids):
            raise ValueError("Sequential choice task requires at least one question identifier.")

        self.config = config
        self.layout_orientation = layout_orientation
        self._question_ids = normalized_ids
        self._task_factory = task_factory
        self._question_index = 0
        self._attempt_number = 1
        self._task_generation = 0
        self._started = False
        self._waiting_for_advance = False
        self._sequence_finished = False
        self._queued_events: list[dict[str, object]] = []
        self._completed_results: list[dict[str, object]] = []
        self._attempt_results: list[dict[str, object]] = []

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "background:#eaf4ff; color:#17324d; font-family:'Microsoft YaHei UI'; "
            "font-size:22px; font-weight:800; padding:8px;"
        )

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self._root.addWidget(self.status_label)
        self._current_task = self._task_factory(0)
        self._root.addWidget(self._current_task, 1)
        self._wire_current_task()
        self._refresh_status()

    @property
    def current_task(self) -> BinaryQuestionTask:
        return self._current_task

    @property
    def current_question_number(self) -> int:
        return self._question_index + 1

    @property
    def question_count(self) -> int:
        return len(self._question_ids)

    @property
    def current_question_text(self) -> str:
        return self._current_task.config.question

    @property
    def patient_display_text(self) -> str:
        return (
            f"第 {self.current_question_number}/{self.question_count} 题\n"
            f"{self.current_question_text}"
        )

    def _wire_current_task(self) -> None:
        self._current_task.answered.connect(self._handle_answer)

    def _refresh_status(self) -> None:
        if self._sequence_finished:
            self.status_label.setText("题库已完成")
        elif self._waiting_for_advance:
            self.status_label.setText(
                f"第 {self.current_question_number}/{self.question_count} 题回答正确 · "
                "按空格或 Enter 继续"
            )
        else:
            self.status_label.setText(
                f"第 {self.current_question_number}/{self.question_count} 题 · 空格或 Enter 可跳过"
            )

    def _question_id(self) -> str:
        return self._question_ids[self._question_index]

    def _decorate_payload(self, payload: Mapping[str, object]) -> dict[str, object]:
        decorated = dict(payload)
        decorated.update(
            {
                "question_id": self._question_id(),
                "question_index": self._question_index,
                "question_number": self.current_question_number,
                "question_count": self.question_count,
                "attempt_number": self._attempt_number,
            }
        )
        return decorated

    def _drain_current_events(self) -> None:
        for event in self._current_task.drain_recording_events():
            payload_value = event.get("payload", {})
            payload = payload_value if isinstance(payload_value, Mapping) else {}
            decorated = dict(event)
            decorated["payload"] = self._decorate_payload(payload)
            self._queued_events.append(decorated)

    def _handle_answer(self, side: str, answer: str) -> None:
        del side, answer
        result = self._decorate_payload(self._current_task.recording_result("answered"))
        self._drain_current_events()
        selected = self._current_task.selected_option_id
        correct = not self._current_task.config.is_scored or (
            selected == self._current_task.config.correct_option_id
        )
        result["correct"] = correct if self._current_task.config.is_scored else None

        if correct:
            self._completed_results.append(result)
            self._current_task.show_correct_feedback()
            self._waiting_for_advance = True
            self._refresh_status()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            return

        self._attempt_results.append(result)
        self._current_task.show_incorrect_feedback()
        generation = self._task_generation
        QTimer.singleShot(1_000, lambda: self._retry_current_question(generation))

    def _replace_current_task(self) -> None:
        self._task_generation += 1
        previous = self._current_task
        self._root.removeWidget(previous)
        previous.deleteLater()
        self._current_task = self._task_factory(self._question_index)
        self._root.addWidget(self._current_task, 1)
        self._wire_current_task()

        if self._started:
            self._current_task.start()

        self.question_changed.emit(self.current_question_text)

    def _retry_current_question(self, generation: int) -> None:
        if (
            generation != self._task_generation
            or self._waiting_for_advance
            or self._sequence_finished
        ):
            return

        self._attempt_number += 1
        self._replace_current_task()
        self._refresh_status()

    def advance_question(self) -> bool:
        """Advance after a correct answer; return whether the sequence ended."""
        if not self._waiting_for_advance or self._sequence_finished:
            return False

        self._waiting_for_advance = False

        if self._question_index + 1 >= self.question_count:
            self._sequence_finished = True
            self._refresh_status()
            self.sequence_completed.emit()
            return True

        self._question_index += 1
        self._attempt_number = 1
        self._replace_current_task()
        self._refresh_status()
        return False

    def skip_question(self, key_name: str) -> bool:
        """Record an operator skip and immediately move to the next question."""
        if self._waiting_for_advance or self._sequence_finished:
            return False

        result = self._decorate_payload(
            self._current_task.recording_result("operator_keyboard_skip")
        )
        self._drain_current_events()
        result.update(
            {
                "selected_option_id": None,
                "selected_side": None,
                "selected_position": None,
                "selected_answer": None,
                "correct": None,
                "completion_status": "skipped",
                "completion_reason": "operator_keyboard_skip",
                "skipped": True,
                "skip_key": key_name,
            }
        )
        self._queued_events.append(
            {
                "event_type": "question_skipped",
                "monotonic_timestamp_ns": monotonic_ns(),
                "payload": dict(result),
            }
        )
        self._completed_results.append(result)
        self._waiting_for_advance = True
        return self.advance_question()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        }:
            if self._waiting_for_advance:
                self.advance_question()
            else:
                key_name = "Space" if event.key() == Qt.Key.Key_Space else "Enter"
                self.skip_question(key_name)

            event.accept()
            return

        super().keyPressEvent(event)

    def start(self) -> None:
        self._started = True
        self._current_task.start()
        self.question_changed.emit(self.current_question_text)

    def stop(self) -> None:
        self._current_task.stop()

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        if not self._waiting_for_advance and not self._sequence_finished:
            self._current_task.consume_sample(sample)

    def recording_context_for_sample(self, sample: EyeTrackerSample) -> dict[str, object]:
        context = dict(self._current_task.recording_context_for_sample(sample))
        context["question_id"] = self._question_id()
        metadata_value = context.get("question_metadata", {})
        metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
        context["question_metadata"] = self._decorate_payload(metadata)
        return context

    def drain_recording_events(self) -> tuple[dict[str, object], ...]:
        self._drain_current_events()
        events = tuple(self._queued_events)
        self._queued_events.clear()
        return events

    def recording_result(self, reason: str) -> dict[str, object]:
        questions = list(self._completed_results)

        if not self._waiting_for_advance and not self._sequence_finished:
            current = self._decorate_payload(self._current_task.recording_result(reason))
            questions.append(current)

        return {
            "completion_status": ("answered" if self._sequence_finished else "partial"),
            "completion_reason": reason.strip() or "completed",
            "question_count": self.question_count,
            "completed_question_count": len(self._completed_results),
            "answered_question_count": sum(
                item.get("completion_status") != "skipped" for item in self._completed_results
            ),
            "skipped_question_count": sum(
                item.get("completion_status") == "skipped" for item in self._completed_results
            ),
            "correct_count": sum(item.get("correct") is True for item in self._completed_results),
            "questions": questions,
            "incorrect_attempts": list(self._attempt_results),
            "requires_manual_advance": True,
            "advance_keys": ["Space", "Enter"],
        }
