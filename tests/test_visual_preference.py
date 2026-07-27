from __future__ import annotations

from collections import Counter

import pytest

from oculidoc.tasks.visual_preference import (
    PreferencePair,
    VisualPreferenceConfig,
    VisualPreferenceTrialObservation,
    summarize_visual_preference_observations,
    validate_visual_preference_for_start,
    visual_preference_protocol,
)


def preference_pairs() -> tuple[PreferencePair, ...]:
    return (
        PreferencePair("pair-1", "cat", "car", "动物—交通"),
        PreferencePair("pair-2", "flower", "cup", "植物—物品"),
        PreferencePair("pair-3", "bird", "train", "动物—交通 02"),
    )


def preference_config(**updates: object) -> VisualPreferenceConfig:
    values: dict[str, object] = {
        "pair_ids": ("pair-1", "pair-2", "pair-3"),
        "pairs": preference_pairs(),
        "randomization_seed": 23,
    }
    values.update(updates)
    return VisualPreferenceConfig(**values)  # type: ignore[arg-type]


def test_visual_preference_protocol_swaps_every_pair_without_adjacency() -> None:
    config = preference_config()

    first = visual_preference_protocol(config)
    second = visual_preference_protocol(config)

    assert first == second
    assert len(first.trials) == 6
    assert Counter(trial.pair_id for trial in first.trials) == {
        "pair-1": 2,
        "pair-2": 2,
        "pair-3": 2,
    }
    assert all(
        current.pair_id != following.pair_id
        for current, following in zip(first.trials, first.trials[1:], strict=False)
    )

    for pair_id in config.pair_ids:
        trials = [trial for trial in first.trials if trial.pair_id == pair_id]
        assert {trial.a_on_left for trial in trials} == {True, False}
        assert {trial.side_presentation_index for trial in trials} == {1, 2}


def test_visual_preference_summary_separates_image_and_side_dwell() -> None:
    config = preference_config()
    protocol = visual_preference_protocol(config)
    observations = tuple(
        VisualPreferenceTrialObservation(
            pair_id=trial.pair_id,
            a_on_left=trial.a_on_left,
            sample_count=100,
            valid_sample_count=80,
            dwell_a_ms=800,
            dwell_b_ms=200,
            background_dwell_ms=100,
            first_entry="a",
            first_entry_ms=250,
            switch_count=2,
        )
        for trial in protocol.trials
    )

    result = summarize_visual_preference_observations(config, observations)

    assert result["valid_sample_ratio"] == 0.8
    assert result["usable_trial_ratio"] == 1.0
    assert result["any_image_entry_ratio"] == 1.0
    assert result["image_dwell_share_a"] == 0.8
    assert result["image_dwell_share_b"] == 0.2
    assert result["left_dwell_share"] == 0.5
    assert result["first_entry_share_a"] == 1.0
    assert result["first_entry_share_left"] == 0.5
    assert result["first_entry_share_right"] == 0.5
    assert result["side_swap_consistency"] == 1.0
    assert result["side_swap_pair_denominator"] == 3
    assert result["median_switch_count"] == 2
    assert result["interpretation"] == "descriptive_visual_preference_observation_only"


def test_low_quality_preference_trial_is_retained_but_not_used_in_summary() -> None:
    config = preference_config()
    observations = (
        VisualPreferenceTrialObservation(
            pair_id="pair-1",
            a_on_left=True,
            sample_count=100,
            valid_sample_count=20,
            dwell_a_ms=500,
            dwell_b_ms=0,
            background_dwell_ms=100,
        ),
        VisualPreferenceTrialObservation(
            pair_id="pair-1",
            a_on_left=False,
            sample_count=100,
            valid_sample_count=80,
            dwell_a_ms=300,
            dwell_b_ms=300,
            background_dwell_ms=100,
        ),
    )

    result = summarize_visual_preference_observations(config, observations)

    assert result["trial_count"] == 2
    assert result["usable_trial_count"] == 1
    assert result["usable_trial_ratio"] == 0.5
    assert result["side_swap_consistency"] is None
    assert result["side_swap_pair_denominator"] == 0


def test_preference_start_validation_lists_missing_images() -> None:
    config = preference_config()

    with pytest.raises(ValueError, match="train"):
        validate_visual_preference_for_start(
            config,
            {"cat", "car", "flower", "cup", "bird"},
        )

    validate_visual_preference_for_start(
        config,
        {"cat", "car", "flower", "cup", "bird", "train"},
    )


def test_empty_default_config_cannot_start() -> None:
    config = VisualPreferenceConfig()

    with pytest.raises(ValueError, match="Select"):
        validate_visual_preference_for_start(config, ())
    with pytest.raises(ValueError, match="Select"):
        visual_preference_protocol(config)
