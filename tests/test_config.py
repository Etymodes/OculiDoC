from pathlib import Path

from oculidoc.config import Settings


def test_default_settings_are_local() -> None:
    settings = Settings(environment="test")
    assert settings.admin_host == "127.0.0.1"
    assert settings.admin_port == 8000
    assert settings.tobii_bridge_host == "127.0.0.1"
    assert settings.tobii_bridge_port == 9999
    assert settings.gaze_source == "mock"


def test_database_url_uses_data_directory(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    assert settings.database_url.startswith("sqlite+pysqlite:///")
    assert settings.database_url.endswith("oculidoc.sqlite3")
