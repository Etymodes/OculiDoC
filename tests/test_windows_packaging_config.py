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
    assert '"assets/stimuli/*.json"' in source
    assert '"assets/stimuli/*.png"' in source
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


def test_inno_installer_supports_online_offline_upgrade_and_shortcuts() -> None:
    source = (
        ROOT / "packaging" / "windows" / "OculiDoC.iss"
    ).read_text(encoding="utf-8")

    assert "AppId={{0D948729-9AE7-43F4-99E7-4C2A156C970A}" in source
    assert "UsePreviousAppDir=yes" in source
    assert "在线安装最新版本" in source
    assert "离线安装当前版本" in source
    assert "DownloadTemporaryFile(" in source
    assert "GetSHA256OfFile(" in source
    assert "HasCommandLineParam('/OFFLINE')" in source
    assert r"{autodesktop}\OculiDoC" in source
    assert r"{autoprograms}\OculiDoC" in source


def test_release_packager_builds_standard_installer() -> None:
    source = (
        ROOT / "scripts" / "package_windows_release.ps1"
    ).read_text(encoding="utf-8")

    assert "Inno Setup 6" in source
    assert "ISCC.exe" in source
    assert "OculiDoC-Setup.exe" in source
    assert '"$setupPath.sha256"' in source
