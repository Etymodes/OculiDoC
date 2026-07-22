from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from oculidoc.image_library import ImageLibraryStore
from oculidoc.tasks.binary_question import BinaryQuestionConfig, BinaryQuestionTask
from oculidoc.tasks.image_choice import (
    ImageChoiceConfig,
    ImageChoiceTask,
    image_question_sequence,
    render_image_card,
)
from oculidoc.tasks.question_bank import BinaryQuestionType
from oculidoc.tasks.sequential_choice import SequentialChoiceTask


def test_image_questions_are_generated_from_category_without_visible_names(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    store = ImageLibraryStore(tmp_path / "image_library")
    config = ImageChoiceConfig(
        category_filters=("水果",),
        style_filters=("彩色图标",),
        question_count=2,
        randomization_seed=7,
    )
    questions = image_question_sequence(config, store)
    question = questions[0]
    assets = {asset.image_id: asset for asset in store.load()}
    task = ImageChoiceTask(question, config, store, assets=assets)
    qtbot.addWidget(task)

    assert questions == image_question_sequence(config, store)
    assert len(questions) == 2
    assert question.prompt.startswith("请看")
    assert (
        render_image_card(assets["banana"], store).size()
        == render_image_card(assets["apple"], store).size()
    )
    assert task.left_button.icon().isNull() is False
    assert task.right_button.icon().isNull() is False
    assert task.left_button.text() == ""
    assert task.right_button.text() == ""
    result = task.recording_result("time_limit")
    assert result["correct_image_id"] in {"banana", "apple"}
    assert result["option_1_image_category"] == "水果"
    assert result["option_2_image_style"] == "彩色图标"


def test_sequential_questions_require_correct_answer_and_manual_advance(
    qtbot: QtBot,
) -> None:
    configs = (
        BinaryQuestionConfig(
            question="一加一等于几？",
            option_1="二",
            option_2="三",
            question_type=BinaryQuestionType.QUESTION_ANSWER,
            correct_option_id="option_1",
            dwell_time_ms=250,
            randomize_sides=False,
        ),
        BinaryQuestionConfig(
            question="北京是中国首都吗？",
            option_1="是",
            option_2="否",
            question_type=BinaryQuestionType.YES_NO,
            correct_option_id="option_1",
            dwell_time_ms=250,
            randomize_sides=False,
        ),
    )
    task = SequentialChoiceTask(
        config=configs[0],
        question_ids=("q1", "q2"),
        task_factory=lambda index: BinaryQuestionTask(configs[index]),
        layout_orientation="horizontal",
    )
    qtbot.addWidget(task)
    completed: list[bool] = []
    task.sequence_completed.connect(lambda: completed.append(True))
    task.start()

    task.current_task.advance_dwell("left", 250, monotonic_timestamp_ns=1_000_000_000)

    assert "✓" in task.current_task.left_button.text()
    assert "空格或 Enter" in task.status_label.text()
    assert task.current_question_number == 1

    assert task.advance_question() is False
    assert task.current_question_number == 2

    task.current_task.advance_dwell("left", 250, monotonic_timestamp_ns=2_000_000_000)
    assert task.recording_result("time_limit")["completion_status"] == "partial"
    assert task.advance_question() is True
    assert completed == [True]

    result = task.recording_result("answered")
    assert result["completion_status"] == "answered"
    assert result["correct_count"] == 2
    assert result["question_count"] == 2
