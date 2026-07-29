from __future__ import annotations

import tomllib
from pathlib import Path

import oculidoc

ROOT = Path(__file__).resolve().parents[1]


def test_release_uses_one_consistent_version() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == "0.1.1"
    assert oculidoc.__version__ == "0.1.1"


def test_public_readme_has_product_ownership_and_support_path() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "面向意识障碍患者的眼动仪操作界面与实验数据台" in readme
    assert "首都医科大学天坛医院意识障碍病区所有" in readme
    assert "he_jianghong@sina.cn" in readme
    assert "peterpig123456@gmail.com" in readme
    assert "feature/gaze-tasks-mvp" not in readme
    assert "M3D13" not in readme
    assert "ὀποῖν θέσις" in readme
    assert "open thesis" in readme
    assert "TOBII_OFFICIAL_INTEGRATION.md" in readme


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


def test_ci_builds_and_tag_workflow_publishes_verified_portable_package() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "windows-package:" in ci
    assert "package_windows_release.ps1" in ci
    assert "permissions:\n  contents: read" in ci
    assert 'tags:\n      - "v*"' in release
    assert "permissions:\n  contents: write" in release
    assert "gh release create" in release
    assert "package_windows_release.ps1" in release
    assert "choco install innosetup" in release


def test_readme_recommends_installer_and_keeps_short_emergency_command() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "OculiDoC-Setup.exe" in readme
    assert "在线安装最新版本" in readme
    assert "离线安装当前版本" in readme
    assert "Get-FileHash" in readme
    assert "Start-Process -FilePath" in readme
    assert "| iex" not in readme
    assert "Join-Path $env:TEMP" not in readme
