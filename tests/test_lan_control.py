from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from oculidoc.api.app import create_api
from oculidoc.config import Settings
from oculidoc.lan_control import (
    DEFAULT_IDLE_TEXT,
    LanControlStateStore,
    build_control_url,
)


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
    assert reloaded.mode == "message"

    idle = store.reset_idle()
    assert idle.revision == 2
    assert idle.text == DEFAULT_IDLE_TEXT


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

    idle = client.post(
        "/api/v1/patient-display/idle",
        params={"token": "secret-pairing-token"},
        json={},
    )
    assert idle.status_code == 200
    assert store.load().mode == "idle"


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
        },
    )
    assert started.status_code == 200

    unsupported = client.post(
        "/api/v1/commands",
        params=parameters,
        json={
            "command_type": "start_task",
            "module_id": "screen_keyboard",
        },
    )
    assert unsupported.status_code == 422

    listed = client.get(
        "/api/v1/commands",
        params=parameters,
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 2
