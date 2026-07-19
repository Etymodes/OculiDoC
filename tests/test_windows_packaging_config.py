from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyinstaller_spec_has_branding_and_onedir() -> None:
    path = ROOT / "packaging" / "windows" / "OculiDoC.spec"
    source = path.read_text(
        encoding="utf-8",
    )

    compile(
        source,
        str(path),
        "exec",
    )
    tree = ast.parse(
        source,
        filename=str(path),
    )

    assert tree.body
    assert "icon=str(ICON_PATH)" in source
    assert "version=str(VERSION_FILE)" in source
    assert "console=False" in source
    assert "COLLECT(" in source
    assert '"assets/*.ico"' in source
    assert '"assets/*.png"' in source
    assert "collect_submodules(" in source
    assert '"oculidoc.package_smoke"' in source


def test_windows_build_script_exposes_validation_and_smoke() -> None:
    path = ROOT / "scripts" / "build_windows.ps1"
    source = path.read_text(
        encoding="utf-8",
    )

    assert "[switch]$InstallDependencies" in source
    assert "[switch]$ValidateOnly" in source
    assert "WINDOWS_BUILD_CONFIG_VALID=PASS" in source
    assert "WINDOWS_EXE_BUILD_VERIFIED=PASS" in source
    assert "--package-smoke" in source
    assert "ExtractAssociatedIcon" in source
    assert "OculiDoC_build_verification.json" in source


def test_build_outputs_are_ignored() -> None:
    source = (ROOT / ".gitignore").read_text(
        encoding="utf-8",
    )

    assert "build/pyinstaller/" in source
    assert "dist/windows/" in source
