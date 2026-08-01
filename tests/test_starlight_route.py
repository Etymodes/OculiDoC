from datetime import UTC, datetime
from time import monotonic_ns

from pytestqt.qtbot import QtBot

from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample
from oculidoc.tasks.starlight_route import (
    ProbeEdge,
    StarlightAdaptiveModel,
    StarlightRouteConfig,
    StarlightRouteTask,
)


def record_round(
    model: StarlightAdaptiveModel,
    *,
    acquired: bool,
    response_ms: float | None,
    sample_count: int = 20,
    valid_sample_count: int = 18,
    sample_coverage_sufficient: bool = True,
):
    return model.record(
        model.next_target(),
        acquired=acquired,
        sample_count=sample_count,
        valid_sample_count=valid_sample_count,
        sample_coverage_sufficient=sample_coverage_sufficient,
        response_ms=response_ms,
    )


def gaze_sample(
    sequence: int,
    *,
    timestamp_ns: int,
    x: float,
    y: float,
) -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=sequence,
            monotonic_timestamp_ns=timestamp_ns,
            utc_timestamp=datetime.now(UTC),
        ),
        gaze_x_normalized=x,
        gaze_y_normalized=y,
        left_eye_valid=True,
        right_eye_valid=False,
    )


def test_three_hits_raise_level_and_two_valid_misses_lower_it() -> None:
    model = StarlightAdaptiveModel(
        StarlightRouteConfig(initial_level=2, edge_probe_interval=10, randomization_seed=7)
    )

    for _ in range(3):
        record_round(model, acquired=True, response_ms=500)

    assert model.level == 3
    assert model.score == 60

    for _ in range(2):
        record_round(model, acquired=False, response_ms=None)

    assert model.level == 2


def test_invalid_tracking_never_penalizes_patient_performance() -> None:
    model = StarlightAdaptiveModel(StarlightRouteConfig(initial_level=4, randomization_seed=11))
    outcome = record_round(
        model,
        acquired=False,
        response_ms=None,
        valid_sample_count=4,
    )

    assert outcome.status == "invalid"
    assert model.level == 4
    assert model.valid_miss_streak == 0


def test_edge_probe_success_expands_and_miss_tightens_reachable_region() -> None:
    model = StarlightAdaptiveModel(
        StarlightRouteConfig(edge_probe_interval=2, randomization_seed=3)
    )
    for _ in range(2):
        record_round(model, acquired=True, response_ms=400, valid_sample_count=20)

    left_probe = model.next_target()
    assert left_probe.probe_edge is ProbeEdge.LEFT
    old_left = model.region.left
    model.record(
        left_probe,
        acquired=True,
        sample_count=20,
        valid_sample_count=20,
        sample_coverage_sufficient=True,
        response_ms=600,
    )
    assert model.region.left < old_left

    regular = model.next_target()
    model.record(
        regular,
        acquired=True,
        sample_count=20,
        valid_sample_count=20,
        sample_coverage_sufficient=True,
        response_ms=500,
    )
    right_probe = model.next_target()
    assert right_probe.probe_edge is ProbeEdge.RIGHT
    old_right = model.region.right
    model.record(
        right_probe,
        acquired=False,
        sample_count=20,
        valid_sample_count=20,
        sample_coverage_sufficient=True,
        response_ms=None,
    )
    assert model.region.right < old_right


def test_seed_reproduces_route_and_higher_level_shrinks_star() -> None:
    config = StarlightRouteConfig(initial_level=1, randomization_seed=99)
    first = StarlightAdaptiveModel(config)
    second = StarlightAdaptiveModel(config)
    assert first.next_target() == second.next_target()

    radius = first.star_radius
    for _ in range(3):
        record_round(first, acquired=True, response_ms=300, valid_sample_count=20)
    assert first.star_radius < radius


def test_insufficient_sample_coverage_is_invalid_even_with_a_perfect_ratio() -> None:
    model = StarlightAdaptiveModel(StarlightRouteConfig(initial_level=4, randomization_seed=13))

    outcome = record_round(
        model,
        acquired=False,
        response_ms=None,
        sample_count=1,
        valid_sample_count=1,
        sample_coverage_sufficient=False,
    )

    assert outcome.status == "invalid"
    assert outcome.valid_sample_ratio == 1.0
    assert model.level == 4
    assert model.valid_miss_streak == 0


def test_task_marks_one_sample_then_silence_invalid(qtbot: QtBot) -> None:
    task = StarlightRouteTask(
        StarlightRouteConfig(
            round_count=6,
            initial_level=4,
            trial_duration_seconds=3,
            randomization_seed=17,
        )
    )
    qtbot.addWidget(task)
    started_ns = monotonic_ns()
    task.start(started_ns)
    target = task._target
    task.consume_sample(
        gaze_sample(
            1,
            timestamp_ns=started_ns + 100_000_000,
            x=target.x,
            y=target.y,
        )
    )
    task.advance_time(started_ns + 3_000_000_000)

    outcome = task.model.outcomes[0]
    assert outcome.status == "invalid"
    assert outcome.sample_count == 1
    assert outcome.valid_sample_count == 1
    assert outcome.valid_sample_ratio == 1.0
    assert outcome.sample_coverage_sufficient is False
    assert task.model.level == 4
    assert task._sample_count == 0
    result = task.recording_result("operator_exit")
    assert result["rounds"][0]["sample_count"] == 1
    assert result["rounds"][0]["sample_coverage_sufficient"] is False
    task.stop()


def test_timeout_sample_is_not_reused_for_the_next_round(qtbot: QtBot) -> None:
    task = StarlightRouteTask(
        StarlightRouteConfig(
            round_count=6,
            trial_duration_seconds=3,
            randomization_seed=18,
        )
    )
    qtbot.addWidget(task)
    started_ns = monotonic_ns()
    task.start(started_ns)
    target = task._target

    task.consume_sample(
        gaze_sample(
            1,
            timestamp_ns=started_ns + 3_000_000_000,
            x=target.x,
            y=target.y,
        )
    )

    assert task.model.outcomes[0].status == "invalid"
    assert task.model.outcomes[0].sample_count == 0
    assert task.model.completed_rounds == 1
    assert task._sample_count == 0
    assert task._valid_sample_count == 0
    task.stop()


def test_task_counts_well_sampled_off_target_rounds_as_valid_misses(qtbot: QtBot) -> None:
    task = StarlightRouteTask(
        StarlightRouteConfig(
            round_count=6,
            initial_level=4,
            trial_duration_seconds=3,
            randomization_seed=19,
        )
    )
    qtbot.addWidget(task)
    started_ns = monotonic_ns()
    task.start(started_ns)
    sequence = 0

    for round_index in range(2):
        round_started_ns = started_ns + round_index * 3_000_000_000
        for sample_index in range(1, 30):
            sequence += 1
            task.consume_sample(
                gaze_sample(
                    sequence,
                    timestamp_ns=round_started_ns + sample_index * 100_000_000,
                    x=0.0,
                    y=0.0,
                )
            )
        task.advance_time(round_started_ns + 3_000_000_000)

    assert [outcome.status for outcome in task.model.outcomes] == ["miss", "miss"]
    assert all(outcome.sample_coverage_sufficient for outcome in task.model.outcomes)
    assert task.model.level == 3
    task.stop()
