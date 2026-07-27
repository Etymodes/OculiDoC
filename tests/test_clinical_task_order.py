from oculidoc.ui.test_plan import (
    CLINICAL_TASK_ORDER,
    BinaryAxisOrder,
    clinical_task_order,
    default_rest_after_step_ids,
)


def test_default_clinical_order_has_optional_zero_and_nine_interaction_stages() -> None:
    assert [definition.step_id for definition in CLINICAL_TASK_ORDER] == [
        "eye_observation",
        "visual_preference",
        "tracking_ball",
        "gaze_games:garden",
        "gaze_games:treasure_hunt",
        "instruction_fixation",
        "image_choice",
        "binary_horizontal",
        "binary_vertical",
        "multiple_choice",
        "screen_keyboard",
    ]
    assert [definition.clinical_number for definition in CLINICAL_TASK_ORDER] == [
        "0",
        "1",
        "2",
        "3a",
        "3b",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
    ]
    assert CLINICAL_TASK_ORDER[0].selected_by_default is False
    assert all(definition.selected_by_default for definition in CLINICAL_TASK_ORDER[1:])
    assert sum(definition.module_id == "gaze_games" for definition in CLINICAL_TASK_ORDER) == 2
    assert {
        definition.game_mode
        for definition in CLINICAL_TASK_ORDER
        if definition.module_id == "gaze_games"
    } == {"garden", "treasure_hunt"}


def test_vertical_first_only_swaps_the_two_binary_steps() -> None:
    default_ids = [
        definition.step_id for definition in clinical_task_order(BinaryAxisOrder.HORIZONTAL_FIRST)
    ]
    vertical_ids = [
        definition.step_id for definition in clinical_task_order(BinaryAxisOrder.VERTICAL_FIRST)
    ]

    assert vertical_ids[7:9] == ["binary_vertical", "binary_horizontal"]
    assert vertical_ids[:7] == default_ids[:7]
    assert vertical_ids[9:] == default_ids[9:]
    assert default_rest_after_step_ids() == (
        "gaze_games:treasure_hunt",
        "binary_vertical",
    )
    assert default_rest_after_step_ids(BinaryAxisOrder.VERTICAL_FIRST) == (
        "gaze_games:treasure_hunt",
        "binary_horizontal",
    )
