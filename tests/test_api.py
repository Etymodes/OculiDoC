from pathlib import Path

from fastapi.testclient import TestClient

from oculidoc.api.app import create_api
from oculidoc.config import GazeDeviceConfig, GazeDeviceConfigStore, Settings


def test_health_endpoint(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)

    with TestClient(create_api(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["application"] == "OculiDoC"
    assert payload["status"] == "ok"
    assert payload["environment"] == "test"
    assert payload["gaze_source"] == "mock"
    assert payload["collaborator"] == "TiantanDoC"


def test_health_reads_latest_saved_gaze_source(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="mock")
    api = create_api(settings)
    GazeDeviceConfigStore.for_settings(settings).save(
        GazeDeviceConfig(
            gaze_source="tobii_stream_engine",
            gaze_preflight_seconds=3,
            gaze_minimum_valid_ratio=0.60,
        )
    )

    with TestClient(api) as client:
        response = client.get("/health")

    assert response.json()["gaze_source"] == "tobii_stream_engine"
