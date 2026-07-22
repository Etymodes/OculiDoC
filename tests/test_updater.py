from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import oculidoc.updater as updater
from oculidoc.updater import UpdateError, find_repository_root, perform_update


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_find_repository_root_walks_from_source_file(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='oculidoc'\n", encoding="utf-8")
    source = tmp_path / "src" / "oculidoc" / "module.py"
    source.parent.mkdir(parents=True)
    source.write_text("", encoding="utf-8")

    assert find_repository_root(source) == tmp_path


def test_updater_refuses_dirty_checkout_before_network_access(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='oculidoc'\n", encoding="utf-8")

    with pytest.raises(UpdateError, match="未提交修改"):
        perform_update(tmp_path)


def test_updater_applies_only_clean_fast_forward(tmp_path: Path, monkeypatch) -> None:
    remote = tmp_path / "remote.git"
    author = tmp_path / "author"
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(author)], check=True)
    _git(author, "config", "user.name", "OculiDoC Test")
    _git(author, "config", "user.email", "test@example.invalid")
    (author / "pyproject.toml").write_text("[project]\nname='oculidoc'\n", encoding="utf-8")
    _git(author, "add", "pyproject.toml")
    _git(author, "commit", "-qm", "initial")
    _git(author, "remote", "add", "origin", str(remote))
    _git(author, "push", "-qu", "origin", "main")
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(checkout)], check=True)

    (author / "version.txt").write_text("next\n", encoding="utf-8")
    _git(author, "add", "version.txt")
    _git(author, "commit", "-qm", "next")
    _git(author, "push", "-q", "origin", "main")
    expected = _git(author, "rev-parse", "HEAD")
    monkeypatch.setattr(updater, "PUBLIC_REPOSITORY_URL", str(remote))

    result = perform_update(checkout)

    assert result["status"] == "updated"
    assert result["after"] == expected
    assert _git(checkout, "rev-parse", "HEAD") == expected
    assert perform_update(checkout)["status"] == "up_to_date"
