"""Fast-forward-only updater used by the administrator desktop button."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

PUBLIC_REPOSITORY_URL = "https://github.com/Etymodes/OculiDoC.git"


class UpdateError(RuntimeError):
    """The source checkout cannot be updated safely."""


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
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise UpdateError(message)

    return completed


def perform_update(repo_root: str | Path) -> dict[str, object]:
    """Fetch the current branch over HTTPS and apply only a clean fast-forward."""
    repo = Path(repo_root).expanduser().resolve()

    if find_repository_root(repo) != repo:
        raise UpdateError("未找到 OculiDoC 源码仓库。")

    if _git(repo, "status", "--porcelain").stdout.strip():
        raise UpdateError("仓库存在未提交修改，已停止更新以免覆盖工作。")

    branch = _git(repo, "branch", "--show-current").stdout.strip()

    if not branch:
        raise UpdateError("当前仓库处于 detached HEAD，无法一键更新。")

    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "fetch", "--quiet", PUBLIC_REPOSITORY_URL, f"refs/heads/{branch}")
    available = _git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()

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
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
