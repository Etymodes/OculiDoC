from __future__ import annotations

import pytest

from oculidoc.tasks.gaze_contingency import (
    GardenBlockObservation,
    GardenBlockType,
    GardenRewardEvent,
    GazeContingencyConfig,
    garden_protocol,
    garden_replay_schedule,
    summarize_garden_observations,
)


def test_garden_protocol_is_reproducible_and_has_fixed_block_order() -> None:
    config = GazeContingencyConfig(
        object_count=5,
        baseline_seconds=9,
        contingent_block_seconds=20,
        replay_block_seconds=25,
        randomization_seed=17,
    )

    first = garden_protocol(config)
    second = garden_protocol(config)

    assert first == second
    assert first.randomization_seed == 17
    assert len(first.objects) == 5
    assert len({(item.x_normalized, item.y_normalized) for item in first.objects}) == 5
    assert [block.block_type for block in first.blocks] == [
        GardenBlockType.BASELINE,
        GardenBlockType.CONTINGENT_1,
        GardenBlockType.REPLAY,
        GardenBlockType.CONTINGENT_2,
    ]
    assert [block.duration_ms for block in first.blocks] == [
        9000,
        20_000,
        25_000,
        20_000,
    ]
    assert [block.gaze_contingent for block in first.blocks] == [
        False,
        True,
        False,
        True,
    ]


def test_garden_replay_uses_recorded_events_or_marked_seeded_fallback() -> None:
    config = GazeContingencyConfig(
        contingent_block_seconds=20,
        replay_block_seconds=10,
        randomization_seed=9,
    )
    protocol = garden_protocol(config)
    recorded = garden_replay_schedule(
        config,
        protocol,
        (
            GardenRewardEvent(protocol.objects[0].object_id, 2000),
            GardenRewardEvent(protocol.objects[1].object_id, 10_000),
        ),
    )
    fallback = garden_replay_schedule(
        config,
        protocol,
        (GardenRewardEvent(protocol.objects[0].object_id, 2000),),
    )

    assert recorded.replay_source == "recorded_contingent_1"
    assert [event.offset_ms for event in recorded.events] == [1000, 5000]
    assert fallback.replay_source == "seeded_fallback"
    assert len(fallback.events) == 3
    assert fallback == garden_replay_schedule(
        config,
        protocol,
        (GardenRewardEvent(protocol.objects[0].object_id, 2000),),
    )


def test_garden_summary_keeps_contingent_and_replay_blocks_separate() -> None:
    config = GazeContingencyConfig(object_count=4)
    result = summarize_garden_observations(
        config,
        (
            GardenBlockObservation(
                GardenBlockType.BASELINE,
                sample_count=100,
                valid_sample_count=80,
                valid_duration_ms=8000,
                target_dwell_ms=1000,
                entered_object_ids=("flower-01",),
            ),
            GardenBlockObservation(
                GardenBlockType.CONTINGENT_1,
                sample_count=100,
                valid_sample_count=90,
                valid_duration_ms=20_000,
                target_dwell_ms=10_000,
                activation_latencies_ms=(1200, 1800),
                entered_object_ids=("flower-01", "flower-02"),
                loss_and_reacquisition_count=1,
            ),
            GardenBlockObservation(
                GardenBlockType.REPLAY,
                sample_count=100,
                valid_sample_count=70,
                valid_duration_ms=18_000,
                target_dwell_ms=4500,
                replay_reward_count=2,
                replay_rewards_on_target=1,
            ),
            GardenBlockObservation(
                GardenBlockType.CONTINGENT_2,
                sample_count=100,
                valid_sample_count=100,
                valid_duration_ms=20_000,
                target_dwell_ms=12_000,
                activation_latencies_ms=(600, 1000),
                entered_object_ids=("flower-03",),
                loss_and_reacquisition_count=2,
            ),
        ),
    )

    assert result["valid_sample_ratio"] == 0.85
    assert result["aoi_exploration_coverage"] == 0.75
    assert result["contingent_activation_count"] == 4
    assert result["median_activation_latency_ms_c1"] == 1500
    assert result["median_activation_latency_ms_c2"] == 800
    assert result["latency_change_ms"] == -700
    assert result["contingent_target_dwell_ratio"] == 0.55
    assert result["replay_target_dwell_ratio"] == 0.25
    assert result["replay_reward_on_target_ratio"] == 0.5
    assert result["loss_and_reacquisition_count"] == 3
    assert result["interpretation"] == "descriptive_gaze_contingency_observation_only"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("object_count", 1),
        ("dwell_time_ms", 200),
        ("reward_animation_ms", 400),
        ("randomization_seed", -1),
    ),
)
def test_garden_config_rejects_values_outside_clinical_bounds(
    field: str,
    value: int,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        GazeContingencyConfig(**{field: value})  # type: ignore[arg-type]
