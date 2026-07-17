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

        task = TrackingBallTask(setup.build_config())
    else:
        setup = BinaryQuestionSetupDialog()

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        task = BinaryQuestionTask(setup.build_config())
        task.answered.connect(
            lambda side, answer: QTimer.singleShot(
                1_200,
                app.quit,
            )
        )

    worker = GazeStreamWorker(settings)
    worker.sample_received.connect(task.consume_sample)
    worker.stream_error.connect(
        lambda message: QMessageBox.warning(
            task,
            "眼动源连接失败",
            message + "\n\n当前仍可使用鼠标进行界面测试。",
        )
    )
    app.aboutToQuit.connect(worker.stop)

    task.showFullScreen()
    task.start()
    worker.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
