from oculidoc.tasks.starlight_route import (
    ProbeEdge,
    StarlightAdaptiveModel,
    StarlightRouteConfig,
)


def test_three_hits_raise_level_and_two_valid_misses_lower_it() -> None:
    model = StarlightAdaptiveModel(
        StarlightRouteConfig(initial_level=2, edge_probe_interval=10, randomization_seed=7)
    )

    for _ in range(3):
        model.record(model.next_target(), acquired=True, valid_sample_ratio=0.9, response_ms=500)

    assert model.level == 3
    assert model.score == 60

    for _ in range(2):
        model.record(model.next_target(), acquired=False, valid_sample_ratio=0.9, response_ms=None)

    assert model.level == 2


def test_invalid_tracking_never_penalizes_patient_performance() -> None:
    model = StarlightAdaptiveModel(StarlightRouteConfig(initial_level=4, randomization_seed=11))
    outcome = model.record(
        model.next_target(), acquired=False, valid_sample_ratio=0.2, response_ms=None
    )

    assert outcome.status == "invalid"
    assert model.level == 4
    assert model.valid_miss_streak == 0


def test_edge_probe_success_expands_and_miss_tightens_reachable_region() -> None:
    model = StarlightAdaptiveModel(
        StarlightRouteConfig(edge_probe_interval=2, randomization_seed=3)
    )
    for _ in range(2):
        model.record(model.next_target(), acquired=True, valid_sample_ratio=1.0, response_ms=400)

    left_probe = model.next_target()
    assert left_probe.probe_edge is ProbeEdge.LEFT
    old_left = model.region.left
    model.record(left_probe, acquired=True, valid_sample_ratio=1.0, response_ms=600)
    assert model.region.left < old_left

    regular = model.next_target()
    model.record(regular, acquired=True, valid_sample_ratio=1.0, response_ms=500)
    right_probe = model.next_target()
    assert right_probe.probe_edge is ProbeEdge.RIGHT
    old_right = model.region.right
    model.record(right_probe, acquired=False, valid_sample_ratio=1.0, response_ms=None)
    assert model.region.right < old_right


def test_seed_reproduces_route_and_higher_level_shrinks_star() -> None:
    config = StarlightRouteConfig(initial_level=1, randomization_seed=99)
    first = StarlightAdaptiveModel(config)
    second = StarlightAdaptiveModel(config)
    assert first.next_target() == second.next_target()

    radius = first.star_radius
    for _ in range(3):
        first.record(first.next_target(), acquired=True, valid_sample_ratio=1.0, response_ms=300)
    assert first.star_radius < radius
