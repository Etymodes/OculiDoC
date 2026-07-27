from __future__ import annotations

import json
from pathlib import Path

import pytest

from oculidoc.task_configs import (
    TaskConfigConflict,
    TaskConfigStore,
    task_config_from_dict,
)
from oculidoc.tasks.gaze_games import GazeGameConfig
from oculidoc.tasks.image_choice import ImageChoiceConfig
from oculidoc.tasks.visual_preference import VisualPreferenceConfig


def test_task_config_store_round_trip_and_preserves_modules(tmp_path: Path) -> None:
    path = tmp_path / "task_configs.json"
    store = TaskConfigStore(path)
    tracking = store.load("tracking_ball")
    binary = store.load("binary_horizontal")
    vertical = store.load("binary_vertical")
    keyboard = store.load("screen_keyboard")
    multiple = store.load("multiple_choice")
    image_choice = store.load("image_choice")
    instruction_fixation = store.load("instruction_fixation")

    assert tracking.revision == 0
    assert tracking.config["diameter_px"] == 300
    assert binary.config["question"] == "你现在感到舒服吗？"
    assert binary.config["fixed_form_size"] == 0
    assert vertical.config == binary.config
    assert keyboard.config["input_mode"] == "direct"
    assert keyboard.config["enable_tone_step"] is True
    assert keyboard.config["output_font_size_pt"] == 48
    assert multiple.config["option_count"] == 4
    assert multiple.config["grid_shape"] == "auto"
    assert multiple.config["template_id"] is None
    assert multiple.config["randomize_positions"] is True
    assert image_choice.config["question_ids"] == []
    assert image_choice.config["category_filters"] == []
    assert image_choice.config["style_filters"] == []
    assert image_choice.config["question_count"] == 6
    assert instruction_fixation.config["target_description"] == "黄色圆形"
    assert instruction_fixation.config["no_target_trial_count"] == 2
    assert instruction_fixation.config["position_ids"] == [
        "top_left",
        "top_right",
        "center",
        "bottom_left",
        "bottom_right",
    ]

    tracking_config = dict(tracking.config)
    tracking_config["diameter_px"] = 180
    saved_tracking = store.save(
        "tracking_ball",
        tracking_config,
        expected_revision=tracking.revision,
    )
    binary_config = dict(binary.config)
    binary_config["option_1"] = "能"
    saved_binary = store.save(
        "binary_horizontal",
        binary_config,
        expected_revision=binary.revision,
    )
    vertical_config = dict(vertical.config)
    vertical_config["option_2"] = "不能"
    saved_vertical = store.save(
        "binary_vertical",
        vertical_config,
        expected_revision=vertical.revision,
    )
    multiple_config = dict(multiple.config)
    multiple_config["option_count"] = 3
    saved_multiple = store.save(
        "multiple_choice",
        multiple_config,
        expected_revision=multiple.revision,
    )

    assert saved_tracking.revision == 1
    assert saved_binary.revision == 1
    assert saved_vertical.revision == 1
    assert saved_multiple.revision == 1
    assert store.load("tracking_ball").config["diameter_px"] == 180
    assert store.load("binary_horizontal").config["option_1"] == "能"
    assert store.load("binary_vertical").config["option_2"] == "不能"
    assert store.load("multiple_choice").config["option_count"] == 3

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "2.0"
    assert payload["active_patient_id"] is None
    assert payload["patients"] == {}
    assert set(payload["legacy_modules"]) == {
        "tracking_ball",
        "binary_horizontal",
        "binary_vertical",
        "multiple_choice",
    }


def test_task_config_store_switches_patient_habits_and_new_patient_uses_defaults(
    tmp_path: Path,
) -> None:
    store = TaskConfigStore(tmp_path / "task_configs.json")
    store.set_active_patient("patient-existing")
    existing = store.load("tracking_ball")
    existing_config = dict(existing.config)
    existing_config["diameter_px"] = 180
    store.save(
        "tracking_ball",
        existing_config,
        expected_revision=existing.revision,
    )

    store.set_active_patient("patient-new")
    fresh = store.load("tracking_ball")
    assert fresh.revision == 0
    assert fresh.config["diameter_px"] == 300
    fresh_config = dict(fresh.config)
    fresh_config["diameter_px"] = 240
    store.save(
        "tracking_ball",
        fresh_config,
        expected_revision=fresh.revision,
    )

    store.set_active_patient("patient-existing")
    assert store.load("tracking_ball").config["diameter_px"] == 180
    assert (
        store.load(
            "tracking_ball",
            patient_id="patient-new",
        ).config["diameter_px"]
        == 240
    )


def test_task_config_store_migrates_legacy_settings_only_when_requested(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task_configs.json"
    legacy_store = TaskConfigStore(path)
    legacy = legacy_store.load("tracking_ball")
    legacy_config = dict(legacy.config)
    legacy_config["diameter_px"] = 210
    legacy_store.save(
        "tracking_ball",
        legacy_config,
        expected_revision=legacy.revision,
    )
    version_two = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "modules": version_two["legacy_modules"],
            }
        ),
        encoding="utf-8",
    )

    migrated = TaskConfigStore(path)
    migrated.set_active_patient("patient-with-history", inherit_legacy=True)
    assert migrated.load("tracking_ball").config["diameter_px"] == 210

    migrated.set_active_patient("brand-new-patient")
    assert migrated.load("tracking_ball").config["diameter_px"] == 300


def test_screen_keyboard_config_validates_tone_boolean(tmp_path: Path) -> None:
    store = TaskConfigStore(tmp_path / "task_configs.json")
    record = store.load("screen_keyboard")
    invalid = dict(record.config)
    invalid["enable_tone_step"] = "false"

    with pytest.raises(TypeError, match="enable_tone_step"):
        store.save("screen_keyboard", invalid, expected_revision=record.revision)


def test_multiple_choice_config_validates_randomization_boolean(tmp_path: Path) -> None:
    store = TaskConfigStore(tmp_path / "task_configs.json")
    record = store.load("multiple_choice")
    invalid = dict(record.config)
    invalid["randomize_positions"] = "false"

    with pytest.raises(TypeError, match="randomize_positions"):
        store.save("multiple_choice", invalid, expected_revision=record.revision)


def test_m3d12d_fixed_image_config_loads_with_new_random_pool_defaults() -> None:
    loaded = task_config_from_dict(
        "image_choice",
        {
            "question_ids": ["image-banana", "image-apple"],
            "dwell_time_ms": 1200,
            "duration_seconds": 30,
            "question_font_size_pt": 48,
            "randomize_sides": True,
            "randomization_seed": None,
        },
    )

    assert isinstance(loaded, ImageChoiceConfig)
    assert loaded.question_ids == ("image-banana", "image-apple")
    assert loaded.category_filters == ()
    assert loaded.style_filters == ()
    assert loaded.question_count == 6


def test_task_config_store_rejects_stale_revision(tmp_path: Path) -> None:
    store = TaskConfigStore(tmp_path / "task_configs.json")
    original = store.load("tracking_ball")
    updated_config = dict(original.config)
    updated_config["duration_seconds"] = 90
    current = store.save(
        "tracking_ball",
        updated_config,
        expected_revision=original.revision,
    )

    with pytest.raises(TaskConfigConflict) as raised:
        store.save(
            "tracking_ball",
            original.config,
            expected_revision=original.revision,
        )

    assert raised.value.current == current
    assert store.load("tracking_ball") == current


def test_task_config_store_validates_boolean_fields(tmp_path: Path) -> None:
    store = TaskConfigStore(tmp_path / "task_configs.json")
    record = store.load("tracking_ball")
    invalid = dict(record.config)
    invalid["show_gaze_cursor"] = "false"

    with pytest.raises(TypeError, match="show_gaze_cursor"):
        store.save(
            "tracking_ball",
            invalid,
            expected_revision=record.revision,
        )


def test_m3d13_nested_configs_round_trip_and_keep_revision_conflicts(
    tmp_path: Path,
) -> None:
    store = TaskConfigStore(tmp_path / "task_configs.json")
    game = store.load("gaze_games")
    preference = store.load("visual_preference")

    assert game.config["default_mode"] == "garden"
    garden = game.config["garden"]
    treasure_hunt = game.config["treasure_hunt"]
    assert isinstance(garden, dict)
    assert isinstance(treasure_hunt, dict)
    assert garden["dwell_time_ms"] == 800
    assert treasure_hunt["catch_trial_count"] == 2
    assert preference.config["pair_ids"] == []
    assert preference.config["pairs"] == []

    game_config = json.loads(json.dumps(game.config))
    game_config["default_mode"] = "treasure_hunt"
    game_config["garden"]["dwell_time_ms"] = 950
    saved_game = store.save(
        "gaze_games",
        game_config,
        expected_revision=game.revision,
    )
    preference_config = {
        **preference.config,
        "pair_ids": ["pair-1", "pair-2"],
        "pairs": [
            {
                "pair_id": "pair-1",
                "image_a_id": "cat",
                "image_b_id": "car",
                "pair_label": "动物—交通",
                "comparison_type": "generic_interest",
                "matching_note": "",
            },
            {
                "pair_id": "pair-2",
                "image_a_id": "flower",
                "image_b_id": "cup",
                "pair_label": "植物—物品",
                "comparison_type": "generic_interest",
                "matching_note": "",
            },
        ],
    }
    saved_preference = store.save(
        "visual_preference",
        preference_config,
        expected_revision=preference.revision,
    )

    loaded_game = task_config_from_dict("gaze_games", saved_game.config)
    loaded_preference = task_config_from_dict(
        "visual_preference",
        saved_preference.config,
    )
    assert isinstance(loaded_game, GazeGameConfig)
    assert loaded_game.garden.dwell_time_ms == 950
    assert isinstance(loaded_preference, VisualPreferenceConfig)
    assert loaded_preference.pair_ids == ("pair-1", "pair-2")
    assert len(loaded_preference.pairs) == 2

    with pytest.raises(TaskConfigConflict):
        store.save(
            "gaze_games",
            game.config,
            expected_revision=game.revision,
        )
