from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
)
from pytestqt.qtbot import QtBot

from oculidoc.application import RegisterPatientRequest
from oculidoc.application.experiment_session_service import (
    CreateExperimentSessionRequest,
)
from oculidoc.config import (
    AdminUiMode,
    AdminUiPreferences,
    AdminUiPreferencesStore,
    Settings,
)
from oculidoc.domain.experiment_session import ExperimentSessionStatus
from oculidoc.infrastructure.database import initialize_database
from oculidoc.task_configs import TaskConfigStore
from oculidoc.ui.main_window import AdminMainWindow
from oculidoc.ui.patient_management import PatientManagementDialog
from oculidoc.ui.test_plan import (
    BinaryAxisOrder,
)
from oculidoc.ui.test_plan import (
    TestPlan as CurrentPlan,
)
from oculidoc.ui.test_plan import (
    TestPlanDialog as PlanDialog,
)
from oculidoc.ui.test_plan import (
    TestPlanStepStatus as PlanStepStatus,
)
from oculidoc.ui.test_plan import (
    TestPlanStore as PlanStore,
)


def test_default_workbench_lists_only_active_patients_without_auto_selection(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    runtime = initialize_database(settings.database_path, data_root=tmp_path)
    active = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="DOC-ACTIVE", family_name="启用")
    )
    inactive = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="DOC-INACTIVE", family_name="停用")
    )
    runtime.patient_service.deactivate_patient(inactive.patient_id)

    window = AdminMainWindow(
        settings,
        runtime.patient_service,
        runtime.experiment_session_service,
    )
    qtbot.addWidget(window)

    assert window.ui_mode is AdminUiMode.CLINICAL_WORKBENCH
    assert window.current_patient is None
    labels = [
        window.workbench_patient_list.item(index).text()
        for index in range(window.workbench_patient_list.count())
    ]
    assert any(active.patient_code in label for label in labels)
    assert all(inactive.patient_code not in label for label in labels)
    management_button = window.findChild(
        QPushButton,
        "workbenchPatientManagementButton",
    )
    assert management_button is not None
    assert management_button.text() == "患者管理"
    assert all(button.text() != "新增患者" for button in window.findChildren(QPushButton))
    assert window.findChild(QPushButton, "adminSettingsButton") is not None
    assert len(window.module_buttons) == 10
    assert "font-size:34px" in window.patient_label.styleSheet()
    assert "font-size:26px" in window.next_task_label.styleSheet()
    window.resize(1366, 768)
    window.show()
    qtbot.wait(20)
    work_area = window.plan_button.parentWidget()
    assert work_area is not None
    assert window.plan_button.geometry().top() > work_area.height() * 0.55

    window.close()
    runtime.dispose()


def test_switching_patients_switches_task_habits_and_new_patient_starts_default(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    runtime = initialize_database(settings.database_path, data_root=tmp_path)
    first = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="DOC-HABIT-A", family_name="甲")
    )
    second = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="DOC-HABIT-B", family_name="乙")
    )
    window = AdminMainWindow(
        settings,
        runtime.patient_service,
        runtime.experiment_session_service,
    )
    qtbot.addWidget(window)

    window._set_current_patient(first)
    first_record = window._task_config_store.load("tracking_ball")
    first_config = dict(first_record.config)
    first_config["diameter_px"] = 180
    window._task_config_store.save(
        "tracking_ball",
        first_config,
        expected_revision=first_record.revision,
    )

    window._set_current_patient(second)
    assert window._task_config_store.load("tracking_ball").config["diameter_px"] == 300
    window._set_current_patient(first)
    assert window._task_config_store.load("tracking_ball").config["diameter_px"] == 180
    assert window.patient_label.text().startswith(f"当前患者：{first.display_label}")
    assert "font-size:34px" in window.patient_label.styleSheet()

    window.close()
    runtime.dispose()


def test_classic_preference_preserves_original_shell_and_can_switch_back(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    store = AdminUiPreferencesStore.for_settings(settings)
    store.save(AdminUiPreferences(mode=AdminUiMode.CLASSIC))

    window = AdminMainWindow(settings)
    qtbot.addWidget(window)
    assert window.ui_mode is AdminUiMode.CLASSIC
    assert not hasattr(window, "workbench_patient_list")
    assert len(window.module_buttons) == 10
    assert (
        window.findChild(
            QPushButton,
            "moduleButton_eye_observation",
        )
        is not None
    )

    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args, **kwargs: ("患者工作台（默认）", True),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    window._open_admin_settings()

    assert window.ui_mode is AdminUiMode.CLASSIC
    assert store.load().mode is AdminUiMode.CLINICAL_WORKBENCH
    window.close()


def test_plan_dialog_defaults_skip_undo_and_axis_exception(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    runtime = initialize_database(settings.database_path)
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="DOC-PLAN", family_name="计划")
    )
    plan = CurrentPlan.default(str(patient.patient_id))
    dialog = PlanDialog(
        settings,
        patient,
        plan,
        TaskConfigStore(tmp_path / "runtime" / "task_configs.json"),
        gaze_status_text="眼动源：工程模拟测试",
    )
    qtbot.addWidget(dialog)

    optional = dialog.findChild(
        QCheckBox,
        "testPlanSelected_eye_observation",
    )
    preference = dialog.findChild(
        QCheckBox,
        "testPlanSelected_visual_preference",
    )
    assert optional is not None and not optional.isChecked()
    assert preference is not None and preference.isChecked()
    assert dialog.plan.progress == (0, 11)
    preference_row = preference.parentWidget()
    assert preference_row is not None
    assert "#1565c0" in str(preference_row.styleSheet())
    preference.setChecked(False)
    assert not next(
        step for step in dialog.plan.steps if step.step_id == "visual_preference"
    ).selected
    assert dialog.plan.progress == (0, 10)
    preference.setChecked(True)
    first_rest = dialog.findChild(
        QCheckBox,
        "testPlanRest_gaze_games:starlight_route",
    )
    second_rest = dialog.findChild(
        QCheckBox,
        "testPlanRest_binary_vertical",
    )
    assert first_rest is not None and first_rest.isChecked()
    assert second_rest is not None and second_rest.isChecked()

    axis = dialog.findChild(QComboBox, "testPlanAxisOrderCombo")
    assert axis is not None
    axis.setCurrentIndex(axis.findData(BinaryAxisOrder.VERTICAL_FIRST.value))
    assert dialog.plan.axis_order is BinaryAxisOrder.VERTICAL_FIRST
    assert [step.step_id for step in dialog.plan.steps][8:10] == [
        "binary_vertical",
        "binary_horizontal",
    ]

    assert not dialog.findChildren(QSlider)
    tracking_choice = dialog.findChild(
        QRadioButton,
        "testPlanBlockChoice_tracking_ball",
    )
    assert tracking_choice is not None
    tracking_choice.setChecked(True)
    dialog.copy_block_button.click()
    assert [step.step_id for step in dialog.plan.steps].count("tracking_ball") == 2
    assert (
        dialog.plan.steps[
            dialog.plan.steps.index(
                next(
                    step for step in dialog.plan.steps if step.block_id == dialog._selected_block_id
                )
            )
            - 1
        ].step_id
        == "tracking_ball"
    )
    dialog.delete_block_button.click()
    assert [step.step_id for step in dialog.plan.steps].count("tracking_ball") == 1

    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args, **kwargs: ("9 · 屏幕打字", True),
    )
    tracking_choice = dialog.findChild(
        QRadioButton,
        "testPlanBlockChoice_tracking_ball",
    )
    assert tracking_choice is not None
    tracking_choice.setChecked(True)
    dialog.insert_block_button.click()
    assert [step.step_id for step in dialog.plan.steps].count("screen_keyboard") == 2
    dialog.delete_block_button.click()
    assert [step.step_id for step in dialog.plan.steps].count("screen_keyboard") == 1

    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args, **kwargs: ("患者疲劳", True),
    )
    dialog._skip_step("tracking_ball")
    tracking = next(step for step in dialog.plan.steps if step.step_id == "tracking_ball")
    assert tracking.status is PlanStepStatus.SKIPPED
    assert tracking.session_id is None

    dialog._undo_skip("tracking_ball")
    tracking = next(step for step in dialog.plan.steps if step.step_id == "tracking_ball")
    assert tracking.status is PlanStepStatus.PENDING

    dialog._save()
    assert dialog.saved_plan is not None
    assert dialog.saved_plan.progress == (0, 11)
    runtime.dispose()


def test_plan_dialog_keeps_block_actions_usable_after_prior_results(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    runtime = initialize_database(settings.database_path)
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="DOC-EDIT", family_name="编排")
    )
    plan = CurrentPlan.default(str(patient.patient_id))
    tracking = next(step for step in plan.steps if step.step_id == "tracking_ball")
    plan = plan.replace_step(tracking.start("session-finished").finish(PlanStepStatus.COMPLETED))
    dialog = PlanDialog(
        settings,
        patient,
        plan,
        TaskConfigStore(tmp_path / "runtime" / "task_configs.json"),
        gaze_status_text="眼动源：工程模拟测试",
    )
    qtbot.addWidget(dialog)

    choice = dialog.findChild(
        QRadioButton,
        f"testPlanBlockChoice_{tracking.block_id}",
    )
    assert choice is not None
    choice.setChecked(True)

    assert choice.text() == "已选定"
    parent = choice.parentWidget()
    assert parent is not None and "#1565c0" in parent.styleSheet()
    assert not dialog.delete_block_button.isEnabled()
    assert dialog.copy_block_button.isEnabled()
    assert dialog.insert_block_button.isEnabled()

    dialog.copy_block_button.click()
    original_index = next(
        index for index, step in enumerate(dialog.plan.steps) if step.block_id == tracking.block_id
    )
    copied = dialog.plan.steps[original_index + 1]
    assert copied.step_id == "tracking_ball"
    assert copied.status is PlanStepStatus.PENDING
    assert copied.session_id is None

    dialog.close()
    runtime.dispose()


def test_workbench_plan_uses_real_session_id_and_keeps_failure_distinct(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    runtime = initialize_database(settings.database_path, data_root=tmp_path)
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="DOC-RUN", family_name="运行")
    )
    plan_store = PlanStore.for_settings(settings)
    plan = plan_store.save(
        CurrentPlan.default(str(patient.patient_id)),
        expected_revision=0,
    )
    window = AdminMainWindow(
        settings,
        runtime.patient_service,
        runtime.experiment_session_service,
    )
    qtbot.addWidget(window)
    window._set_current_patient(patient)

    launches: list[dict[str, object]] = []
    monkeypatch.setattr(
        window,
        "_open_gaze_task_module",
        lambda module, **kwargs: launches.append({"module_id": module.module_id, **kwargs}),
    )
    window._start_next_plan_step()
    assert launches == [
        {
            "module_id": "visual_preference",
            "config_revision": 0,
            "game_mode": None,
            "plan_step_id": "visual_preference",
        }
    ]

    session = runtime.experiment_session_service.create_session(
        CreateExperimentSessionRequest(
            patient_id=patient.patient_id,
            module_id="visual_preference",
        )
    )
    runtime.experiment_session_service.start_session(session.session_id)
    window._mark_plan_step_running(
        patient.patient_id,
        "visual_preference",
        session.session_id,
    )
    running = plan_store.load(str(patient.patient_id))
    assert running is not None
    preference = next(step for step in running.steps if step.step_id == "visual_preference")
    assert preference.status is PlanStepStatus.RUNNING
    assert preference.session_id == str(session.session_id)

    runtime.experiment_session_service.fail_session(
        session.session_id,
        "设备中断",
    )
    window._finish_plan_step_for_session(
        patient.patient_id,
        session.session_id,
        ExperimentSessionStatus.FAILED,
    )
    failed = plan_store.load(str(patient.patient_id))
    assert failed is not None
    preference = next(step for step in failed.steps if step.step_id == "visual_preference")
    assert preference.status is PlanStepStatus.FAILED
    assert failed.next_pending_step is not None
    assert failed.next_pending_step.step_id == "tracking_ball"
    assert failed.progress == (1, 11)
    assert failed.revision > plan.revision

    window.close()
    runtime.dispose()


def test_changed_task_config_blocks_silent_plan_launch(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    runtime = initialize_database(settings.database_path, data_root=tmp_path)
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="DOC-CONFLICT", family_name="冲突")
    )
    PlanStore.for_settings(settings).save(
        CurrentPlan.default(str(patient.patient_id)),
        expected_revision=0,
    )
    config_store = TaskConfigStore(tmp_path / "runtime" / "task_configs.json")
    current = config_store.load("visual_preference")
    config_store.save(
        "visual_preference",
        current.config,
        expected_revision=current.revision,
    )
    window = AdminMainWindow(
        settings,
        runtime.patient_service,
        runtime.experiment_session_service,
    )
    qtbot.addWidget(window)
    window._set_current_patient(patient)

    warnings: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )
    monkeypatch.setattr(
        window,
        "_open_gaze_task_module",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stale plan must not launch")),
    )
    window._start_next_plan_step()

    assert any("设置已更新，请重新确认" in str(call) for call in warnings)
    window.close()
    runtime.dispose()


def test_running_patient_cannot_be_deactivated_but_plan_is_not_patient_data(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    runtime = initialize_database(settings.database_path)
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="DOC-BUSY", family_name="忙碌")
    )
    plan_store = PlanStore.for_settings(settings)
    saved_plan = plan_store.save(
        CurrentPlan.default(str(patient.patient_id)),
        expected_revision=0,
    )
    dialog = PatientManagementDialog(
        runtime.patient_service,
        is_patient_session_active=lambda patient_id: patient_id == patient.patient_id,
    )
    qtbot.addWidget(dialog)
    dialog.patient_list.setCurrentRow(0)

    messages: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append(args),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: QMessageBox.StandardButton.Yes,
    )
    dialog._toggle_patient_status()
    assert runtime.patient_service.get_patient(patient.patient_id).is_active
    assert messages

    runtime.patient_service.deactivate_patient(patient.patient_id)
    assert plan_store.load(str(patient.patient_id)) == saved_plan
    runtime.patient_service.activate_patient(patient.patient_id)
    assert plan_store.load(str(patient.patient_id)) == saved_plan
    assert UUID(saved_plan.patient_id) == patient.patient_id
    runtime.dispose()
