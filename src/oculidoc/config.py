"""Application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_prefix="OCULIDOC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OculiDoC"
    collaborator_name: str = "TiantanDoC"
    environment: Literal[
        "development",
        "test",
        "production",
    ] = "development"

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".oculidoc" / "data")

    admin_host: str = "127.0.0.1"
    admin_port: int = Field(default=8000, ge=1, le=65535)

    gaze_source: Literal[
        "mock",
        "tobii_legacy_bridge",
    ] = "mock"
    tobii_bridge_host: str = "127.0.0.1"
    tobii_bridge_port: int = Field(
        default=9999,
        ge=1,
        le=65535,
    )

    @property
    def database_path(self) -> Path:
        """Return the resolved SQLite database path."""
        return (self.data_dir.expanduser() / "oculidoc.sqlite3").resolve()

    @property
    def database_url(self) -> str:
        """Return the SQLAlchemy-compatible SQLite URL."""
        return f"sqlite+pysqlite:///{self.database_path.as_posix()}"

    @property
    def admin_base_url(self) -> str:
        """Return the local administrator API base URL."""
        return f"http://{self.admin_host}:{self.admin_port}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
