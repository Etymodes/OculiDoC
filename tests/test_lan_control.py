from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oculidoc.api.app import create_api
from oculidoc.config import Settings
from oculidoc.lan_control import (
    DEFAULT_IDLE_TEXT,
    LanControlStateStore,
    LanControlTransitionError,
    PatientDisplayMode,
    build_control_url,
)
from oculidoc.task_configs import TaskConfigStore


def test_lan_control_state_round_trip(
    tmp_path: Path,
) -> None:
    store = LanControlStateStore(tmp_path / "state.json")

    assert store.load().text == DEFAULT_IDLE_TEXT

    message = store.set_display(
        "请看向屏幕中央",
        mode="message",
    )
    assert message.revision == 1

    reloaded = store.load()
    assert reloaded.text == "请看向屏幕中央"
    assert reloaded.mode is PatientDisplayMode.PREVIEW

    idle = store.reset_idle()
    assert idle.revision == 2
    assert idle.text == DEFAULT_IDLE_TEXT


def test_patient_display_state_machine_and_countdown(tmp_path: Path) -> None:
    store = LanControlStateStore(tmp_path / "state.json")
    ready = store.set_display(
        "追踪球即将开始",
        mode=PatientDisplayMode.READY,
        task_id="tracking_ball",
        countdown_seconds=3,
    )
    assert ready.countdown_seconds == 3

    running = store.set_display(
        "正在进行追踪球",
        mode=PatientDisplayMode.RUNNING,
        task_id="tracking_ball",
    )
    assert running.mode is PatientDisplayMode.RUNNING

    with pytest.raises(LanControlTransitionError, match="running -> preview"):
        store.set_display("不应覆盖运行中任务", mode=PatientDisplayMode.PREVIEW)

    result = store.set_display(
        "任务已结束",
        mode=PatientDisplayMode.RESULT,
        task_id="tracking_ball",
    )
    assert result.mode is PatientDisplayMode.RESULT
    assert store.reset_idle().mode is PatientDisplayMode.IDLE
    assert store.set_closed().mode is PatientDisplayMode.CLOSED
    assert store.reset_idle().mode is PatientDisplayMode.IDLE


def test_legacy_message_mode_loads_as_preview(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        '{"schema_version":"1.0","state":{"revision":4,"mode":"message",'
        '"text":"旧版投屏文字","task_id":null,"updated_at_utc":"2026-07-20T00:00:00Z"}}',
        encoding="utf-8",
    )

    assert LanControlStateStore(path).load().mode is PatientDisplayMode.PREVIEW


def test_control_url_contains_pairing_token() -> None:
    url = build_control_url(
        "192.168.1.20",
        8000,
        "pairing-token",
    )

    assert url == ("http://192.168.1.20:8000/control?token=pairing-token")


def test_mobile_api_controls_patient_display(
    tmp_path: Path,
) -> None:
    store = LanControlStateStore(tmp_path / "state.json")
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        gaze_source="mock",
    )
    api = create_api(
        settings,
        token="secret-pairing-token",
        state_store=store,
    )
    client = TestClient(api)

    unauthorized = client.get(
        "/api/v1/runtime",
        params={"token": "wrong-token"},
    )
    assert unauthorized.status_code == 401

    control_page = client.get(
        "/control",
        params={"token": "secret-pairing-token"},
    )
    assert control_page.status_code == 200
    assert "OculiDoC 手机管理员端" in control_page.text
    assert "保存并直接启动" in control_page.text

    sent = client.post(
        "/api/v1/patient-display/text",
        params={"token": "secret-pairing-token"},
        json={"text": "请尝试睁眼"},
    )
    assert sent.status_code == 200
    assert store.load().text == "请尝试睁眼"

    preview = client.post(
        "/api/v1/tasks/preview",
        params={"token": "secret-pairing-token"},
        json={"module_id": "tracking_ball"},
    )
    assert preview.status_code == 200
    assert store.load().mode == "preview"
    assert store.load().task_id == "tracking_ball"

    runtime = client.get(
        "/api/v1/runtime",
        params={"token": "secret-pairing-token"},
    )
    assert runtime.status_code == 200
    assert runtime.json()["gaze_source"] == "mock"
    assert runtime.json()["patient_display"]["mode"] == "preview"
    assert len(runtime.json()["question_bank"]) >= 66
    assert {asset["category"] for asset in runtime.json()["image_library"]} >= {
        "水果",
        "动物",
    }

    idle = client.post(
        "/api/v1/patient-display/idle",
        params={"token": "secret-pairing-token"},
        json={},
    )
    assert idle.status_code == 200
    assert store.load().mode == "idle"

    store.set_display(
        "准备开始",
        mode=PatientDisplayMode.READY,
        task_id="tracking_ball",
        countdown_seconds=1,
    )
    store.set_display(
        "任务进行中",
        mode=PatientDisplayMode.RUNNING,
        task_id="tracking_ball",
    )
    blocked = client.post(
        "/api/v1/patient-display/text",
        params={"token": "secret-pairing-token"},
        json={"text": "不应覆盖任务"},
    )
    assert blocked.status_code == 409

    blocked_idle = client.post(
        "/api/v1/patient-display/idle",
        params={"token": "secret-pairing-token"},
        json={},
    )
    assert blocked_idle.status_code == 409


def test_mobile_api_submits_desktop_commands(
    tmp_path: Path,
) -> None:
    from oculidoc.lan_commands import (
        LanCommandStatus,
        LanCommandStore,
    )

    command_store = LanCommandStore(tmp_path / "commands")
    api = create_api(
        Settings(
            environment="test",
            data_dir=tmp_path,
            gaze_source="mock",
        ),
        token="secret-pairing-token",
        command_store=command_store,
    )
    client = TestClient(api)
    parameters = {"token": "secret-pairing-token"}

    opened = client.post(
        "/api/v1/commands",
        params=parameters,
        json={"command_type": "open_patient_display"},
    )
    assert opened.status_code == 200
    assert opened.json()["status"] == LanCommandStatus.PENDING.value

    started = client.post(
        "/api/v1/commands",
        params=parameters,
        json={
            "command_type": "start_task",
            "module_id": "tracking_ball",
            "config_revision": 0,
        },
    )
    assert started.status_code == 200
    assert started.json()["payload"]["config_revision"] == 0

    keyboard = client.post(
        "/api/v1/commands",
        params=parameters,
        json={
            "command_type": "start_task",
            "module_id": "screen_keyboard",
            "config_revision": 0,
        },
    )
    assert keyboard.status_code == 200

    vertical = client.post(
        "/api/v1/commands",
        params=parameters,
        json={
            "command_type": "start_task",
            "module_id": "binary_vertical",
            "config_revision": 0,
        },
    )
    assert vertical.status_code == 200

    multiple = client.post(
        "/api/v1/commands",
        params=parameters,
        json={
            "command_type": "start_task",
            "module_id": "multiple_choice",
            "config_revision": 0,
        },
    )
    assert multiple.status_code == 200

    replayed = client.post(
        "/api/v1/commands",
        params=parameters,
        json={"command_type": "replay_speech"},
    )
    assert replayed.status_code == 200

    listed = client.get(
        "/api/v1/commands",
        params=parameters,
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 6


def test_mobile_api_synchronizes_versioned_task_configs(tmp_path: Path) -> None:
    task_configs = TaskConfigStore(tmp_path / "task_configs.json")
    api = create_api(
        Settings(environment="test", data_dir=tmp_path, gaze_source="mock"),
        token="secret-pairing-token",
        task_config_store=task_configs,
    )
    client = TestClient(api)
    parameters = {"token": "secret-pairing-token"}

    loaded = client.get("/api/v1/task-configs/tracking_ball", params=parameters)
    assert loaded.status_code == 200
    assert loaded.json()["revision"] == 0

    config = loaded.json()["config"]
    config["diameter_px"] = 160
    saved = client.put(
        "/api/v1/task-configs/tracking_ball",
        params=parameters,
        json={"revision": 0, "config": config},
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["config"]["diameter_px"] == 160

    conflict = client.put(
        "/api/v1/task-configs/tracking_ball",
        params=parameters,
        json={"revision": 0, "config": config},
    )
    assert conflict.status_code == 409
    assert conflict.json() == saved.json()

    runtime = client.get("/api/v1/runtime", params=parameters)
    modules = {item["module_id"]: item for item in runtime.json()["modules"]}
    assert modules["tracking_ball"]["config_revision"] == 1
