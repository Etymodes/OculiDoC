from collections.abc import Callable

from pytestqt.qtbot import QtBot

import oculidoc.ui.gaze_self_check as gaze_self_check_module
from oculidoc.config import Settings
from oculidoc.ui.gaze_self_check import GazeSelfCheckDialog


class StubWorker:
    def __init__(
        self,
        settings: Settings,
        parent: object,
        **kwargs: object,
    ) -> None:
        del settings, parent, kwargs
        self.status_changed = StubSignal()
        self.preflight_completed = StubSignal()
        self.sample_received = StubSignal()
        self.stream_error = StubSignal()

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def enable_sample_delivery(self) -> None:
        return None


class StubSignal:
    def connect(self, callback: Callable[..., object]) -> None:
        del callback


def test_self_check_excludes_subjective_eye_position_view(
    qtbot: QtBot,
    monkeypatch,
) -> None:
    monkeypatch.setattr(gaze_self_check_module, "GazeStreamWorker", StubWorker)
    dialog = GazeSelfCheckDialog(Settings(environment="test"))
    qtbot.addWidget(dialog)

    assert not hasattr(dialog, "canvas")
    assert "采样率" in dialog.metrics_label.text()
    assert any(
        "OpoinThesis" in label.text()
        for label in dialog.findChildren(gaze_self_check_module.QLabel)
    )
    assert "实际采集字段" in dialog.capability_label.text()
