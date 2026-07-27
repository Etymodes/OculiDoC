"""Non-bedside engineering guards added after P4.3 acceptance."""

from pathlib import Path
from time import monotonic_ns

from pytestqt.qtbot import QtBot

from oculidoc.application.gaze_report import _html_document
from oculidoc.image_library import ImageLibraryStore
from oculidoc.tasks.gaze_contingency import (
    GazeContingencyConfig,
    GazeContingencyTask,
)
from oculidoc.tasks.visual_hunt import (
    VisualHuntConfig,
    VisualHuntPhase,
    VisualHuntTask,
)
from oculidoc.tasks.visual_preference import (
    PreferencePair,
    VisualPreferenceConfig,
    VisualPreferenceTask,
)


def test_sound_off_suppresses_all_new_task_speech(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    store = ImageLibraryStore(tmp_path / "images")
    spoken: list[str] = []

    garden = GazeContingencyTask(
        GazeContingencyConfig(
            sound_enabled=False,
            randomization_seed=41,
        )
    )
    qtbot.addWidget(garden)
    garden.speech_requested.connect(spoken.append)
    garden.start(1_000_000_000)
    garden.expire_current_block()
    garden.expire_current_block()
    garden.expire_current_block()
    for index, flower in enumerate(garden.protocol.objects):
        garden._trigger_reward(
            flower.object_id,
            2_000_000_000 + index * 100_000_000,
        )
    garden.stop()

    hunt = VisualHuntTask(
        VisualHuntConfig(
            preview_trial_count=1,
            popout_trial_count=0,
            catch_trial_count=0,
            sound_enabled=False,
            randomization_seed=42,
        ),
        store,
    )
    qtbot.addWidget(hunt)
    hunt.speech_requested.connect(spoken.append)
    hunt.start(monotonic_ns())
    while hunt.phase is not VisualHuntPhase.ARRAY:
        hunt.expire_current_phase()
    assert hunt.phase_deadline_ns is not None
    hunt._acquire_target(hunt.phase_deadline_ns - 1_000_000)
    hunt.stop()

    preference = VisualPreferenceTask(
        VisualPreferenceConfig(
            pair_ids=("fruit", "familiar"),
            pairs=(
                PreferencePair("fruit", "banana", "apple", "水果"),
                PreferencePair("familiar", "lion", "car", "熟悉事物"),
            ),
            sound_intro_enabled=False,
            randomization_seed=43,
        ),
        store,
    )
    qtbot.addWidget(preference)
    preference.speech_requested.connect(spoken.append)
    preference.start(monotonic_ns())
    preference.stop()

    assert spoken == []


def test_new_task_animation_timers_keep_33_ms_interval(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    store = ImageLibraryStore(tmp_path / "images")
    tasks = (
        GazeContingencyTask(GazeContingencyConfig(randomization_seed=51)),
        VisualHuntTask(
            VisualHuntConfig(
                preview_trial_count=1,
                popout_trial_count=0,
                catch_trial_count=0,
                randomization_seed=52,
            ),
            store,
        ),
        VisualPreferenceTask(
            VisualPreferenceConfig(
                pair_ids=("fruit", "familiar"),
                pairs=(
                    PreferencePair("fruit", "banana", "apple", "水果"),
                    PreferencePair("familiar", "lion", "car", "熟悉事物"),
                ),
                randomization_seed=53,
            ),
            store,
        ),
    )

    for task in tasks:
        qtbot.addWidget(task)
        assert task._timer.interval() == 33


def test_report_keeps_low_quality_warning_and_non_diagnostic_notice() -> None:
    document = _html_document(
        patient_label="Beta00（内置测试患者）",
        module_id="visual_preference",
        record_time="2026-07-24T12:00:00+00:00",
        generated_at="2026-07-24T12:05:00+00:00",
        metrics={
            "sample_count": 10,
            "valid_sample_count": 4,
            "valid_sample_ratio": 0.40,
            "question_count": 0,
        },
        task_results=[],
        has_tracking_plot=False,
        has_tracking_timeline=False,
        task_detail_kind=None,
    )

    assert "数据质量不足，谨慎解释" in document
    assert "眼动任务不能单独诊断意识状态" in document
    assert "不能替代 CRS-R" in document
