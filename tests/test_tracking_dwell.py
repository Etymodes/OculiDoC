"""Tests for moving-target dwell state."""

from oculidoc.tasks.tracking_dwell import (
    DwellPhase,
    TrackingDwellController,
)


def test_dwell_reaches_maintained_state() -> None:
    controller = TrackingDwellController(dwell_time_ms=900)

    controller.observe(True, 0)
    controller.observe(
        True,
        500_000_000,
    )
    snapshot = controller.observe(
        True,
        1_000_000_000,
    )

    assert snapshot.phase is (DwellPhase.MAINTAINED)
    assert snapshot.progress == 1.0
    assert snapshot.success_count == 1


def test_dwell_survives_brief_dropout() -> None:
    controller = TrackingDwellController(
        dwell_time_ms=900,
        dropout_grace_ms=180,
    )

    controller.observe(True, 0)
    controller.observe(
        True,
        500_000_000,
    )
    snapshot = controller.observe(
        False,
        600_000_000,
    )

    assert snapshot.phase is (DwellPhase.ACQUIRING)
    assert snapshot.dwell_ms > 0


def test_dwell_resets_after_dropout_grace() -> None:
    controller = TrackingDwellController(
        dwell_time_ms=900,
        dropout_grace_ms=180,
    )

    controller.observe(True, 0)
    controller.observe(
        True,
        500_000_000,
    )
    controller.observe(
        False,
        600_000_000,
    )
    snapshot = controller.observe(
        False,
        900_000_000,
    )

    assert snapshot.phase is DwellPhase.OUTSIDE
    assert snapshot.dwell_ms == 0
