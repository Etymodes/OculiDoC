from __future__ import annotations

import json
from pathlib import Path

import pytest

from oculidoc.task_configs import (
    TaskConfigConflict,
    TaskConfigStore,
)


def test_task_config_store_round_trip_and_preserves_modules(tmp_path: Path) -> None:
    path = tmp_path / "task_configs.json"
    store = TaskConfigStore(path)
    tracking = store.load("tracking_ball")
    binary = store.load("binary_horizontal")
    vertical = store.load("binary_vertical")
    keyboard = store.load("screen_keyboard")

    assert tracking.revision == 0
    assert tracking.config["diameter_px"] == 300
    assert binary.config["question"] == "你现在感到舒服吗？"
    assert vertical.config == binary.config
    assert keyboard.config["enable_tone_step"] is True
    assert keyboard.config["output_font_size_pt"] == 48

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

    assert saved_tracking.revision == 1
    assert saved_binary.revision == 1
    assert saved_vertical.revision == 1
    assert store.load("tracking_ball").config["diameter_px"] == 180
    assert store.load("binary_horizontal").config["option_1"] == "能"
    assert store.load("binary_vertical").config["option_2"] == "不能"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert set(payload["modules"]) == {
        "tracking_ball",
        "binary_horizontal",
        "binary_vertical",
    }


def test_screen_keyboard_config_validates_tone_boolean(tmp_path: Path) -> None:
    store = TaskConfigStore(tmp_path / "task_configs.json")
    record = store.load("screen_keyboard")
    invalid = dict(record.config)
    invalid["enable_tone_step"] = "false"

    with pytest.raises(TypeError, match="enable_tone_step"):
        store.save("screen_keyboard", invalid, expected_revision=record.revision)


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
