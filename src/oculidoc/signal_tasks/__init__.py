"""Independent EEG, SSVEP, and motor-imagery task workflows."""

from oculidoc.signal_tasks.config import (
    SIGNAL_TASK_CAPABILITIES,
    SignalTaskCapability,
    SignalTaskConfig,
    SignalTaskKind,
)
from oculidoc.signal_tasks.runner import SignalTaskCancelled, run_signal_task

__all__ = [
    "SIGNAL_TASK_CAPABILITIES",
    "SignalTaskCancelled",
    "SignalTaskCapability",
    "SignalTaskConfig",
    "SignalTaskKind",
    "run_signal_task",
]
