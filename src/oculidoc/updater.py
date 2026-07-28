"""Fast-forward-only updater used by the administrator desktop button."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlparse

PUBLIC_REPOSITORY_URL = "https://github.com/Etymodes/OculiDoC.git"
PUBLIC_REPOSITORY_SSH_443_URL = "ssh://git@ssh.github.com:443/Etymodes/OculiDoC.git"
PUBLIC_BRANCH = "main"
LATEST_RELEASE_API = "https://api.github.com/repos/Etymodes/OculiDoC/releases/latest"
SETUP_ASSET_NAME = "OculiDoC-Setup.exe"
SETUP_HASH_ASSET_NAME = "OculiDoC-Setup.exe.sha256"


class UpdateError(RuntimeError):
    """The source checkout cannot be updated safely."""


def installed_version() -> str:
    return version("oculidoc")


def _version_tuple(value: str) -> tuple[int, ...]:
    numeric = value.strip().lower().removeprefix("v").split("-", 1)[0]
    try:
        return tuple(int(part) for part in numeric.split("."))
    except ValueError as error:
        raise UpdateError(f"无法识别版本号：{value}") from error


def _latest_release() -> dict[str, object]:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "OculiDoC-Updater",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (OSError, ValueError) as error:
        raise UpdateError(f"无法获取 OculiDoC 最新正式版本：{error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("tag_name"), str):
        raise UpdateError("GitHub 返回的最新版本信息无效。")
    return payload


def check_release_update(current_version: str | None = None) -> dict[str, object]:
    current = current_version or installed_version()
    release = _latest_release()
    latest = str(release["tag_name"]).removeprefix("v")
    return {
        "status": (
            "update_available" if _version_tuple(latest) > _version_tuple(current) else "up_to_date"
        ),
        "current_version": current,
        "latest_version": latest,
    }


def _asset_url(release: dict[str, object], name: str) -> str:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("最新版本没有可下载文件。")
    for asset in assets:
        if (
            isinstance(asset, dict)
            and asset.get("name") == name
            and isinstance(asset.get("browser_download_url"), str)
        ):
            url = str(asset["browser_download_url"])
            if urlparse(url).scheme != "https":
                raise UpdateError(f"{name} 下载地址不是 HTTPS。")
            return url
    raise UpdateError(f"最新正式版本缺少 {name}。")


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "OculiDoC-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            destination.write_bytes(response.read())
    except OSError as error:
        raise UpdateError(f"下载 {destination.name} 失败：{error}") from error


def install_latest_release(current_version: str | None = None) -> dict[str, object]:
    current = current_version or installed_version()
    release = _latest_release()
    latest = str(release["tag_name"]).removeprefix("v")
    if _version_tuple(latest) <= _version_tuple(current):
        return {
            "status": "up_to_date",
            "current_version": current,
            "latest_version": latest,
        }

    download_dir = Path(tempfile.mkdtemp(prefix="OculiDoC-update-"))
    setup_path = download_dir / SETUP_ASSET_NAME
    hash_path = download_dir / SETUP_HASH_ASSET_NAME
    _download(_asset_url(release, SETUP_HASH_ASSET_NAME), hash_path)
    _download(_asset_url(release, SETUP_ASSET_NAME), setup_path)

    expected_hash = hash_path.read_text(encoding="utf-8-sig").strip().split()[0].lower()
    actual_hash = hashlib.sha256(setup_path.read_bytes()).hexdigest()
    if len(expected_hash) != 64 or actual_hash != expected_hash:
        setup_path.unlink(missing_ok=True)
        raise UpdateError("最新版安装包 SHA-256 校验失败，已停止更新。")

    creation_flags = 0x00000008 if os.name == "nt" else 0
    subprocess.Popen(
        [str(setup_path), "/OFFLINE"],
        close_fds=True,
        creationflags=creation_flags,
    )
    return {
        "status": "installer_started",
        "current_version": current,
        "latest_version": latest,
    }


def find_repository_root(start: str | Path) -> Path | None:
    path = Path(start).expanduser().resolve()

    if path.is_file():
        path = path.parent

    for candidate in (path, *path.parents):
        if (candidate / ".git").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate

    return None


def _git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding=None,
        errors="replace",
        timeout=30,
    )

    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise UpdateError(message)

    return completed


def _public_sources(repo: Path) -> list[str]:
    sources: list[str] = []
    remotes = _git(repo, "remote", check=False).stdout.splitlines()

    for remote in remotes:
        url = _git(repo, "remote", "get-url", remote, check=False).stdout.strip()
        normalized = url.lower().removesuffix(".git").replace("\\", "/")
        if normalized.endswith("etymodes/oculidoc"):
            sources.append(remote)

    for fallback in (PUBLIC_REPOSITORY_SSH_443_URL, PUBLIC_REPOSITORY_URL):
        if fallback not in sources:
            sources.append(fallback)

    return sources


def _fetch_public_main(repo: Path) -> str:
    errors: list[str] = []

    for source in _public_sources(repo):
        try:
            fetched = _git(
                repo,
                "fetch",
                "--quiet",
                source,
                f"refs/heads/{PUBLIC_BRANCH}",
                check=False,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{source}: 连接超时")
            continue

        if fetched.returncode == 0:
            return _git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()

        message = fetched.stderr.strip() or fetched.stdout.strip() or "连接失败"
        errors.append(f"{source}: {message}")

    raise UpdateError(
        "无法连接 OculiDoC 官方 main 分支。请检查网络或 GitHub SSH 配置。\n" + "\n".join(errors)
    )


def perform_update(repo_root: str | Path) -> dict[str, object]:
    """Fetch public main and apply only a clean fast-forward."""
    repo = Path(repo_root).expanduser().resolve()

    if find_repository_root(repo) != repo:
        raise UpdateError("未找到 OculiDoC 源码仓库。")

    if _git(repo, "status", "--porcelain").stdout.strip():
        raise UpdateError("仓库存在未提交修改，已停止更新以免覆盖工作。")

    branch = _git(repo, "branch", "--show-current").stdout.strip()

    if not branch:
        raise UpdateError("当前仓库处于 detached HEAD，无法一键更新。")
    if branch != PUBLIC_BRANCH:
        raise UpdateError(
            f"当前位于 {branch} 分支；一键更新只更新 {PUBLIC_BRANCH}。请先切换到 {PUBLIC_BRANCH}。"
        )

    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    available = _fetch_public_main(repo)

    if before == available:
        return {
            "status": "up_to_date",
            "branch": branch,
            "before": before,
            "after": before,
        }

    ancestor = _git(repo, "merge-base", "--is-ancestor", before, available, check=False)

    if ancestor.returncode != 0:
        raise UpdateError("本地分支与远端已分叉，不能自动快进更新。")

    _git(repo, "merge", "--ff-only", available)
    after = _git(repo, "rev-parse", "HEAD").stdout.strip()

    if after != available:
        raise UpdateError("更新后的提交核验失败。")

    return {
        "status": "updated",
        "branch": branch,
        "before": before,
        "after": after,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    try:
        result = perform_update(args.repo)
    except (OSError, subprocess.SubprocessError, UpdateError) as error:
        print(json.dumps({"status": "error", "message": str(error)}))
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
