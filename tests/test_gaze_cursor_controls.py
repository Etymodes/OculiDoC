from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from oculidoc.api.mobile_page import mobile_control_html
from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample
from oculidoc.image_library import ImageLibraryStore
from oculidoc.task_configs import (
    TASK_CONFIG_MODULE_IDS,
    TaskConfigStore,
    task_config_from_dict,
    task_config_to_dict,
)
from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionSetupDialog,
    BinaryQuestionTask,
    binary_question_sequence,
)
from oculidoc.tasks.image_choice import (
    ImageChoiceConfig,
    ImageChoiceSetupDialog,
    ImageChoiceTask,
    image_question_sequence,
)
from oculidoc.tasks.multiple_choice import (
    MultipleChoiceConfig,
    MultipleChoiceSetupDialog,
    MultipleChoiceTask,
)
from oculidoc.tasks.question_bank import CommonQuestionStore
from oculidoc.tasks.screen_keyboard import (
    ScreenKeyboardConfig,
    ScreenKeyboardSetupDialog,
    ScreenKeyboardTask,
)
from oculidoc.tasks.sequential_choice import SequentialChoiceTask


def gaze_sample(
    x: float | None,
    y: float | None,
    *,
    valid: bool,
    sequence: int = 1,
) -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=sequence * 1_000_000,
            utc_timestamp=datetime.now(UTC),
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=valid,
        right_eye_valid=valid,
    )


def test_all_nonzero_task_settings_define_a_gaze_cursor_toggle(tmp_path: Path) -> None:
    store = TaskConfigStore(tmp_path / "task_configs.json")
    assert "eye_observation" not in TASK_CONFIG_MODULE_IDS

    for module_id in TASK_CONFIG_MODULE_IDS - {"gaze_games"}:
        assert store.load(module_id).config["show_gaze_cursor"] in {True, False}

    games = store.load("gaze_games").config
    garden = cast(dict[str, object], games["garden"])
    treasure_hunt = cast(dict[str, object], games["treasure_hunt"])
    assert garden["show_gaze_cursor"] is False
    assert treasure_hunt["show_gaze_cursor"] is False


@pytest.mark.parametrize(
    "module_id",
    ("binary_horizontal", "binary_vertical", "multiple_choice", "image_choice", "screen_keyboard"),
)
def test_old_saved_settings_without_cursor_field_upgrade_to_hidden(
    tmp_path: Path,
    module_id: str,
) -> None:
    store = TaskConfigStore(tmp_path / "task_configs.json")
    old_config = dict(store.load(module_id).config)
    old_config.pop("show_gaze_cursor")

    upgraded = task_config_from_dict(module_id, old_config)

    assert task_config_to_dict(upgraded)["show_gaze_cursor"] is False


def test_new_cursor_fields_preserve_legacy_positional_parameter_order() -> None:
    assert fields(ImageChoiceConfig)[-1].name == "show_gaze_cursor"
    assert fields(MultipleChoiceConfig)[-1].name == "show_gaze_cursor"


def test_binary_question_sequences_keep_the_cursor_choice(tmp_path: Path) -> None:
    question_store = CommonQuestionStore(tmp_path / "common_questions.json")
    template_id = question_store.load()[0].template_id
    config = BinaryQuestionConfig(
        question="备用单题",
        question_template_ids=(template_id,),
        show_gaze_cursor=True,
    )

    sequence = binary_question_sequence(config, question_store)

    assert sequence
    assert all(question.show_gaze_cursor for _question_id, question in sequence)


def test_missing_desktop_setup_dialogs_round_trip_the_cursor_choice(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    dialogs = (
        BinaryQuestionSetupDialog(
            config=BinaryQuestionConfig(
                question="你现在感到舒服吗？",
                show_gaze_cursor=True,
            ),
            question_bank_path=tmp_path / "common_questions.json",
        ),
        MultipleChoiceSetupDialog(
            config=MultipleChoiceConfig(show_gaze_cursor=True),
        ),
        ScreenKeyboardSetupDialog(
            config=ScreenKeyboardConfig(show_gaze_cursor=True),
        ),
        ImageChoiceSetupDialog(
            config=ImageChoiceConfig(show_gaze_cursor=True),
            image_library_path=tmp_path / "image_library",
        ),
    )

    for dialog in dialogs:
        qtbot.addWidget(dialog)
        assert dialog.show_gaze_cursor_check.isChecked()
        assert dialog.build_config().show_gaze_cursor is True


@pytest.mark.parametrize(
    "task_factory",
    (
        lambda: BinaryQuestionTask(
            BinaryQuestionConfig(
                question="是否听到？",
                show_gaze_cursor=True,
            )
        ),
        lambda: MultipleChoiceTask(
            MultipleChoiceConfig(show_gaze_cursor=True),
        ),
        lambda: ScreenKeyboardTask(
            ScreenKeyboardConfig(show_gaze_cursor=True),
        ),
    ),
    ids=("binary", "multiple_choice", "screen_keyboard"),
)
def test_widget_tasks_show_valid_gaze_and_hide_invalid_gaze(
    qtbot: QtBot,
    task_factory: Callable[[], BinaryQuestionTask | MultipleChoiceTask | ScreenKeyboardTask],
) -> None:
    task = task_factory()
    qtbot.addWidget(task)
    task.resize(1_000, 700)
    task.show()

    task.consume_sample(gaze_sample(0.25, 0.75, valid=True))

    assert task.gaze_cursor_overlay.enabled is True
    assert task.gaze_cursor_overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert task.gaze_cursor_overlay.normalized_position == (0.25, 0.75)
    assert not task.gaze_cursor_overlay.isHidden()

    task.consume_sample(gaze_sample(None, None, valid=False, sequence=2))

    assert task.gaze_cursor_overlay.normalized_position is None
    assert task.gaze_cursor_overlay.isHidden()


def test_image_choice_passes_cursor_setting_to_shared_binary_runtime(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    store = ImageLibraryStore(tmp_path / "image_library")
    config = ImageChoiceConfig(
        question_count=1,
        show_gaze_cursor=True,
        randomization_seed=7,
    )
    question = image_question_sequence(config, store)[0]
    task = ImageChoiceTask(question, config, store)
    qtbot.addWidget(task)

    task.consume_sample(gaze_sample(0.4, 0.6, valid=True))

    assert task.config.show_gaze_cursor is True
    assert task.gaze_cursor_overlay.normalized_position == (0.4, 0.6)


def test_sequential_choice_keeps_cursor_live_while_waiting_for_operator(
    qtbot: QtBot,
) -> None:
    config = BinaryQuestionConfig(
        question="是否听到？",
        dwell_time_ms=250,
        show_gaze_cursor=True,
    )
    task = SequentialChoiceTask(
        config=config,
        question_ids=("q1",),
        task_factory=lambda _index: BinaryQuestionTask(config),
        layout_orientation="horizontal",
    )
    qtbot.addWidget(task)
    task.resize(1_000, 700)
    task.show()
    task.start()

    task.consume_sample(gaze_sample(0.25, 0.75, valid=True))
    task.current_task.advance_dwell("left", 250, monotonic_timestamp_ns=2_000_000)

    assert "按空格或 Enter 继续" in task.status_label.text()
    assert task.current_task.gaze_cursor_overlay.normalized_position == (0.25, 0.75)

    task.consume_sample(gaze_sample(None, None, valid=False, sequence=3))

    assert task.current_task.gaze_cursor_overlay.normalized_position is None
    assert task.current_task.gaze_cursor_overlay.isHidden()


def test_mobile_settings_expose_cursor_without_adding_an_eye_observation_setting() -> None:
    page = mobile_control_html("secret-token")

    sections = {
        "binary": page.split("const binaryFields = [", 1)[1].split("];", 1)[0],
        "multiple_choice": page.split("multiple_choice: [", 1)[1].split("image_choice: [", 1)[0],
        "image_choice": page.split("image_choice: [", 1)[1].split("instruction_fixation: [", 1)[0],
        "screen_keyboard": page.split("screen_keyboard: [", 1)[1].split("\n  ]", 1)[0],
    }

    for section in sections.values():
        assert 'name: "show_gaze_cursor"' in section

    assert "eye_observation:" not in page
