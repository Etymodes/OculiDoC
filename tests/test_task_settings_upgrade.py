"""Tests for upgraded gaze-task settings."""

from __future__ import annotations

import json
from math import pi
from pathlib import Path

from PySide6.QtWidgets import QMessageBox
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionSetupDialog,
    BinaryQuestionTask,
)
from oculidoc.tasks.question_bank import (
    BinaryQuestionType,
    CommonQuestionStore,
    CommonQuestionTemplate,
)
from oculidoc.tasks.tracking_ball import (
    TargetPath,
    TrackingBallConfig,
    TrackingBallSetupDialog,
    TrackingBallTask,
)


class InvalidSample:
    gaze_valid = False
    gaze_x_normalized = None


def test_tracking_defaults_and_z_path(
    qtbot: QtBot,
) -> None:
    config = TrackingBallConfig()

    assert config.diameter_px == 300
    assert config.period_seconds == 12.0

    task = TrackingBallTask(TrackingBallConfig(path=TargetPath.Z))
    qtbot.addWidget(task)

    start = task.target_center_normalized(0.0)
    forward_end = task.target_center_normalized(pi)
    cycle_end = task.target_center_normalized(2.0 * pi)

    assert start == (0.15, 0.2)
    assert forward_end == (0.85, 0.8)
    assert cycle_end == start

    samples = [task.target_center_normalized(index * 2.0 * pi / 200.0) for index in range(201)]

    assert all(0.15 <= x <= 0.85 and 0.2 <= y <= 0.8 for x, y in samples)
    assert (
        max(
            ((next_x - x) ** 2 + (next_y - y) ** 2) ** 0.5
            for (
                (x, y),
                (next_x, next_y),
            ) in zip(samples, samples[1:], strict=False)
        )
        < 0.08
    )


def test_tracking_setup_uses_new_defaults(
    qtbot: QtBot,
) -> None:
    dialog = TrackingBallSetupDialog()
    qtbot.addWidget(dialog)

    assert dialog.diameter_spin.value() == 300
    assert dialog.period_spin.value() == 12.0
    assert dialog.path_combo.findData(TargetPath.Z) >= 0


def test_scored_options_randomize_logical_sides(
    qtbot: QtBot,
) -> None:
    task = BinaryQuestionTask(
        BinaryQuestionConfig(
            question="一加一等于几？",
            option_1="二",
            option_2="三",
            question_type=(BinaryQuestionType.QUESTION_ANSWER),
            randomization_seed=1,
        )
    )
    qtbot.addWidget(task)

    assert task.displayed_options == {
        "left": "三",
        "right": "二",
    }
    assert task.displayed_correct_side == "right"

    context = task.recording_context_for_sample(InvalidSample())
    metadata = context["question_metadata"]
    roles = {aoi["metadata"]["logical_option_id"]: aoi["role"] for aoi in context["aois"]}

    assert metadata["is_scored"] is True
    assert metadata["correct_option_id"] == "option_1"
    assert metadata["left_option_id"] == "option_2"
    assert metadata["right_option_id"] == "option_1"
    assert metadata["randomization_seed"] == 1
    assert roles["option_1"] == "correct_option"
    assert roles["option_2"] == "incorrect_option"


def test_inquiry_has_no_correct_option(
    qtbot: QtBot,
) -> None:
    config = BinaryQuestionConfig(
        question="你现在感到舒服吗？",
        option_1="是",
        option_2="否",
        question_type=BinaryQuestionType.INQUIRY,
        randomization_seed=2,
    )
    task = BinaryQuestionTask(config)
    qtbot.addWidget(task)

    context = task.recording_context_for_sample(InvalidSample())

    assert config.is_scored is False
    assert config.correct_option_id is None
    assert task.displayed_correct_side is None
    assert {aoi["role"] for aoi in context["aois"]} == {"other"}


def test_legacy_left_right_configuration_remains_stable(
    qtbot: QtBot,
) -> None:
    config = BinaryQuestionConfig(
        question="测试问题",
        left_answer="是",
        right_answer="否",
        correct_side="left",
    )
    task = BinaryQuestionTask(config)
    qtbot.addWidget(task)

    assert config.randomize_sides is False
    assert task.displayed_options == {
        "left": "是",
        "right": "否",
    }
    assert task.displayed_correct_side == "left"


def test_question_store_persists_full_template(
    tmp_path: Path,
) -> None:
    path = tmp_path / "common_questions.json"
    store = CommonQuestionStore(path)
    template = CommonQuestionTemplate.create(
        question_type=(BinaryQuestionType.QUESTION_ANSWER),
        question="天空通常是什么颜色？",
        option_1="蓝色",
        option_2="绿色",
        correct_option_id="option_1",
    )

    store.add(template)

    reloaded = {item.template_id: item for item in store.load()}
    saved = reloaded[template.template_id]

    assert saved.question_type is (BinaryQuestionType.QUESTION_ANSWER)
    assert saved.question == "天空通常是什么颜色？"
    assert saved.option_1 == "蓝色"
    assert saved.option_2 == "绿色"
    assert saved.correct_option_id == "option_1"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert len(payload["questions"]) == 1


def test_question_setup_loads_and_saves_templates(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    messages: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append(args),
    )
    dialog = BinaryQuestionSetupDialog(question_bank_path=(tmp_path / "common_questions.json"))
    qtbot.addWidget(dialog)

    assert dialog.question_font_size_spin.value() == 48
    assert dialog.option_font_size_spin.value() == 44
    assert dialog.common_question_combo.count() >= 5
    assert dialog.option_1_label.text() == "选项1："

    dialog.question_type_buttons[BinaryQuestionType.QUESTION_ANSWER].setChecked(True)
    dialog._refresh_option_labels()

    assert dialog.option_1_label.text() == "正确选项："
    assert dialog.option_2_label.text() == "错误选项："

    dialog.question_edit.setText("三加二等于几？")
    dialog.option_1_edit.setText("五")
    dialog.option_2_edit.setText("六")
    dialog._add_common_question()

    assert messages
    assert dialog.common_question_combo.currentData() is not None

    config = dialog.build_config()

    assert config.question_type is (BinaryQuestionType.QUESTION_ANSWER)
    assert config.option_1 == "五"
    assert config.option_2 == "六"
    assert config.correct_option_id == "option_1"
    assert config.randomize_sides is True


def test_question_store_updates_custom_template_in_place(
    tmp_path: Path,
) -> None:
    path = tmp_path / "common_questions.json"
    store = CommonQuestionStore(path)
    original = CommonQuestionTemplate.create(
        question_type=BinaryQuestionType.INQUIRY,
        question="你想休息吗？",
        option_1="想",
        option_2="不想",
    )
    store.add(original)

    updated = CommonQuestionTemplate(
        template_id=original.template_id,
        question_type=BinaryQuestionType.INQUIRY,
        question="你现在想休息吗？",
        option_1="想休息",
        option_2="继续",
    )
    saved = store.update(
        original.template_id,
        updated,
    )

    assert saved.template_id == original.template_id
    reloaded = {item.template_id: item for item in store.load()}
    assert reloaded[original.template_id].question == "你现在想休息吗？"
    assert reloaded[original.template_id].option_1 == "想休息"


def test_question_setup_uses_radio_buttons_and_edits_custom_template(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    path = tmp_path / "common_questions.json"
    store = CommonQuestionStore(path)
    original = CommonQuestionTemplate.create(
        question_type=BinaryQuestionType.INQUIRY,
        question="你感到冷吗？",
        option_1="冷",
        option_2="不冷",
    )
    store.add(original)
    messages: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append(args),
    )

    dialog = BinaryQuestionSetupDialog(
        question_bank_path=path,
    )
    qtbot.addWidget(dialog)

    assert not hasattr(
        dialog,
        "question_type_combo",
    )
    assert tuple(dialog.question_type_buttons) == tuple(BinaryQuestionType)
    assert dialog.question_type_buttons[BinaryQuestionType.INQUIRY].isChecked()

    selected_index = dialog.common_question_combo.findData(original.template_id)
    dialog.common_question_combo.setCurrentIndex(selected_index)
    dialog.question_edit.setText("你现在感到冷吗？")
    dialog.option_1_edit.setText("是")
    dialog.option_2_edit.setText("否")
    dialog._edit_common_question()

    reloaded = {item.template_id: item for item in store.load()}
    assert reloaded[original.template_id].question == "你现在感到冷吗？"
    assert dialog.common_question_combo.currentData() == original.template_id
    assert messages


def test_builtin_question_is_copied_instead_of_overwritten(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    path = tmp_path / "common_questions.json"
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *_args: None,
    )
    dialog = BinaryQuestionSetupDialog(
        question_bank_path=path,
    )
    qtbot.addWidget(dialog)

    builtin_index = dialog.common_question_combo.findData("builtin-comfort")
    dialog.common_question_combo.setCurrentIndex(builtin_index)
    dialog.question_edit.setText("你现在身体舒服吗？")
    dialog._edit_common_question()

    templates = CommonQuestionStore(path).load()
    custom = [item for item in templates if not item.built_in]
    assert len(custom) == 1
    assert custom[0].template_id != "builtin-comfort"
    assert custom[0].question == "你现在身体舒服吗？"
