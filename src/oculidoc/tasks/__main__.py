"""Standalone demos for gaze-driven tasks."""

import argparse

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QMessageBox,
)

from oculidoc.app import create_qt_application
from oculidoc.config import get_settings
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "task",
        choices=(
            "tracking",
            "binary",
        ),
    )
    args = parser.parse_args()

    app = create_qt_application()
    settings = get_settings()

    if args.task == "tracking":
        setup = TrackingBallSetupDialog()

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()
        task = TrackingBallTask(config)
        title = "追踪球"
        duration_seconds = config.duration_seconds
    else:
        setup = BinaryQuestionSetupDialog()

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()
        task = BinaryQuestionTask(config)
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
    worker.sample_received.connect(task.consume_sample)
    worker.stream_error.connect(
        lambda message: QMessageBox.warning(
            window,
            "眼动源连接失败",
            message + "\n\n当前仍可使用鼠标测试。",
        )
    )

    window.finished.connect(lambda reason: app.quit())
    app.aboutToQuit.connect(worker.stop)

    window.showFullScreen()
    window.start()
    worker.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
