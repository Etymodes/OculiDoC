"""Small guards for standalone task process outcomes."""

from oculidoc.tasks.__main__ import _task_exit_code


def test_device_error_uses_failure_exit_code() -> None:
    assert _task_exit_code("device_error") == 3


def test_user_exit_keeps_result_file_available_for_abort_classification() -> None:
    assert _task_exit_code("manual_exit") == 0
    assert _task_exit_code("window_closed") == 0
