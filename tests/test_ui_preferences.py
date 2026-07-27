from pathlib import Path

import pytest

from oculidoc.config import (
    AdminUiMode,
    AdminUiPreferences,
    AdminUiPreferencesStore,
    Settings,
)


def test_admin_ui_preferences_default_to_clinical_workbench(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path)
    store = AdminUiPreferencesStore.for_settings(settings)

    assert store.load().mode is AdminUiMode.CLINICAL_WORKBENCH


def test_admin_ui_preferences_round_trip_classic_atomically(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "admin_ui_preferences.json"
    store = AdminUiPreferencesStore(path)
    store.save(AdminUiPreferences(mode=AdminUiMode.CLASSIC))

    assert store.load().mode is AdminUiMode.CLASSIC
    assert not list(path.parent.glob(".admin_ui_preferences.json.*.tmp"))


def test_invalid_admin_ui_preferences_do_not_fall_back_silently(
    tmp_path: Path,
) -> None:
    path = tmp_path / "admin_ui_preferences.json"
    path.write_text(
        '{"schema_version":"1.0","preferences":{"mode":"unknown"}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="管理端界面设置无效"):
        AdminUiPreferencesStore(path).load()
