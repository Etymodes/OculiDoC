"""Small guards for standalone task process outcomes."""

from types import SimpleNamespace
from unittest.mock import Mock

from oculidoc.tasks.__main__ import (
    _place_window_on_patient_screen,
    _task_exit_code,
)


def test_device_error_uses_failure_exit_code() -> None:
    assert _task_exit_code("device_error") == 3


def test_user_exit_keeps_result_file_available_for_abort_classification() -> None:
    assert _task_exit_code("manual_exit") == 0
    assert _task_exit_code("window_closed") == 0


def test_patient_task_is_placed_on_the_named_screen() -> None:
    operator = SimpleNamespace(name=lambda: "operator")
    patient = SimpleNamespace(
        name=lambda: "patient",
        geometry=lambda: SimpleNamespace(topLeft=lambda: (-1920, 0)),
    )
    window = Mock()

    placed = _place_window_on_patient_screen(
        window,  # type: ignore[arg-type]
        SimpleNamespace(screens=lambda: [operator, patient]),  # type: ignore[arg-type]
        "patient",
    )

    assert placed is True
    window.setScreen.assert_called_once_with(patient)
    window.move.assert_called_once_with((-1920, 0))
    window.winId.assert_not_called()
    window.windowHandle.assert_not_called()


def test_patient_task_rejects_an_unavailable_target_screen() -> None:
    window = Mock()

    placed = _place_window_on_patient_screen(
        window,  # type: ignore[arg-type]
        SimpleNamespace(screens=lambda: [SimpleNamespace(name=lambda: "operator")]),  # type: ignore[arg-type]
        "disconnected-patient-screen",
    )

    assert placed is False
    window.setScreen.assert_not_called()
    window.move.assert_not_called()
