from __future__ import annotations

from pytestqt.qtbot import QtBot

import oculidoc.ui.main_window as main_window_module
from oculidoc.config import Settings
from oculidoc.devices.preflight import GazePreflightResult, GazePreflightStore
from oculidoc.lan_control import PatientDisplayMode
from oculidoc.ui.main_window import AdminMainWindow


def test_main_window_displays_native_tobii_source(
    qtbot: QtBot,
    tmp_path,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="tobii_stream_engine",
        )
    )
    qtbot.addWidget(window)

    assert window.gaze_status_label.text() == ("眼动源：Tobii Eye Tracker 5 · 尚未预检")
    assert "#b42318" in window.gaze_status_label.styleSheet()


def test_main_window_marks_mock_source(
    qtbot: QtBot,
    tmp_path,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)

    assert window.gaze_status_label.text() == "眼动源：模拟模式（仅工程测试）"
    assert "#6b7280" in window.gaze_status_label.styleSheet()


def test_main_window_displays_latest_live_gaze_quality(qtbot: QtBot, tmp_path) -> None:
    GazePreflightStore(tmp_path / "runtime" / "gaze_preflight.json").save(
        GazePreflightResult(
            source="tobii_stream_engine",
            device_name="Tobii Eye Tracker 5",
            device_url="tobii://device-1",
            library_path="C:/Tobii/tobii_stream_engine.dll",
            duration_seconds=3.0,
            sample_count=99,
            valid_sample_count=98,
            sample_rate_hz=33.0,
            valid_ratio=98 / 99,
            minimum_valid_ratio=0.60,
            passed=True,
            error=None,
            updated_at_utc="2026-07-20T00:00:00+00:00",
        )
    )
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="tobii_stream_engine",
        )
    )
    qtbot.addWidget(window)

    assert "33 Hz" in window.gaze_status_label.text()
    assert "有效率 99%" in window.gaze_status_label.text()
    assert "#176b36" in window.gaze_status_label.styleSheet()


def test_main_window_marks_low_live_validity_yellow(qtbot: QtBot, tmp_path) -> None:
    GazePreflightStore(tmp_path / "runtime" / "gaze_preflight.json").save(
        GazePreflightResult(
            source="tobii_stream_engine",
            device_name="Tobii Eye Tracker 5",
            device_url="tobii://device-1",
            library_path=None,
            duration_seconds=3.0,
            sample_count=90,
            valid_sample_count=20,
            sample_rate_hz=30.0,
            valid_ratio=20 / 90,
            minimum_valid_ratio=0.60,
            passed=False,
            error="有效率低于要求",
            updated_at_utc="2026-07-20T00:00:00+00:00",
        )
    )
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="tobii_stream_engine",
        )
    )
    qtbot.addWidget(window)

    assert "有效率不足" in window.gaze_status_label.text()
    assert "#8a5a00" in window.gaze_status_label.styleSheet()


def test_main_window_exposes_lan_pairing_status(
    qtbot: QtBot,
    tmp_path,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)

    assert "本地后台：准备启动" in window.backend_status_button.text()
    assert window._lan_control_url.startswith("http://")
    assert "/control?token=" in window._lan_control_url


def test_main_window_polls_mobile_display_state(
    qtbot: QtBot,
    tmp_path,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)

    state = window._lan_state_store.set_display(
        "来自手机的患者端文字",
        mode="message",
    )
    window._poll_lan_control_state()

    assert window._last_lan_revision == state.revision
    assert window._patient_window.placeholder_label.text() == "来自手机的患者端文字"
    assert window._patient_window.current_state.mode is PatientDisplayMode.PREVIEW


def test_desktop_projects_text_through_shared_state(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    window = AdminMainWindow(Settings(environment="test", data_dir=tmp_path, gaze_source="mock"))
    qtbot.addWidget(window)
    monkeypatch.setattr(
        main_window_module.QInputDialog,
        "getMultiLineText",
        lambda *args: ("请看向屏幕中央", True),
    )

    window._project_patient_text()

    state = window._lan_state_store.load()
    assert state.mode is PatientDisplayMode.PREVIEW
    assert state.text == "请看向屏幕中央"
    assert window._patient_window.isVisible()


def test_main_window_refreshes_lan_address(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)
    window._ensure_pairing_dialog()

    monkeypatch.setattr(
        "oculidoc.ui.main_window.preferred_private_ipv4",
        lambda: "192.168.50.25",
    )
    window._refresh_lan_pairing_address()

    assert window._lan_host == "192.168.50.25"
    assert window._lan_control_url.startswith("http://192.168.50.25:")
    assert window._pairing_dialog is not None
    assert window._pairing_dialog.url_edit.text() == window._lan_control_url


def test_pairing_click_pins_and_second_click_closes(
    qtbot: QtBot,
    tmp_path,
) -> None:
    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)

    window._toggle_lan_pairing_pin()
    assert window._pairing_pinned
    assert window._pairing_dialog is not None
    assert window._pairing_dialog.isVisible()

    window._toggle_lan_pairing_pin()
    assert not window._pairing_pinned
    assert not window._pairing_dialog.isVisible()


def test_desktop_completes_open_display_command(
    qtbot: QtBot,
    tmp_path,
) -> None:
    from oculidoc.lan_commands import (
        LanCommandStatus,
        LanCommandType,
    )

    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)
    command = window._lan_command_store.submit(LanCommandType.OPEN_PATIENT_DISPLAY)

    window._poll_lan_commands()

    completed = window._lan_command_store.load(command.command_id)
    assert completed.status is LanCommandStatus.COMPLETED
    assert window._patient_window.isVisible()


def test_desktop_rejects_remote_start_without_patient(
    qtbot: QtBot,
    tmp_path,
) -> None:
    from oculidoc.lan_commands import (
        LanCommandStatus,
        LanCommandType,
    )

    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)
    command = window._lan_command_store.submit(
        LanCommandType.START_TASK,
        payload={"module_id": "tracking_ball"},
    )

    window._poll_lan_commands()

    rejected = window._lan_command_store.load(command.command_id)
    assert rejected.status is LanCommandStatus.REJECTED
    assert "尚未选择患者" in rejected.message


def test_desktop_accepts_remote_start_after_validation(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    from oculidoc.lan_commands import (
        LanCommandStatus,
        LanCommandType,
    )

    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)
    window.current_patient = object()
    window.experiment_session_service = object()

    launched: list[int | None] = []

    def fake_open(module, *, config_revision=None) -> None:
        launched.append(config_revision)
        window._active_gaze_module_ids.add(module.module_id)

    monkeypatch.setattr(window, "_open_gaze_task_module", fake_open)
    command = window._lan_command_store.submit(
        LanCommandType.START_TASK,
        payload={"module_id": "tracking_ball", "config_revision": 0},
    )

    window._poll_lan_commands()

    completed = window._lan_command_store.load(command.command_id)
    assert completed.status is LanCommandStatus.COMPLETED
    assert "直接启动" in completed.message
    assert launched == [0]
    assert window._lan_state_store.load().mode is PatientDisplayMode.PREVIEW


def test_desktop_rejects_remote_start_with_stale_config(
    qtbot: QtBot,
    tmp_path,
) -> None:
    from oculidoc.lan_commands import (
        LanCommandStatus,
        LanCommandType,
    )

    window = AdminMainWindow(Settings(environment="test", data_dir=tmp_path, gaze_source="mock"))
    qtbot.addWidget(window)
    window.current_patient = object()
    window.experiment_session_service = object()
    record = window._task_config_store.load("tracking_ball")
    window._task_config_store.save(
        "tracking_ball",
        record.config,
        expected_revision=record.revision,
    )
    command = window._lan_command_store.submit(
        LanCommandType.START_TASK,
        payload={"module_id": "tracking_ball", "config_revision": 0},
    )

    window._poll_lan_commands()

    rejected = window._lan_command_store.load(command.command_id)
    assert rejected.status is LanCommandStatus.REJECTED
    assert "设置已更新" in rejected.message


def test_desktop_rejects_stop_without_running_task(
    qtbot: QtBot,
    tmp_path,
) -> None:
    from oculidoc.lan_commands import (
        LanCommandStatus,
        LanCommandType,
    )

    window = AdminMainWindow(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        )
    )
    qtbot.addWidget(window)
    command = window._lan_command_store.submit(
        LanCommandType.STOP_TASK,
        payload={"module_id": "tracking_ball"},
    )

    window._poll_lan_commands()

    rejected = window._lan_command_store.load(command.command_id)
    assert rejected.status is LanCommandStatus.REJECTED
    assert "没有匹配的运行中任务" in rejected.message
