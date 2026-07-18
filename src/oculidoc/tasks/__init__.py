"""Reusable gaze-driven task widgets."""

from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionSetupDialog,
    BinaryQuestionTask,
)
from oculidoc.tasks.gaze_stream import (
    GazeStreamWorker,
    create_eye_tracker,
)
from oculidoc.tasks.question_bank import (
    BinaryQuestionType,
    CommonQuestionStore,
    CommonQuestionTemplate,
)
from oculidoc.tasks.task_window import (
    TimedTaskWindow,
)
from oculidoc.tasks.tracking_ball import (
    TargetEffect,
    TargetPath,
    TargetShape,
    TrackingBallConfig,
    TrackingBallSetupDialog,
    TrackingBallTask,
)

__all__ = [
    "BinaryQuestionConfig",
    "BinaryQuestionSetupDialog",
    "BinaryQuestionTask",
    "BinaryQuestionType",
    "CommonQuestionStore",
    "CommonQuestionTemplate",
    "GazeStreamWorker",
    "TargetEffect",
    "TimedTaskWindow",
    "TargetPath",
    "TargetShape",
    "TrackingBallConfig",
    "TrackingBallSetupDialog",
    "TrackingBallTask",
    "create_eye_tracker",
]
