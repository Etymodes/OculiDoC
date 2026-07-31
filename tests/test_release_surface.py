from __future__ import annotations

import codecs
import tomllib
from pathlib import Path

import oculidoc

ROOT = Path(__file__).resolve().parents[1]


def test_release_uses_one_consistent_version() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == "0.1.1"
    assert oculidoc.__version__ == "0.1.1"


def test_public_readme_has_evaluation_license_and_support_path() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "面向意识障碍患者的眼动仪操作界面与实验数据台" in readme
    assert "公众可下载、安装和运行官方 v0.1.1" in readme
    assert "76 张刺激图和品牌资源" in readme
    assert "仅限非临床工程评估" in readme
    assert "不代表医院或科室的官方发布" in readme
    assert "不得用于任何\n真实患者或临床用途" in readme
    assert "LICENSE-v0.1.1.txt" in readme
    assert "GitHub Issues" in readme
    assert "mailto:" not in readme
    assert "首都医科大学天坛医院意识障碍病区所有" not in readme
    assert "feature/gaze-tasks-mvp" not in readme
    assert "M3D13" not in readme
    assert "ὀποῖν θέσις" in readme
    assert "open thesis" in readme
    assert "TOBII_OFFICIAL_INTEGRATION.md" in readme
    assert "校准显示器与全屏任务所在显示器为同一固定显示器" in readme


def test_notice_limits_public_permission_to_v011_nonclinical_evaluation() -> None:
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")

    assert "`v0.1.1` 标签及其官方 GitHub Release" in notice
    assert "76 张刺激图和品牌资源" in notice
    assert "安装和运行 OculiDoC v0.1.1" in notice
    assert "仅限非临床工程评估" in notice
    assert "不授予通用开源许可" in notice
    assert "任何真实患者或临床用途" in notice
    assert "拆出、独立复用、改作或再分发刺激图及品牌资源" in notice
    assert "本版本不是医院或科室的官方发行" in notice
    assert "mailto:" not in notice
    assert "首都医科大学天坛医院意识障碍病区所有" not in notice


def test_repository_workflow_uses_current_github_repo_as_source_of_truth() -> None:
    workflow = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "Etymodes/OculiDoC" in workflow
    assert "不得为了“重新理解项目”而反复解压、扫描或比较" in workflow
    assert "医院眼动仪.zip" in workflow


def test_tobii_integrations_remain_external_and_optional() -> None:
    policy = (ROOT / "TOBII_OFFICIAL_INTEGRATION.md").read_text(encoding="utf-8")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependency_text = "\n".join(
        metadata["project"]["dependencies"]
        + [
            dependency
            for group in metadata["project"]["optional-dependencies"].values()
            for dependency in group
        ]
    )

    assert "Tobii Pro SDK" in policy
    assert "Tobii Pro Glasses 3 API" in policy
    assert "普通 Eye Tracker 5 是纯游戏设备，不能用于开发" in policy
    assert "tobii_research" not in dependency_text


def test_public_release_excludes_internal_development_documents() -> None:
    documents = ROOT / "docs"
    public_files = tuple(path for path in documents.rglob("*") if path.is_file())

    assert public_files == ()


def test_windows_maintenance_scripts_use_repository_venv() -> None:
    scripts = (
        "install.ps1",
        "run_app.ps1",
        "run_api.ps1",
        "check.ps1",
        "update.ps1",
    )

    for name in scripts:
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert ".venv" in text
        assert r"Envs\ops" not in text

    assert (ROOT / "scripts" / "install_portable.ps1").is_file()
    assert (ROOT / "scripts" / "package_windows_release.ps1").is_file()


def test_powershell_scripts_are_utf8_bom_for_windows_powershell_51() -> None:
    scripts = sorted((ROOT / "scripts").glob("*.ps1"))

    assert scripts
    for script in scripts:
        data = script.read_bytes()
        assert data.startswith(codecs.BOM_UTF8), script
        data[len(codecs.BOM_UTF8) :].decode("utf-8")


def test_ci_inventories_candidate_and_v0_release_has_lightweight_provenance() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "windows-package:" in ci
    assert "Validate Windows PowerShell 5.1 scripts" in ci
    assert "shell: powershell" in ci
    assert 'PSEdition -ne "Desktop"' in ci
    assert "PSVersion.Major -ne 5" in ci
    assert "$bytes[0] -ne 0xEF" in ci
    assert "System.Management.Automation.Language.Parser" in ci
    assert "package_windows_release.ps1" in ci
    assert "inventory_windows_signing.ps1" in ci
    assert ci.index("build_windows.ps1") < ci.index("package_windows_release.ps1")
    assert ci.index("package_windows_release.ps1") < ci.index("inventory_windows_signing.ps1")
    assert ci.count("inventory_windows_signing.ps1") == 1
    assert "-BundleRoot ./dist/windows/OculiDoC" in ci
    assert "OculiDoC_bundle_signing_inventory.json" in ci
    assert "check.ps1 -PythonCommand python" in ci
    assert ci.count('python-version: "3.11.9"') == 2
    assert "permissions:\n  contents: read" in ci
    assert 'tags:\n      - "v*"' in release
    assert "permissions:\n  contents: write" in release
    assert "id-token: write" in release
    assert "attestations: write" in release
    assert "gh release create" in release
    assert "package_windows_release.ps1" in release
    assert "choco install innosetup" in release
    assert "check.ps1 -PythonCommand python" in release
    assert "TRUSTED_SIGNING_PROVIDER_REQUIRED_FOR_V1" in release
    assert "TRUSTED_SIGNING_PROVIDER_PENDING" not in release
    assert "([version]$version).Major -ge 1" in release
    assert "RELEASE_CHANNEL=pre-1.0-lightweight" in release
    assert 'python-version: "3.11.9"' in release
    assert "Prepare clean release environment" in release
    assert 'pip install -e ".[research,build]"' in release
    assert "./.release-venv/Scripts/python.exe" in release
    assert "actions/attest@v4" in release
    assert "subject-path: dist/release/*" in release
    assert "gh attestation verify" in release
    assert "OculiDoC_bundle_signing_inventory.json" in release
    assert "--notes-file RELEASE_NOTES.md" in release
    assert "--verify-tag" in release
    assert "--latest" in release
    assert "--prerelease" not in release
    assert "--clobber" not in release
    assert "gh release upload" not in release
    assert "already exists; publish a new patch version instead" in release
    gate = release.index("TRUSTED_SIGNING_PROVIDER_REQUIRED_FOR_V1")
    build = release.index("Build and verify release")
    inventory = release.index("inventory_windows_signing.ps1")
    attest = release.index("actions/attest@v4")
    upload_artifact = release.index("actions/upload-artifact@v4")
    publish = release.index("gh release create")
    assert gate < release.index("Install project")
    assert build < inventory < attest < upload_artifact < publish


def test_release_notes_disclose_pre_1_0_trust_boundary() -> None:
    notes = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")

    assert "v0.x" in notes
    assert "SHA-256" in notes
    assert "Sigstore" in notes
    assert "Authenticode" in notes
    assert "v1.0" in notes
    assert "不要关闭 Windows 安全策略" in notes
    assert "不代表医院或科室的官方发布" in notes
    assert "76 张刺激图和品牌资源" in notes
    assert "仅限非临床工程评估" in notes
    assert "不得用于任何真实患者或临床" in notes
    assert "LICENSE-v0.1.1.txt" in notes
    assert "gh attestation verify" in notes
    assert "真实 Tobii" in notes
    assert "床旁" in notes


def test_readme_recommends_installer_and_keeps_short_emergency_command() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "OculiDoC-Setup.exe" in readme
    assert "在线安装最新版本" in readme
    assert "离线安装当前版本" in readme
    assert "Get-FileHash" in readme
    assert "Start-Process -FilePath" in readme
    assert "| iex" not in readme
    assert "Join-Path $env:TEMP" not in readme
