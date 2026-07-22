import json
from pathlib import Path

import pytest

from oculidoc.config import (
    GazeDeviceConfig,
    GazeDeviceConfigStore,
    Settings,
    apply_saved_gaze_device_config,
)


def test_default_settings_are_local() -> None:
    settings = Settings(environment="test")
    assert settings.admin_host == "127.0.0.1"
    assert settings.admin_port == 8000
    assert settings.tobii_bridge_host == "127.0.0.1"
    assert settings.tobii_bridge_port == 9999
    assert settings.gaze_source == "mock"
    assert settings.gaze_preflight_seconds == 3
    assert settings.gaze_minimum_valid_ratio == 0.35


def test_database_url_uses_data_directory(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    assert settings.database_url.startswith("sqlite+pysqlite:///")
    assert settings.database_url.endswith("oculidoc.sqlite3")


def test_saved_gaze_device_config_overrides_next_launch(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, gaze_source="mock")
    store = GazeDeviceConfigStore.for_settings(settings)
    dll_path = tmp_path / "tobii_stream_engine.dll"
    gaze_collect_root = tmp_path / "gaze-json"
    hpf_player = tmp_path / "HPFMediaPlayer.exe"
    eye_position = tmp_path / "EyePosition.exe"
    just_need_to_see_root = tmp_path / "JustNeedToSee"
    store.save(
        GazeDeviceConfig(
            gaze_source="auto",
            tobii_stream_engine_dll=dll_path,
            tobii_bridge_host="192.168.10.5",
            tobii_bridge_port=8765,
            gaze_collect_json_root=gaze_collect_root,
            gaze_collect_player_executable=hpf_player,
            eye_position_executable=eye_position,
            just_need_to_see_root=just_need_to_see_root,
            gaze_preflight_seconds=7,
            gaze_minimum_valid_ratio=0.75,
        )
    )

    applied = apply_saved_gaze_device_config(settings)

    assert applied.gaze_source == "auto"
    assert applied.tobii_stream_engine_dll == dll_path
    assert applied.tobii_bridge_host == "192.168.10.5"
    assert applied.tobii_bridge_port == 8765
    assert applied.gaze_collect_json_root == gaze_collect_root
    assert applied.gaze_collect_player_executable == hpf_player
    assert applied.eye_position_executable == eye_position
    assert applied.just_need_to_see_root == just_need_to_see_root
    assert applied.gaze_preflight_seconds == 7
    assert applied.gaze_minimum_valid_ratio == 0.75
    assert not list((tmp_path / "runtime").glob(".gaze_device_config.json.*.tmp"))


def test_invalid_saved_gaze_config_never_falls_back_to_mock(tmp_path: Path) -> None:
    settings = Settings(environment="production", data_dir=tmp_path, gaze_source="mock")
    store = GazeDeviceConfigStore.for_settings(settings)
    store.path.parent.mkdir(parents=True)
    store.path.write_text('{"config":{"gaze_source":"broken"}}', encoding="utf-8")

    with pytest.raises(ValueError, match="眼动设备配置无效"):
        apply_saved_gaze_device_config(settings)


def test_saved_pre_compatibility_config_uses_new_path_defaults(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    store = GazeDeviceConfigStore.for_settings(settings)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "config": {
                    "gaze_source": "mock",
                    "tobii_stream_engine_dll": None,
                    "tobii_bridge_host": "127.0.0.1",
                    "tobii_bridge_port": 9999,
                    "gaze_preflight_seconds": 3,
                    "gaze_minimum_valid_ratio": 0.35,
                },
            }
        ),
        encoding="utf-8",
    )

    applied = apply_saved_gaze_device_config(settings)

    assert applied.gaze_collect_json_root == Path(r"D:\GazeCollect\HPFData\json")
    assert applied.just_need_to_see_root == Path(r"D:\JustNeedToSee")


def test_saved_former_default_validity_threshold_is_migrated(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    store = GazeDeviceConfigStore.for_settings(settings)
    store.save(
        GazeDeviceConfig(
            gaze_source="mock",
            gaze_preflight_seconds=3,
            gaze_minimum_valid_ratio=0.60,
        )
    )

    assert apply_saved_gaze_device_config(settings).gaze_minimum_valid_ratio == 0.35
