from __future__ import annotations

from pathlib import Path

import pytest

from oculidoc.api.mobile_page import mobile_control_html
from oculidoc.lan_commands import (
    LanCommandStatus,
    LanCommandStore,
    LanCommandType,
)


def test_command_store_lifecycle(tmp_path: Path) -> None:
    store = LanCommandStore(tmp_path / "commands")
    submitted = store.submit(
        LanCommandType.START_TASK,
        payload={"module_id": "tracking_ball"},
    )

    assert submitted.status is LanCommandStatus.PENDING
    assert store.pending() == (submitted,)

    accepted = store.transition(
        submitted.command_id,
        LanCommandStatus.ACCEPTED,
        "桌面端已接收。",
    )
    assert accepted.status is LanCommandStatus.ACCEPTED
    assert store.pending() == ()

    completed = store.transition(
        submitted.command_id,
        LanCommandStatus.COMPLETED,
        "任务设置窗口已打开。",
    )
    assert completed.status.is_terminal
    assert store.load(submitted.command_id).message == "任务设置窗口已打开。"


def test_terminal_command_cannot_transition(tmp_path: Path) -> None:
    store = LanCommandStore(tmp_path / "commands")
    command = store.submit(LanCommandType.OPEN_PATIENT_DISPLAY)
    store.transition(
        command.command_id,
        LanCommandStatus.REJECTED,
        "拒绝。",
    )

    with pytest.raises(ValueError, match="Invalid LAN command transition"):
        store.transition(
            command.command_id,
            LanCommandStatus.ACCEPTED,
            "不允许。",
        )


def test_mobile_page_contains_desktop_commands() -> None:
    html = mobile_control_html("secret-pairing-token")

    assert "桌面管理员命令" in html
    assert "open_patient_display" in html
    assert "start_task" in html
    assert "stop_task" in html
