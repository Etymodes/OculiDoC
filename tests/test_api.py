from pathlib import Path

from fastapi.testclient import TestClient

from oculidoc.api.app import create_api
from oculidoc.config import Settings


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
