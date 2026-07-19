from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch

import oculidoc.__main__ as main_module
import oculidoc.package_smoke as smoke_module
from oculidoc.package_smoke import (
    build_package_smoke_report,
    write_package_smoke_report,
)


def test_package_smoke_loads_brand_assets(
    qapp: object,
) -> None:
    del qapp

    report = build_package_smoke_report()

    assert report["ok"] is True
    assert report["checks"] == {
        "all_assets_present": True,
        "application_icon_loaded": True,
        "blue_mark_loaded": True,
        "white_mark_loaded": True,
        "wordmark_loaded": True,
        "qt_application_named": True,
    }


def test_package_smoke_report_is_written(
    qapp: object,
    tmp_path: Path,
) -> None:
    del qapp
    output = tmp_path / "smoke.json"

    exit_code = write_package_smoke_report(output)

    assert exit_code == 0
    payload = json.loads(
        output.read_text(
            encoding="utf-8",
        )
    )
    assert payload["ok"] is True


def test_dispatch_forwards_package_smoke_path(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "frozen.json"
    received: list[Path] = []

    monkeypatch.setattr(
        smoke_module,
        "write_package_smoke_report",
        lambda path: received.append(path) or 29,
    )

    result = main_module.dispatch(
        [
            "--package-smoke",
            str(output),
        ]
    )

    assert result == 29
    assert received == [
        output,
    ]


def test_dispatch_rejects_missing_smoke_path() -> None:
    with pytest.raises(
        SystemExit,
        match="requires one output path",
    ):
        main_module.dispatch(
            [
                "--package-smoke",
            ]
        )
