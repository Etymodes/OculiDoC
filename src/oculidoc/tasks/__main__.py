"""Standalone demos for gaze-driven tasks."""

import argparse
from collections.abc import Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QMessageBox,
)

from oculidoc.app import create_qt_application
from oculidoc.config import apply_saved_gaze_device_config, get_settings
from oculidoc.devices.preflight import GazePreflightResult, GazePreflightStore
from oculidoc.experiments.task_runtime import RecordedTaskRuntime
from oculidoc.lan_control import (
    LanControlStateStore,
    LanControlTransitionError,
    PatientDisplayMode,
)
from oculidoc.task_configs import (
    TaskConfigConflict,
    TaskConfigStore,
    task_config_from_dict,
    task_config_to_dict,
)
from oculidoc.tasks.binary_question import (
    BinaryQuestionSetupDialog,
    BinaryQuestionTask,
)
from oculidoc.tasks.gaze_stream import (
    GazeStreamWorker,
)
from oculidoc.tasks.task_window import (
    TimedTaskWindow,
)
from oculidoc.tasks.tracking_ball import (
    TrackingBallSetupDialog,
    TrackingBallTask,
)

TASK_START_COUNTDOWN_SECONDS = 3


def main(
    argv: Sequence[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "task",
        choices=(
            "tracking",
            "binary",
        ),
    )
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--config-revision", type=int)
    args = parser.parse_args(argv)

    if args.direct != (args.config_revision is not None):
        parser.error("--direct and --config-revision must be used together.")

    app = create_qt_application()
    settings = apply_saved_gaze_device_config(get_settings())
    allow_mouse_fallback = settings.gaze_source == "mock"
    module_id = "tracking_ball" if args.task == "tracking" else "binary_horizontal"
    config_store = TaskConfigStore(settings.data_dir / "runtime" / "task_configs.json")
    record = config_store.load(module_id)
    config = task_config_from_dict(module_id, record.config)

    if args.direct:
        if args.config_revision != record.revision:
            raise SystemExit(
                "Task config revision changed before launch: "
                f"requested {args.config_revision}, current {record.revision}."
            )
    elif args.task == "tracking":
        setup = TrackingBallSetupDialog(config=config)

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()
    else:
        setup = BinaryQuestionSetupDialog(
            question_bank_path=(settings.data_dir / "common_questions.json"),
            config=config,
        )

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()

    if not args.direct:
        try:
            config_store.save(
                module_id,
                task_config_to_dict(config),
                expected_revision=record.revision,
            )
        except TaskConfigConflict:
            QMessageBox.warning(
                setup,
                "任务设置已更新",
                "手机端已修改这项任务设置。请关闭后重新打开设置窗口。",
            )
            return 2

    if args.task == "tracking":
        task = TrackingBallTask(
            config,
            allow_mouse_fallback=(allow_mouse_fallback),
        )
        title = "追踪球"
        duration_seconds = config.duration_seconds
    else:
        task = BinaryQuestionTask(
            config,
            allow_mouse_fallback=(allow_mouse_fallback),
        )
        title = "左右二分问答"
        duration_seconds = config.duration_seconds

    window = TimedTaskWindow(
        task,
        duration_seconds=duration_seconds,
        title=title,
    )

    if isinstance(
        task,
        BinaryQuestionTask,
    ):
        task.answered.connect(
            lambda side, answer: QTimer.singleShot(
                700,
                lambda: window.finish("answered"),
            )
        )

    preflight_seconds = 0 if settings.environment == "test" else settings.gaze_preflight_seconds
    preflight_store = GazePreflightStore(settings.data_dir / "runtime" / "gaze_preflight.json")
    worker = GazeStreamWorker(
        settings,
        window,
        preflight_seconds=preflight_seconds,
        preflight_store=preflight_store,
    )
    recorded_runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        announce=True,
        parent=task,
    )
    worker.sample_received.connect(recorded_runtime.handle_sample)

    window.finished.connect(recorded_runtime.finish)
    window.finished.connect(lambda reason: app.quit())
    app.aboutToQuit.connect(worker.stop)

    display_state_store = LanControlStateStore(
        settings.data_dir / "runtime" / "lan_control_state.json"
    )
    countdown_seconds = 0 if settings.environment == "test" else TASK_START_COUNTDOWN_SECONDS
    source_hint = "\n模拟模式" if settings.gaze_source == "mock" else ""
    display_state_store.set_display(
        f"{title}\n正在进行眼动设备预检{source_hint}",
        mode=PatientDisplayMode.PREVIEW,
        task_id=module_id,
    )

    preflight_failed = False

    def fail_preflight(message: str) -> None:
        nonlocal preflight_failed

        if preflight_failed:
            return

        preflight_failed = True
        try:
            display_state_store.set_display(
                "眼动设备预检失败\n请联系管理员",
                mode=PatientDisplayMode.ERROR,
                task_id=module_id,
            )
        except LanControlTransitionError:
            pass
        QMessageBox.warning(
            window,
            "眼动设备预检失败",
            message + "\n\n任务已阻止，不会回退到模拟眼动源。",
        )
        app.exit(3)

    worker.stream_error.connect(fail_preflight)

    def start_task() -> None:
        current = display_state_store.load()

        if current.mode is not PatientDisplayMode.READY or current.task_id != module_id:
            app.quit()
            return

        display_state_store.set_display(
            f"正在进行：{title}",
            mode=PatientDisplayMode.RUNNING,
            task_id=module_id,
        )
        worker.enable_sample_delivery()
        window.showFullScreen()
        window.start()

    def begin_countdown(result: GazePreflightResult) -> None:
        if not result.passed:
            return

        current = display_state_store.load()
        if current.mode is not PatientDisplayMode.PREVIEW or current.task_id != module_id:
            app.exit(2)
            return

        try:
            display_state_store.set_display(
                f"{title}\n即将开始\n{countdown_seconds}",
                mode=PatientDisplayMode.READY,
                task_id=module_id,
                countdown_seconds=countdown_seconds,
            )
        except LanControlTransitionError:
            app.exit(2)
            return

        if countdown_seconds == 0:
            start_task()
            return

        remaining_seconds = countdown_seconds
        countdown_timer = QTimer(window)
        countdown_timer.setInterval(1_000)

        def advance_countdown() -> None:
            nonlocal remaining_seconds
            current = display_state_store.load()

            if current.mode is not PatientDisplayMode.READY or current.task_id != module_id:
                countdown_timer.stop()
                app.quit()
                return

            remaining_seconds -= 1

            if remaining_seconds <= 0:
                countdown_timer.stop()
                start_task()
                return

            display_state_store.set_display(
                f"{title}\n即将开始\n{remaining_seconds}",
                mode=PatientDisplayMode.READY,
                task_id=module_id,
                countdown_seconds=remaining_seconds,
            )

        countdown_timer.timeout.connect(advance_countdown)
        countdown_timer.start()

    worker.preflight_completed.connect(begin_countdown)
    worker.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
