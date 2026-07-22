"""Application and persisted eye-tracker configuration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

GazeSource = Literal[
    "auto",
    "gaze_collect_legacy",
    "just_need_to_see_bundle",
    "mock",
    "tobii_stream_engine",
    "tobii_legacy_bridge",
]


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

    gaze_source: GazeSource = "mock"
    tobii_stream_engine_dll: Path | None = None
    gaze_preflight_seconds: int = Field(default=3, ge=3, le=10)
    gaze_minimum_valid_ratio: float = Field(default=0.35, ge=0.0, le=1.0)
    tobii_bridge_mode: Literal[
        "hospital_server",
        "client",
    ] = "hospital_server"
    tobii_bridge_bind_host: str = "0.0.0.0"
    tobii_bridge_host: str = "127.0.0.1"
    tobii_bridge_port: int = Field(
        default=9999,
        ge=1,
        le=65535,
    )
    tobii_screen_width_px: int = Field(
        default=1920,
        ge=320,
        le=16_384,
    )
    tobii_screen_height_px: int = Field(
        default=1080,
        ge=240,
        le=16_384,
    )
    tobii_helper_executable: Path | None = None
    gaze_collect_json_root: Path = Path(r"D:\GazeCollect\HPFData\json")
    gaze_collect_player_executable: Path | None = Path(
        r"D:\GazeCollect\VMMachine\HPFMediaPlayer.exe"
    )
    eye_position_executable: Path | None = Path(
        r"D:\EyePosition\TobiiDynavox.EyeAssist.Smorgasbord.exe"
    )
    just_need_to_see_root: Path = Path(r"D:\JustNeedToSee")

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


class GazeDeviceConfig(BaseModel):
    """The small device configuration edited inside OculiDoC."""

    gaze_source: GazeSource
    tobii_stream_engine_dll: Path | None = None
    tobii_bridge_host: str = "127.0.0.1"
    tobii_bridge_port: int = Field(default=9999, ge=1, le=65535)
    gaze_collect_json_root: Path = Path(r"D:\GazeCollect\HPFData\json")
    gaze_collect_player_executable: Path | None = Path(
        r"D:\GazeCollect\VMMachine\HPFMediaPlayer.exe"
    )
    eye_position_executable: Path | None = Path(
        r"D:\EyePosition\TobiiDynavox.EyeAssist.Smorgasbord.exe"
    )
    just_need_to_see_root: Path = Path(r"D:\JustNeedToSee")
    gaze_preflight_seconds: int = Field(ge=3, le=10)
    gaze_minimum_valid_ratio: float = Field(ge=0.0, le=1.0)

    @classmethod
    def from_settings(cls, settings: Settings) -> GazeDeviceConfig:
        return cls(
            gaze_source=settings.gaze_source,
            tobii_stream_engine_dll=settings.tobii_stream_engine_dll,
            tobii_bridge_host=settings.tobii_bridge_host,
            tobii_bridge_port=settings.tobii_bridge_port,
            gaze_collect_json_root=settings.gaze_collect_json_root,
            gaze_collect_player_executable=settings.gaze_collect_player_executable,
            eye_position_executable=settings.eye_position_executable,
            just_need_to_see_root=settings.just_need_to_see_root,
            gaze_preflight_seconds=settings.gaze_preflight_seconds,
            gaze_minimum_valid_ratio=settings.gaze_minimum_valid_ratio,
        )

    def apply(self, settings: Settings) -> Settings:
        """Return settings with this persisted device selection applied."""
        return settings.model_copy(
            update={
                "gaze_source": self.gaze_source,
                "tobii_stream_engine_dll": self.tobii_stream_engine_dll,
                "tobii_bridge_host": self.tobii_bridge_host,
                "tobii_bridge_port": self.tobii_bridge_port,
                "gaze_collect_json_root": self.gaze_collect_json_root,
                "gaze_collect_player_executable": self.gaze_collect_player_executable,
                "eye_position_executable": self.eye_position_executable,
                "just_need_to_see_root": self.just_need_to_see_root,
                "gaze_preflight_seconds": self.gaze_preflight_seconds,
                "gaze_minimum_valid_ratio": self.gaze_minimum_valid_ratio,
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "gaze_source": self.gaze_source,
            "tobii_stream_engine_dll": (
                str(self.tobii_stream_engine_dll)
                if self.tobii_stream_engine_dll is not None
                else None
            ),
            "tobii_bridge_host": self.tobii_bridge_host,
            "tobii_bridge_port": self.tobii_bridge_port,
            "gaze_collect_json_root": str(self.gaze_collect_json_root),
            "gaze_collect_player_executable": (
                str(self.gaze_collect_player_executable)
                if self.gaze_collect_player_executable is not None
                else None
            ),
            "eye_position_executable": (
                str(self.eye_position_executable)
                if self.eye_position_executable is not None
                else None
            ),
            "just_need_to_see_root": str(self.just_need_to_see_root),
            "gaze_preflight_seconds": self.gaze_preflight_seconds,
            "gaze_minimum_valid_ratio": self.gaze_minimum_valid_ratio,
        }


class GazeDeviceConfigStore:
    """Atomically persist the administrator's eye-tracker selection."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    @classmethod
    def for_settings(cls, settings: Settings) -> GazeDeviceConfigStore:
        return cls(settings.data_dir / "runtime" / "gaze_device_config.json")

    def load(self, default: GazeDeviceConfig) -> GazeDeviceConfig:
        if not self.path.exists():
            return default

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("Saved gaze-device configuration must be an object.")
            value = payload.get("config", payload)
            config = GazeDeviceConfig.model_validate(value)

            # M3D12D lowers the former default threshold for low-validity DoC patients.
            # Preserve every explicitly different administrator setting.
            if config.gaze_minimum_valid_ratio == 0.60:
                config = config.model_copy(update={"gaze_minimum_valid_ratio": 0.35})

            return config
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as error:
            raise ValueError(f"已保存的眼动设备配置无效：{self.path}") from error

    def save(self, config: GazeDeviceConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "config": config.to_dict(),
        }

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")

        temporary_path.replace(self.path)


def apply_saved_gaze_device_config(settings: Settings) -> Settings:
    """Apply the last valid in-app device configuration when present."""
    default = GazeDeviceConfig.from_settings(settings)
    return GazeDeviceConfigStore.for_settings(settings).load(default).apply(settings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
