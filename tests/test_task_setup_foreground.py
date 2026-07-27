from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from oculidoc.tasks.__main__ import _exec_task_setup


def test_task_setup_is_raised_above_prepared_patient_screen() -> None:
    events: list[object] = []

    class StubDialog:
        def setWindowFlag(self, flag: Qt.WindowType, enabled: bool = True) -> None:
            events.append(("flag", flag, enabled))

        def show(self) -> None:
            events.append("show")

        def raise_(self) -> None:
            events.append("raise")

        def activateWindow(self) -> None:
            events.append("activate")

        def exec(self) -> int:
            events.append("exec")
            return int(QDialog.DialogCode.Accepted)

    result = _exec_task_setup(StubDialog())  # type: ignore[arg-type]

    assert result is QDialog.DialogCode.Accepted
    assert events == [
        ("flag", Qt.WindowType.WindowStaysOnTopHint, True),
        "show",
        "raise",
        "activate",
        "exec",
    ]
