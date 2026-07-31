from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_pins_audited_qt_for_python_version() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "PySide6==6.11.1" in metadata["project"]["dependencies"]
    assert metadata["project"]["optional-dependencies"]["build"] == [
        "pyinstaller==6.21.0",
        "pip-licenses==5.5.5",
    ]


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
    assert '"PySide6.QtWebEngineCore"' in source
    assert '"PySide6.QtWebEngineWidgets"' in source
    assert '"PySide6.QtGraphs"' in source
    assert '"PySide6.QtHttpServer"' in source
    assert '"PySide6.QtNetworkAuth"' in source
    assert '"PySide6.QtQuick3D"' in source
    assert '"PySide6.QtQuickTimeline"' in source
    assert '"PySide6.QtVirtualKeyboard"' in source


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
    assert "*WebEngine*" in source
    assert "Qt6CanvasPainter*" in source
    assert "Qt6Graphs*" in source
    assert "Qt6HttpServer*" in source
    assert "Qt6NetworkAuth*" in source
    assert "Qt6Quick3D*" in source
    assert "Qt6QuickTimeline*" in source
    assert "Qt6VirtualKeyboard*" in source
    assert "remainingForbiddenQtPayload.Count -ne 0" in source
    assert "piplicenses" in source
    assert "THIRD_PARTY_LICENSES.json" in source
    assert 'pythonRuntimeVersion -ne "3.11.9"' in source
    assert "Python-3.11.9-LICENSE.txt" in source
    assert "$manualLicenses.Count -lt 40" in source
    assert source.index("remainingForbiddenQtPayload.Count -ne 0") < source.index("--package-smoke")


def test_build_outputs_are_ignored() -> None:
    source = (ROOT / ".gitignore").read_text(
        encoding="utf-8",
    )

    assert "build/pyinstaller/" in source
    assert "dist/windows/" in source


def test_inno_installer_supports_online_offline_upgrade_and_shortcuts() -> None:
    source = (ROOT / "packaging" / "windows" / "OculiDoC.iss").read_text(encoding="utf-8")

    assert "AppId={{0D948729-9AE7-43F4-99E7-4C2A156C970A}" in source
    assert "UsePreviousAppDir=yes" in source
    assert "在线安装最新版本" in source
    assert "离线安装当前版本" in source
    assert "DownloadTemporaryFile(" in source
    assert "GetSHA256OfFile(" in source
    assert "HasCommandLineParam('/OFFLINE')" in source
    assert r"{autodesktop}\OculiDoC" in source
    assert r"{autoprograms}\OculiDoC" in source
    assert r"LicenseFile={#SourceDir}\LICENSE-v0.1.1.txt" in source
    assert "仅限非临床工程评估" in source


def test_release_packager_builds_standard_installer() -> None:
    source = (ROOT / "scripts" / "package_windows_release.ps1").read_text(encoding="utf-8")

    assert "Inno Setup 6" in source
    assert "ISCC.exe" in source
    assert "OculiDoC-Setup.exe" in source
    assert '"$setupPath.sha256"' in source
    assert "LICENSE-v0.1.1.txt" in source
    assert "THIRD_PARTY_NOTICES.md" in source
    assert "THIRD_PARTY_LICENSES.json" in source
    assert "QT_SOURCE_OFFER.md" in source
    assert "complete_license_text_count" in source
    assert "third_party_license_record_count" in source
    assert source.index("$requiredBundleDocuments") < source.index("Compress-Archive")


def test_report_summary_uses_system_browser_without_qt_webengine() -> None:
    source = (ROOT / "src" / "oculidoc" / "ui" / "session_history.py").read_text(encoding="utf-8")

    assert "QDesktopServices.openUrl" in source
    assert "report_summary.html" in source
    assert "QWebEngine" not in source
    assert "PySide6.QtWebEngine" not in source


def test_release_license_material_is_version_scoped_and_complete() -> None:
    license_text = (ROOT / "LICENSE-v0.1.1.txt").read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    qt_offer = (ROOT / "QT_SOURCE_OFFER.md").read_text(encoding="utf-8")
    license_root = ROOT / "packaging" / "windows" / "licenses"
    normalized_license = " ".join(license_text.split())

    assert "official OculiDoC v0.1.1" in license_text
    assert "solely for non-clinical engineering evaluation" in normalized_license
    assert "any real patient" in license_text
    assert 'PROVIDED "AS IS"' in license_text
    assert "PySide6 / Qt 6.11" in notices
    assert "Qt WebEngine/Chromium" in notices
    assert "LGPL-3.0" in notices
    assert "reverse engineering" in qt_offer
    assert (license_root / "LGPL-3.0-only.txt").is_file()
    assert (license_root / "GPL-3.0-only.txt").is_file()
    qt_license_root = license_root / "qt-6.11.1"
    assert len(tuple(qt_license_root.glob("*.txt"))) >= 39
    provenance = (qt_license_root / "README.txt").read_text(encoding="utf-8")
    assert "59c81a3c2247b821b9b84b4eb8d939b77e07e276" in provenance
    assert "73fb12a067c2e8f7a464a310aaee2860fa2b64d2" in provenance
    python_license_root = license_root / "python-3.11.9"
    assert (python_license_root / "LICENSE.txt").is_file()
    python_provenance = (python_license_root / "README.txt").read_text(encoding="utf-8")
    assert "de54cf5be371a6f5e2e9f208c38def5f81d3ef02" in python_provenance


def test_windows_signing_inventory_is_read_only_and_covers_all_candidates() -> None:
    source = (ROOT / "scripts" / "inventory_windows_signing.ps1").read_text(encoding="utf-8")

    assert "Get-ChildItem $bundleRootPath -Recurse -File" in source
    assert '@(".exe", ".dll", ".pyd", ".ps1")' in source
    assert "OculiDoC_bundle_signing_inventory.json" in source
    assert 'GetExtension($outputFullPath).ToLowerInvariant() -ne ".json"' in source
    assert "[System.StringComparison]::OrdinalIgnoreCase" in source
    assert "Get-AuthenticodeSignature" in source
    assert "Get-FileHash" in source
    assert "relative_path" in source
    assert "extension = $candidate.Extension.ToLowerInvariant()" in source
    assert "counts_by_extension" in source
    assert "WINDOWS_SIGNING_${extensionLabel}_NEEDS_RSA=" in source
    assert "signer_subject" in source
    assert "signer_thumbprint" in source
    assert "public_key_algorithm" in source
    assert "valid_rsa_signatures" in source
    assert "needs_rsa_trusted_signature" in source
    assert "WINDOWS_SIGNING_INVENTORY_PATH=" in source
    assert source.count('"WINDOWS_SIGNING_INVENTORY=') == 1
    assert "Set-AuthenticodeSignature" not in source
    assert "signtool" not in source.lower()


def test_inno_translation_retains_upstream_notice() -> None:
    translation = (
        ROOT / "packaging" / "windows" / "languages" / "ChineseSimplified.isl"
    ).read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "Maintainer: Zhenghan Yang (Kira)" in translation
    assert "Inno Setup License" in notices
    assert "packaging/windows/languages/ChineseSimplified.isl" in notices
