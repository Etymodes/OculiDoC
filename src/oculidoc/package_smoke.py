from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from oculidoc.app import create_qt_application
from oculidoc.branding import (
    application_icon,
    brand_asset_path,
    brand_mark_pixmap,
    brand_wordmark_pixmap,
)

_BRAND_ASSETS = (
    "app_icon.ico",
    "app_icon.png",
    "brand_mark_blue.png",
    "brand_mark_white.png",
    "brand_wordmark_blue.png",
)


def build_package_smoke_report() -> dict[str, object]:
    """Verify packaged assets and basic Qt image loading."""

    app = create_qt_application([])
    assets: dict[
        str,
        dict[str, object],
    ] = {}

    for name in _BRAND_ASSETS:
        path = brand_asset_path(name)
        assets[name] = {
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": (path.stat().st_size if path.is_file() else None),
        }

    icon = application_icon()
    blue_mark = brand_mark_pixmap(
        variant="blue",
        max_width=160,
        max_height=100,
    )
    white_mark = brand_mark_pixmap(
        variant="white",
        max_width=160,
        max_height=100,
    )
    wordmark = brand_wordmark_pixmap(
        max_width=480,
        max_height=440,
    )

    checks = {
        "all_assets_present": all(
            record["exists"]
            and isinstance(
                record["size_bytes"],
                int,
            )
            and record["size_bytes"] > 0
            for record in assets.values()
        ),
        "application_icon_loaded": (not icon.isNull()),
        "blue_mark_loaded": (not blue_mark.isNull()),
        "white_mark_loaded": (not white_mark.isNull()),
        "wordmark_loaded": (not wordmark.isNull()),
        "qt_application_named": (app.applicationName() == "OculiDoC"),
    }

    return {
        "schema_version": "1.0",
        "generated_at_utc": (datetime.now(UTC).isoformat()),
        "ok": all(checks.values()),
        "frozen": bool(
            getattr(
                sys,
                "frozen",
                False,
            )
        ),
        "executable": sys.executable,
        "platform": sys.platform,
        "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
        "checks": checks,
        "assets": assets,
    }


def _atomic_write_json(
    path: Path,
    payload: dict[str, object],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(
        temporary_path,
        path,
    )


def write_package_smoke_report(
    output_path: Path,
) -> int:
    """Write a machine-readable frozen-bundle smoke report."""

    path = Path(output_path).expanduser().resolve()

    try:
        payload = build_package_smoke_report()
    except Exception as error:
        payload = {
            "schema_version": "1.0",
            "generated_at_utc": (datetime.now(UTC).isoformat()),
            "ok": False,
            "frozen": bool(
                getattr(
                    sys,
                    "frozen",
                    False,
                )
            ),
            "executable": sys.executable,
            "platform": sys.platform,
            "error_type": (type(error).__name__),
            "error": str(error),
        }

    _atomic_write_json(
        path,
        payload,
    )
    return 0 if payload.get("ok") is True else 1
