"""Standalone demos for gaze-driven tasks."""

import argparse
from collections.abc import Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QMessageBox,
)

from oculidoc.app import create_qt_application
from oculidoc.config import get_settings
from oculidoc.experiments.task_runtime import RecordedTaskRuntime
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
    settings = get_settings()
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

    worker = GazeStreamWorker(
        settings,
        window,
    )
    recorded_runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        announce=True,
        parent=task,
    )
    worker.sample_received.connect(recorded_runtime.handle_sample)
    worker.stream_error.connect(
        lambda message: QMessageBox.warning(
            window,
            "眼动源连接失败",
            message + "\n\n当前仍可使用鼠标测试。",
        )
    )

    window.finished.connect(recorded_runtime.finish)
    window.finished.connect(lambda reason: app.quit())
    app.aboutToQuit.connect(worker.stop)

    window.showFullScreen()
    window.start()
    worker.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
