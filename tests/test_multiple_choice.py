from __future__ import annotations

from typing import cast

import pytest
from PySide6.QtWidgets import QSizePolicy
from pytestqt.qtbot import QtBot

from oculidoc.experiments.task_runtime import RecordedTaskRuntime
from oculidoc.tasks.multiple_choice import (
    BUILT_IN_MULTIPLE_CHOICE_TEMPLATES,
    MultipleChoiceConfig,
    MultipleChoiceSetupDialog,
    MultipleChoiceTask,
)


def test_config_supports_two_to_twelve_options_and_validates_values() -> None:
    assert len(MultipleChoiceConfig(option_count=2).options) == 2
    assert len(MultipleChoiceConfig(option_count=12).options) == 12

    with pytest.raises(ValueError, match="between 2 and 12"):
        MultipleChoiceConfig(option_count=1)

    with pytest.raises(ValueError, match="cannot be empty"):
        MultipleChoiceConfig(option_count=3, option_3=" ")

    with pytest.raises(ValueError, match="grid or ring"):
        MultipleChoiceConfig(layout="diagonal")

    with pytest.raises(ValueError, match="at most 6"):
        MultipleChoiceConfig(option_count=7, layout="ring")

    with pytest.raises(ValueError, match="enough option cells"):
        MultipleChoiceConfig(option_count=7, grid_shape="2x3")


@pytest.mark.parametrize(
    ("shape", "option_count", "rows", "columns"),
    (
        ("2x2", 4, 2, 2),
        ("2x3", 6, 2, 3),
        ("2x4", 8, 2, 4),
        ("3x2", 6, 3, 2),
        ("3x3", 9, 3, 3),
        ("3x4", 12, 3, 4),
    ),
)
def test_grid_shapes_use_large_option_rows_and_narrow_question_strips(
    qtbot: QtBot,
    shape: str,
    option_count: int,
    rows: int,
    columns: int,
) -> None:
    task = MultipleChoiceTask(
        MultipleChoiceConfig(
            option_count=option_count,
            grid_shape=shape,
            randomize_positions=False,
        )
    )
    qtbot.addWidget(task)
    task.resize(1_200, 900)
    task.show()
    qtbot.wait(10)

    positions = []

    for index in range(1, option_count + 1):
        layout_index = task.options_layout.indexOf(task._buttons[f"option_{index}"])
        row, column, row_span, column_span = cast(
            tuple[int, int, int, int],
            task.options_layout.getItemPosition(layout_index),
        )
        positions.append((row, column))
        assert row_span == 1
        assert column_span == 1

    assert {row for row, _column in positions} == {index * 2 for index in range(rows)}
    assert max(column for _row, column in positions) == columns - 1
    assert len(task.question_labels) == rows - 1

    for separator_index, label in enumerate(task.question_labels):
        layout_index = task.options_layout.indexOf(label)
        row, column, row_span, column_span = cast(
            tuple[int, int, int, int],
            task.options_layout.getItemPosition(layout_index),
        )
        assert (row, column, row_span, column_span) == (
            separator_index * 2 + 1,
            0,
            1,
            columns,
        )


def test_grid_options_fill_high_resolution_cells(qtbot: QtBot) -> None:
    task = MultipleChoiceTask(
        MultipleChoiceConfig(
            option_count=12,
            grid_shape="3x4",
            randomize_positions=False,
        )
    )
    qtbot.addWidget(task)
    task.resize(1_280, 720)
    task.show()
    qtbot.wait(10)
    small_height = task._buttons["option_1"].height()

    task.resize(3_840, 2_160)
    qtbot.wait(10)
    first = task._buttons["option_1"]

    assert first.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Expanding
    assert first.height() > small_height * 4
    assert first.width() >= task.width() * 0.20
    assert all(label.height() < task.height() * 0.05 for label in task.question_labels)


def test_dwell_toggles_selection_only_after_gaze_leaves(qtbot: QtBot) -> None:
    task = MultipleChoiceTask(
        MultipleChoiceConfig(
            dwell_time_ms=500,
            randomize_positions=False,
        )
    )
    qtbot.addWidget(task)
    changes: list[tuple[str, bool]] = []
    task.selection_changed.connect(
        lambda option_id, selected: changes.append((option_id, selected))
    )

    task.start()
    task.advance_dwell("option_1", 500, monotonic_timestamp_ns=1_000_000_000)
    task.advance_dwell("option_1", 900, monotonic_timestamp_ns=1_900_000_000)

    assert task.selected_option_ids == ("option_1",)
    assert changes == [("option_1", True)]

    task.advance_dwell(None, 10, monotonic_timestamp_ns=2_000_000_000)
    task.advance_dwell("option_1", 500, monotonic_timestamp_ns=2_500_000_000)

    assert task.selected_option_ids == ()
    assert changes[-1] == ("option_1", False)


def test_multiple_selections_remain_active_until_manual_exit(qtbot: QtBot) -> None:
    task = MultipleChoiceTask(
        MultipleChoiceConfig(
            option_count=3,
            dwell_time_ms=250,
            randomize_positions=False,
        )
    )
    qtbot.addWidget(task)
    task.start()

    task.advance_dwell("option_1", 250, monotonic_timestamp_ns=1_000_000_000)
    task.advance_dwell(None, 1, monotonic_timestamp_ns=1_100_000_000)
    task.advance_dwell("option_3", 250, monotonic_timestamp_ns=1_350_000_000)

    assert task.selected_option_ids == ("option_1", "option_3")
    assert "✓" in task._buttons["option_1"].text()
    assert "✓" in task._buttons["option_3"].text()

    result = task.recording_result("manual_exit")
    assert result["selected_option_ids"] == ["option_1", "option_3"]
    assert result["selected_count"] == 2
    assert result["toggle_count"] == 2
    assert result["allows_multiple"] is True
    assert result["has_fixed_answer"] is False
    assert result["is_scored"] is False
    assert result["completion_reason"] == "manual_exit"


def test_ring_layout_records_randomized_positions_and_aois(qtbot: QtBot) -> None:
    task = MultipleChoiceTask(
        MultipleChoiceConfig(
            option_count=6,
            layout="ring",
            randomize_positions=True,
            randomization_seed=17,
        )
    )
    qtbot.addWidget(task)
    task.resize(1_000, 800)
    task.show()
    qtbot.wait(10)

    context = task.recording_context_for_sample(None)  # type: ignore[arg-type]
    metadata = cast(dict[str, object], context["question_metadata"])
    aois = cast(list[dict[str, object]], context["aois"])

    assert metadata["layout"] == "ring"
    assert metadata["randomization_seed"] == 17
    assert len(aois) == 6
    assert {cast(dict[str, object], aoi["metadata"])["position"] for aoi in aois} == set(
        range(1, 7)
    )
    assert RecordedTaskRuntime._infer_task_kind(task) == "multiple_choice"


def test_setup_dialog_controls_option_count_layout_and_font_sizes(qtbot: QtBot) -> None:
    dialog = MultipleChoiceSetupDialog(
        config=MultipleChoiceConfig(
            option_count=3,
            layout="ring",
            question_font_size_pt=52,
            option_font_size_pt=48,
        )
    )
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "多选项问答设置"
    assert dialog.option_edits[2].isEnabled()
    assert not dialog.option_edits[3].isEnabled()
    assert dialog.build_config().layout == "ring"
    assert dialog.build_config().question_font_size_pt == 52
    assert dialog.build_config().option_font_size_pt == 48


def test_fixed_multiple_choice_templates_cover_requested_categories(qtbot: QtBot) -> None:
    assert len(BUILT_IN_MULTIPLE_CHOICE_TEMPLATES) >= 12
    categories = {template.category for template in BUILT_IN_MULTIPLE_CHOICE_TEMPLATES}
    assert {"城市选择", "水果选择", "交通工具", "游戏活动", "即时护理"} <= categories
    assert any("吸痰" in template.options for template in BUILT_IN_MULTIPLE_CHOICE_TEMPLATES)
    assert any("睡觉" in template.options for template in BUILT_IN_MULTIPLE_CHOICE_TEMPLATES)
    assert any("康复训练" in template.options for template in BUILT_IN_MULTIPLE_CHOICE_TEMPLATES)

    dialog = MultipleChoiceSetupDialog()
    qtbot.addWidget(dialog)
    template = next(
        item for item in BUILT_IN_MULTIPLE_CHOICE_TEMPLATES if item.template_id == "city-choice"
    )
    dialog.template_combo.setCurrentIndex(dialog.template_combo.findData(template.template_id))
    config = dialog.build_config()

    assert config.template_id == "city-choice"
    assert config.option_count == 12
    assert config.grid_shape == "3x4"
    assert tuple(label for _option_id, label in config.options) == template.options
