from __future__ import annotations

from collections import Counter

import pytest

from oculidoc.tasks.visual_hunt import (
    VisualHuntCondition,
    VisualHuntConfig,
    VisualHuntStimulus,
    VisualHuntTrialObservation,
    summarize_visual_hunt_observations,
    visual_hunt_protocol,
)


def stimuli(count: int = 8) -> tuple[VisualHuntStimulus, ...]:
    return tuple(VisualHuntStimulus(f"image-{index}", f"label-{index}") for index in range(count))


def test_visual_hunt_protocol_replays_seed_and_keeps_catches_target_absent() -> None:
    config = VisualHuntConfig(
        preview_trial_count=6,
        popout_trial_count=4,
        catch_trial_count=2,
        distractor_count=3,
        randomization_seed=71,
    )

    first = visual_hunt_protocol(config, stimuli())
    second = visual_hunt_protocol(config, stimuli())

    assert first == second
    assert Counter(trial.condition for trial in first.trials) == {
        VisualHuntCondition.PREVIEW_SEARCH: 6,
        VisualHuntCondition.POPOUT: 4,
        VisualHuntCondition.CATCH: 2,
    }

    positions = Counter(trial.target_position for trial in first.trials if trial.target_present)
    assert max(positions.values()) - min(positions.values()) <= 1

    for trial in first.trials:
        assert len(set(trial.array_labels)) == len(trial.array_labels)
        if trial.condition is VisualHuntCondition.CATCH:
            assert trial.target_position is None
            assert trial.target_label not in trial.array_labels
            assert len(trial.array_labels) == config.distractor_count
        else:
            assert trial.target_position is not None
            assert trial.array_stimulus_ids[trial.target_position] == trial.target_stimulus_id
            assert len(trial.array_labels) == config.distractor_count + 1


def test_visual_hunt_rejects_an_image_pool_with_too_few_labels() -> None:
    config = VisualHuntConfig(
        preview_trial_count=1,
        popout_trial_count=0,
        catch_trial_count=0,
        distractor_count=3,
        randomization_seed=1,
    )
    repeated_labels = (
        VisualHuntStimulus("one", "same"),
        VisualHuntStimulus("two", "same"),
        VisualHuntStimulus("three", "other"),
        VisualHuntStimulus("four", "third"),
    )

    with pytest.raises(ValueError, match="different image labels"):
        visual_hunt_protocol(config, repeated_labels)


def test_visual_hunt_summary_retains_failures_and_catch_false_selections() -> None:
    result = summarize_visual_hunt_observations(
        (
            VisualHuntTrialObservation(
                VisualHuntCondition.PREVIEW_SEARCH,
                target_present=True,
                sample_count=100,
                valid_sample_count=80,
                target_acquired=True,
                first_target_entry_ms=300,
                target_acquisition_ms=900,
                longest_target_dwell_ms=1000,
                distractor_dwell_ms=200,
                array_valid_duration_ms=4000,
                wrong_dwell_count=1,
                aoi_visits_before_target=2,
                normalized_scanpath_length=0.6,
                target_field="left",
            ),
            VisualHuntTrialObservation(
                VisualHuntCondition.PREVIEW_SEARCH,
                target_present=True,
                sample_count=100,
                valid_sample_count=60,
                target_acquired=False,
                first_target_entry_ms=1200,
                longest_target_dwell_ms=400,
                distractor_dwell_ms=1000,
                array_valid_duration_ms=3000,
                wrong_dwell_count=2,
                normalized_scanpath_length=1.2,
                target_field="right",
            ),
            VisualHuntTrialObservation(
                VisualHuntCondition.POPOUT,
                target_present=True,
                sample_count=100,
                valid_sample_count=100,
                target_acquired=True,
                first_target_entry_ms=200,
                target_acquisition_ms=700,
                longest_target_dwell_ms=900,
                distractor_dwell_ms=100,
                array_valid_duration_ms=3000,
                aoi_visits_before_target=0,
                normalized_scanpath_length=0.3,
                target_field="right",
            ),
            VisualHuntTrialObservation(
                VisualHuntCondition.CATCH,
                target_present=False,
                sample_count=100,
                valid_sample_count=60,
                distractor_dwell_ms=1200,
                array_valid_duration_ms=3000,
                wrong_dwell_count=1,
                normalized_scanpath_length=0.9,
            ),
        )
    )

    assert result["trial_count"] == 4
    assert result["target_present_trial_count"] == 3
    assert result["successful_trial_count"] == 2
    assert result["failed_or_timeout_trial_count"] == 1
    assert result["valid_sample_ratio"] == 0.75
    assert result["target_acquisition_ratio"] == pytest.approx(2 / 3)
    assert result["target_acquisition_ratio_by_condition"] == {
        "preview_search": 0.5,
        "popout": 1.0,
    }
    assert result["median_target_acquisition_ms"] == 800
    assert result["target_acquisition_latency_denominator"] == 2
    assert result["wrong_dwell_count"] == 4
    assert result["catch_false_selection_ratio"] == 1.0
    assert result["field_hit_ratio"] == {"left": 1.0, "right": 0.5}
    assert result["interpretation"] == "descriptive_visual_search_observation_only"


def test_visual_hunt_config_limits_catch_trials() -> None:
    with pytest.raises(ValueError, match="40 percent"):
        VisualHuntConfig(
            preview_trial_count=1,
            popout_trial_count=0,
            catch_trial_count=1,
        )
