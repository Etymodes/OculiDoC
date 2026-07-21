from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from oculidoc.experiments.task_runtime import RecordedTaskRuntime
from oculidoc.tasks.multiple_choice import (
    MultipleChoiceConfig,
    MultipleChoiceSetupDialog,
    MultipleChoiceTask,
)


def test_config_supports_two_to_six_options_and_validates_values() -> None:
    assert len(MultipleChoiceConfig(option_count=2).options) == 2
    assert len(MultipleChoiceConfig(option_count=6).options) == 6

    with pytest.raises(ValueError, match="between 2 and 6"):
        MultipleChoiceConfig(option_count=1)

    with pytest.raises(ValueError, match="cannot be empty"):
        MultipleChoiceConfig(option_count=3, option_3=" ")

    with pytest.raises(ValueError, match="grid or ring"):
        MultipleChoiceConfig(layout="diagonal")


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
    metadata = context["question_metadata"]
    aois = context["aois"]

    assert metadata["layout"] == "ring"
    assert metadata["randomization_seed"] == 17
    assert len(aois) == 6
    assert {aoi["metadata"]["position"] for aoi in aois} == set(range(1, 7))
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
