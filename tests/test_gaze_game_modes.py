from oculidoc.tasks.gaze_contingency import GazeContingencyConfig
from oculidoc.tasks.gaze_games import GazeGameConfig, GazeGameMode
from oculidoc.tasks.visual_hunt import VisualHuntConfig


def test_gaze_game_config_keeps_two_modes_under_one_container() -> None:
    config = GazeGameConfig(
        default_mode="treasure_hunt",  # type: ignore[arg-type]
        garden={"dwell_time_ms": 900, "randomization_seed": 5},  # type: ignore[arg-type]
        treasure_hunt={  # type: ignore[arg-type]
            "preview_trial_count": 3,
            "popout_trial_count": 2,
            "catch_trial_count": 1,
            "randomization_seed": 6,
        },
    )

    assert config.default_mode is GazeGameMode.TREASURE_HUNT
    assert isinstance(config.garden, GazeContingencyConfig)
    assert config.garden.dwell_time_ms == 900
    assert isinstance(config.treasure_hunt, VisualHuntConfig)
    assert config.treasure_hunt.trial_count == 6
