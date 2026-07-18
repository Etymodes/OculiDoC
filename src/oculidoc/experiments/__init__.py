"""Experiment recording and analysis primitives."""

from oculidoc.experiments.recording import (
    AoiRole,
    NormalizedAoi,
    RecorderState,
    ScreenContext,
    TaskRunRecorder,
)
from oculidoc.experiments.task_runtime import RecordedTaskRuntime

__all__ = [
    "AoiRole",
    "NormalizedAoi",
    "RecorderState",
    "RecordedTaskRuntime",
    "ScreenContext",
    "TaskRunRecorder",
]
