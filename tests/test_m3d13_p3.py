from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from oculidoc.api.app import create_api
from oculidoc.api.mobile_page import mobile_control_html
from oculidoc.application.clinical_trends import _add_comparisons, _build_point
from oculidoc.application.gaze_report import (
    _task_detail_plot,
    _task_result_rows,
)
from oculidoc.application.session_history import SessionHistoryEntry
from oculidoc.config import Settings
from oculidoc.domain.experiment_session import ExperimentSessionStatus
from oculidoc.lan_commands import LanCommandStore, LanCommandType
from oculidoc.modules.registry import DEFAULT_MODULES
from oculidoc.process_launch import gaze_task_process_command
from oculidoc.tasks.gaze_games import (
    GazeGameConfig,
    GazeGameMode,
    GazeGameSetupDialog,
)
from oculidoc.tasks.visual_preference import (
    PreferencePair,
    VisualPreferenceConfig,
    VisualPreferenceSetupDialog,
)


def test_registry_has_exact_clinical_order_and_only_two_new_top_level_modules() -> None:
    identifiers = [module.module_id for module in DEFAULT_MODULES]

    assert identifiers == [
        "eye_observation",
        "visual_preference",
        "tracking_ball",
        "gaze_games",
        "instruction_fixation",
        "image_choice",
        "binary_horizontal",
        "binary_vertical",
        "multiple_choice",
        "screen_keyboard",
    ]
    assert not {
        "garden",
        "treasure_hunt",
        "gaze_contingency",
        "visual_hunt",
    } & set(identifiers)


def test_direct_gaze_game_launch_requires_explicit_mode() -> None:
    program, arguments = gaze_task_process_command(
        "gaze-games",
        config_revision=4,
        game_mode="treasure_hunt",
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )

    assert program == "OculiDoC.exe"
    assert arguments == [
        "--task",
        "gaze-games",
        "--direct",
        "--config-revision",
        "4",
        "--game-mode",
        "treasure_hunt",
    ]

    with pytest.raises(ValueError, match="requires game_mode"):
        gaze_task_process_command(
            "gaze-games",
            config_revision=4,
            executable=Path("OculiDoC.exe"),
            frozen=True,
        )

    assert gaze_task_process_command(
        "visual-preference",
        config_revision=2,
        executable=Path("OculiDoC.exe"),
        frozen=True,
    )[1] == [
        "--task",
        "visual-preference",
        "--direct",
        "--config-revision",
        "2",
    ]


def test_lan_and_api_reject_missing_gaze_game_mode(tmp_path: Path) -> None:
    command_store = LanCommandStore(tmp_path / "commands")
    command = command_store.submit(
        LanCommandType.START_TASK,
        payload={
            "module_id": "gaze_games",
            "config_revision": 0,
            "game_mode": "garden",
        },
    )
    assert command.game_mode == "garden"

    api = create_api(
        Settings(environment="test", data_dir=tmp_path, gaze_source="mock"),
        token="secret-pairing-token",
        command_store=command_store,
    )
    client = TestClient(api)
    parameters = {"token": "secret-pairing-token"}

    missing = client.post(
        "/api/v1/commands",
        params=parameters,
        json={
            "command_type": "start_task",
            "module_id": "gaze_games",
            "config_revision": 0,
        },
    )
    assert missing.status_code == 422
    assert "请选择游戏模式" in missing.text

    accepted = client.post(
        "/api/v1/commands",
        params=parameters,
        json={
            "command_type": "start_task",
            "module_id": "gaze_games",
            "config_revision": 0,
            "game_mode": "treasure_hunt",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["payload"]["game_mode"] == "treasure_hunt"

    unrelated = client.post(
        "/api/v1/commands",
        params=parameters,
        json={
            "command_type": "start_task",
            "module_id": "visual_preference",
            "config_revision": 0,
            "game_mode": "garden",
        },
    )
    assert unrelated.status_code == 422


def test_game_mode_page_can_return_before_accepting(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    dialog = GazeGameSetupDialog(
        config=GazeGameConfig(),
        image_library_path=tmp_path / "images",
    )
    qtbot.addWidget(dialog)
    assert dialog.pages.currentIndex() == 0

    garden = dialog.findChild(QPushButton, "gazeGameGardenButton")
    assert garden is not None
    garden.click()
    assert dialog.pages.currentIndex() == 1

    garden_page = dialog.pages.widget(1)
    assert garden_page is not None
    back = next(
        button
        for button in garden_page.findChildren(QPushButton)
        if button.text() == "返回模式选择"
    )
    back.click()
    assert dialog.pages.currentIndex() == 0

    dialog._accept_mode(GazeGameMode.GARDEN)
    assert dialog.selected_mode is GazeGameMode.GARDEN
    assert dialog.build_config().default_mode is GazeGameMode.GARDEN


def test_visual_preference_setup_preserves_selected_pairs(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    pairs = (
        PreferencePair("pair-a", "banana", "apple", "水果"),
        PreferencePair("pair-b", "car", "lion", "日常与动物"),
    )
    dialog = VisualPreferenceSetupDialog(
        config=VisualPreferenceConfig(
            pair_ids=("pair-a", "pair-b"),
            pairs=pairs,
        ),
        image_library_path=tmp_path / "images",
    )
    qtbot.addWidget(dialog)

    built = dialog.build_config()
    assert built.pair_ids == ("pair-a", "pair-b")
    assert built.present_each_side_once is True


def _garden_task_record(
    *,
    valid_sample_ratio: float = 0.8,
    dwell_time_ms: int = 800,
    contingent_ratio: float = 0.45,
) -> dict[str, object]:
    return {
        "task_kind": "gaze_games",
        "result": {
            "game_mode": "garden",
            "completion_status": "completed",
            "valid_sample_ratio": valid_sample_ratio,
            "randomization_seed": 23,
            "replay_source": "recorded_contingent_1",
            "aoi_exploration_coverage": 0.75,
            "contingent_activation_count": 3,
            "contingent_target_dwell_ratio": contingent_ratio,
            "replay_target_dwell_ratio": 0.25,
            "loss_and_reacquisition_count": 1,
            "configuration": {
                "object_count": 4,
                "dwell_time_ms": dwell_time_ms,
                "contingent_block_seconds": 30,
            },
            "blocks": [
                {
                    "block_type": "baseline",
                    "sample_count": 10,
                    "valid_sample_count": 8,
                    "valid_duration_ms": 7000.0,
                    "target_dwell_ms": 1200.0,
                    "activation_latencies_ms": [],
                },
                {
                    "block_type": "contingent_1",
                    "sample_count": 20,
                    "valid_sample_count": 16,
                    "valid_duration_ms": 25000.0,
                    "target_dwell_ms": 9000.0,
                    "activation_latencies_ms": [900.0, 3400.0],
                },
            ],
            "recording_failed": False,
        },
    }


def test_garden_report_has_dedicated_rows_and_figure(tmp_path: Path) -> None:
    record = _garden_task_record()
    rows = dict(_task_result_rows(record))

    assert rows["游戏模式"] == "点亮花园"
    assert rows["联动目标停留占比"] == "45.0%"
    assert "意识分类" in rows["解释边界"]

    output = tmp_path / "task_detail.png"
    assert _task_detail_plot([record], output) == "garden"
    assert output.is_file()


def test_hunt_and_preference_reports_keep_failures_and_side_bias(
    tmp_path: Path,
) -> None:
    hunt: dict[str, object] = {
        "task_kind": "gaze_games",
        "result": {
            "game_mode": "treasure_hunt",
            "valid_sample_ratio": 0.82,
            "successful_trial_count": 1,
            "target_present_trial_count": 2,
            "failed_or_timeout_trial_count": 1,
            "target_acquisition_ratio_by_condition": {
                "preview_search": 0.5,
                "popout": 1.0,
            },
            "median_target_acquisition_ms": 950.0,
            "distractor_dwell_ratio": 0.2,
            "catch_false_selection_ratio": 0.5,
            "trials": [
                {
                    "trial_number": 1,
                    "condition": "preview_search",
                    "target_present": True,
                    "target_acquired": True,
                    "target_acquisition_ms": 950.0,
                    "distractor_dwell_ms": 300.0,
                },
                {
                    "trial_number": 2,
                    "condition": "preview_search",
                    "target_present": True,
                    "target_acquired": False,
                    "target_acquisition_ms": None,
                    "distractor_dwell_ms": 1400.0,
                },
                {
                    "trial_number": 3,
                    "condition": "catch",
                    "target_present": False,
                    "target_acquired": False,
                    "target_acquisition_ms": None,
                    "distractor_dwell_ms": 500.0,
                },
            ],
        },
    }
    hunt_rows = dict(_task_result_rows(hunt))
    assert hunt_rows["未成功或超时"] == "1"
    hunt_plot = tmp_path / "hunt.png"
    assert _task_detail_plot([hunt], hunt_plot) == "treasure_hunt"
    assert hunt_plot.is_file()

    preference: dict[str, object] = {
        "task_kind": "visual_preference",
        "result": {
            "valid_sample_ratio": 0.76,
            "usable_trial_count": 4,
            "trial_count": 6,
            "any_image_entry_ratio": 1.0,
            "image_dwell_share_a": 0.62,
            "image_dwell_share_b": 0.38,
            "left_dwell_share": 0.71,
            "side_swap_consistency": None,
            "side_swap_pair_denominator": 2,
            "trials": [],
        },
    }
    preference_rows = dict(_task_result_rows(preference))
    assert preference_rows["图片 A 停留占比"] == "62.0%"
    assert preference_rows["固定左侧停留占比"] == "71.0%"
    preference_plot = tmp_path / "preference.png"
    assert _task_detail_plot([preference], preference_plot) == "visual_preference"
    assert preference_plot.is_file()


def _trend_entry(tmp_path: Path) -> SessionHistoryEntry:
    now = datetime(2026, 7, 23, tzinfo=UTC)
    return SessionHistoryEntry(
        session_id=uuid4(),
        patient_id=uuid4(),
        module_id="gaze_games",
        status=ExperimentSessionStatus.COMPLETED,
        created_at=now,
        started_at=now,
        ended_at=now,
        duration_seconds=90.0,
        artifact_count=3,
        sample_count=100,
        valid_sample_ratio=0.8,
        dwell_by_role_ms={},
        failure_reason=None,
        session_directory=tmp_path,
        task_results=(),
    )


def test_trends_split_game_modes_and_only_compare_matching_configuration(
    tmp_path: Path,
) -> None:
    entry = _trend_entry(tmp_path)
    first = _build_point(entry, _garden_task_record(contingent_ratio=0.40), task_index=1)
    second = _build_point(entry, _garden_task_record(contingent_ratio=0.55), task_index=1)
    changed = _build_point(
        entry,
        _garden_task_record(dwell_time_ms=1200, contingent_ratio=0.60),
        task_index=1,
    )
    points = [first, second, changed]
    _add_comparisons(points)

    assert first["metric_family"] == "gaze_garden"
    comparison = second["comparison"]
    assert isinstance(comparison, dict)
    delta = comparison["delta"]
    assert isinstance(delta, dict)
    assert delta["contingent_target_dwell_ratio"] == pytest.approx(0.15)
    assert changed["comparison"] is None

    low_quality = _build_point(
        entry,
        _garden_task_record(valid_sample_ratio=0.40),
        task_index=1,
    )
    assert low_quality["usable_for_trend"] is False


def test_mobile_page_contains_new_forms_and_explicit_game_mode() -> None:
    page = mobile_control_html("secret-pairing-token")

    assert "gaze_games" in page
    assert "visual_preference" in page
    assert "garden.dwell_time_ms" in page
    assert "treasure_hunt.catch_trial_count" in page
    assert "新增并选中刺激对" in page
    assert "payload.game_mode" in page
