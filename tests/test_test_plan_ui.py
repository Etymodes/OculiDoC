from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from oculidoc.ui.test_plan import (
    BinaryAxisOrder,
)
from oculidoc.ui.test_plan import (
    TestPlan as CurrentTestPlan,
)
from oculidoc.ui.test_plan import (
    TestPlanConflict as PlanConflict,
)
from oculidoc.ui.test_plan import (
    TestPlanStepStatus as StepStatus,
)
from oculidoc.ui.test_plan import (
    TestPlanStore as CurrentTestPlanStore,
)


def step(plan: CurrentTestPlan, step_id: str):
    return next(item for item in plan.steps if item.step_id == step_id)


def test_default_plan_excludes_optional_eye_observation_from_progress() -> None:
    plan = CurrentTestPlan.default(
        "patient-1",
        config_revisions={
            "visual_preference": 2,
            "gaze_games": 4,
            "tracking_ball": 3,
        },
    )

    assert step(plan, "eye_observation").selected is False
    assert step(plan, "visual_preference").config_revision == 2
    assert step(plan, "gaze_games:garden").config_revision == 4
    assert step(plan, "gaze_games:treasure_hunt").config_revision == 4
    assert step(plan, "gaze_games:starlight_route").config_revision == 4
    assert plan.progress == (0, 11)
    assert plan.next_pending_step == step(plan, "visual_preference")


def test_plan_uses_real_session_terminals_and_keeps_failure_distinct() -> None:
    plan = CurrentTestPlan.default("patient-1")
    preference = step(plan, "visual_preference").start("session-1")
    plan = plan.replace_step(preference)
    assert plan.next_pending_step == step(plan, "tracking_ball")

    failed = preference.finish(StepStatus.FAILED)
    plan = plan.replace_step(failed)

    assert plan.progress == (1, 11)
    assert step(plan, "visual_preference").status is StepStatus.FAILED
    assert step(plan, "visual_preference").session_id == "session-1"
    assert plan.next_pending_step == step(plan, "tracking_ball")

    retried = failed.prepare_retry()
    plan = plan.replace_step(retried)
    assert retried.status is StepStatus.PENDING
    assert retried.session_id is None
    assert retried.retry_count == 1


def test_skipping_requires_reason_and_creates_no_session() -> None:
    plan = CurrentTestPlan.default("patient-1")
    tracking = step(plan, "tracking_ball")

    with pytest.raises(ValueError, match="reason"):
        tracking.skip("")

    skipped = tracking.skip("patient_fatigue")
    assert skipped.status is StepStatus.SKIPPED
    assert skipped.session_id is None
    assert skipped.skip_reason == "patient_fatigue"
    assert skipped.undo_skip().status is StepStatus.PENDING


def test_axis_exception_only_reorders_pending_binary_steps() -> None:
    plan = CurrentTestPlan.default("patient-1")
    vertical_first = plan.with_axis_order(BinaryAxisOrder.VERTICAL_FIRST)

    assert [item.step_id for item in vertical_first.steps][8:10] == [
        "binary_vertical",
        "binary_horizontal",
    ]
    assert vertical_first.rest_after_step_ids[-1] == "binary_horizontal"

    running_horizontal = step(plan, "binary_horizontal").start("session-binary")
    started_plan = plan.replace_step(running_horizontal)
    with pytest.raises(ValueError, match="cannot change"):
        started_plan.with_axis_order(BinaryAxisOrder.VERTICAL_FIRST)


def test_task_blocks_copy_insert_delete_round_trip_and_lock_after_start(
    tmp_path: Path,
) -> None:
    plan = CurrentTestPlan.default("patient-1")
    copied = plan.copy_block("tracking_ball")
    tracking_blocks = [item for item in copied.steps if item.step_id == "tracking_ball"]
    assert len(tracking_blocks) == 2
    assert tracking_blocks[0].block_id != tracking_blocks[1].block_id
    inserted = copied.insert_block_after(
        tracking_blocks[1].block_id,
        "screen_keyboard",
        config_revision=7,
    )
    assert inserted.steps[4].step_id == "screen_keyboard"
    assert inserted.steps[4].config_revision == 7
    edited = inserted.delete_block(tracking_blocks[0].block_id)
    assert [item.step_id for item in edited.steps].count("tracking_ball") == 1

    store = CurrentTestPlanStore(tmp_path / "current_test_plans.json")
    saved = store.save(edited, expected_revision=0)
    assert store.load("patient-1") == saved

    running = edited.replace_step(step(edited, "tracking_ball").start("session-1"))
    with pytest.raises(ValueError, match="不能"):
        running.copy_block(step(running, "visual_preference").block_id)
    with pytest.raises(ValueError, match="不能"):
        running.insert_block_after(
            step(running, "visual_preference").block_id,
            "screen_keyboard",
            config_revision=0,
        )


def test_terminal_blocks_can_be_copied_or_followed_but_not_deleted() -> None:
    plan = CurrentTestPlan.default("patient-1")
    completed = step(plan, "tracking_ball").start("session-1").finish(StepStatus.COMPLETED)
    plan = plan.replace_step(completed)

    copied = plan.copy_block(completed.block_id)
    duplicate = copied.steps[copied.steps.index(completed) + 1]
    assert duplicate.step_id == completed.step_id
    assert duplicate.status is StepStatus.PENDING
    assert duplicate.session_id is None
    assert duplicate.selected

    inserted = plan.insert_block_after(
        completed.block_id,
        "screen_keyboard",
        config_revision=7,
    )
    assert inserted.steps[inserted.steps.index(completed) + 1].step_id == "screen_keyboard"

    with pytest.raises(ValueError, match="已有结果"):
        plan.delete_block(completed.block_id)

    pending = step(plan, "visual_preference")
    deleted = plan.delete_block(pending.block_id)
    assert pending.block_id not in {item.block_id for item in deleted.steps}


def test_old_plan_without_block_ids_loads_with_stable_legacy_ids() -> None:
    original = CurrentTestPlan.default("patient-legacy")
    payload = original.to_dict()
    for item in cast(list[dict[str, object]], payload["steps"]):
        item.pop("block_id")

    loaded = CurrentTestPlan.from_dict(payload)
    assert [item.block_id for item in loaded.steps] == [item.step_id for item in loaded.steps]
    assert loaded.rest_after_step_ids == original.rest_after_step_ids


def test_test_plan_store_round_trip_conflict_and_close(tmp_path: Path) -> None:
    path = tmp_path / "current_test_plans.json"
    store = CurrentTestPlanStore(path)
    first = store.save(CurrentTestPlan.default("patient-1"), expected_revision=0)
    second_patient = store.save(
        CurrentTestPlan.default("patient-2"),
        expected_revision=0,
    )

    assert first.revision == 1
    assert second_patient.revision == 1
    assert store.load("patient-1") == first
    assert store.load("patient-2") == second_patient
    assert not list(tmp_path.glob(".current_test_plans.json.*.tmp"))

    updated = store.save(
        first.replace_step(step(first, "visual_preference").skip("care_interruption")),
        expected_revision=first.revision,
    )
    assert updated.revision == 2
    assert store.load("patient-2") == second_patient

    with pytest.raises(PlanConflict) as raised:
        store.save(first, expected_revision=first.revision)
    assert raised.value.current == updated

    store.close("patient-1", expected_revision=updated.revision)
    assert store.load("patient-1") is None
    assert store.load("patient-2") == second_patient
