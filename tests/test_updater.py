from __future__ import annotations

import subprocess
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

import oculidoc
import oculidoc.updater as updater
from oculidoc.updater import (
    UpdateError,
    _asset_url,
    _version_tuple,
    find_repository_root,
    perform_update,
)


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


def test_release_versions_compare_numerically() -> None:
    assert _version_tuple("v0.10.0") > _version_tuple("0.9.9")
    assert _version_tuple("v1.2.3") == (1, 2, 3)


def test_installed_version_works_without_distribution_metadata() -> None:
    assert updater.installed_version() == oculidoc.__version__


@pytest.mark.parametrize("valid_hash", [True, False])
def test_release_installer_starts_only_after_hash_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_hash: bool
) -> None:
    payload = b"synthetic installer bytes"
    expected_hash = sha256(payload).hexdigest() if valid_hash else "0" * 64
    release = {
        "tag_name": "v99.0.0",
        "assets": [
            {"name": name, "browser_download_url": f"https://example.test/{name}"}
            for name in (updater.SETUP_ASSET_NAME, updater.SETUP_HASH_ASSET_NAME)
        ],
    }
    launches: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(updater, "_latest_release", lambda: release)
    monkeypatch.setattr(updater.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path))

    def fake_download(_url: str, destination: Path) -> None:
        if destination.name == updater.SETUP_HASH_ASSET_NAME:
            destination.write_text(expected_hash, encoding="utf-8")
        else:
            destination.write_bytes(payload)

    def fake_launch(arguments: list[str], **kwargs: object) -> object:
        launches.append((arguments, kwargs))
        return object()

    monkeypatch.setattr(updater, "_download", fake_download)
    monkeypatch.setattr(updater.subprocess, "Popen", fake_launch)

    if valid_hash:
        result = updater.install_latest_release(current_version="0.1.3")
        assert result["status"] == "installer_started"
        assert launches == [
            (
                [str(tmp_path / updater.SETUP_ASSET_NAME), "/OFFLINE"],
                {"close_fds": True, "creationflags": 0x00000008 if updater.os.name == "nt" else 0},
            )
        ]
    else:
        with pytest.raises(UpdateError, match="SHA-256 校验失败"):
            updater.install_latest_release(current_version="0.1.3")
        assert launches == []
        assert not (tmp_path / updater.SETUP_ASSET_NAME).exists()


def test_release_asset_requires_https() -> None:
    release = {
        "assets": [{"name": "OculiDoC-Setup.exe", "browser_download_url": "http://example.test/a"}]
    }
    with pytest.raises(UpdateError, match="不是 HTTPS"):
        _asset_url(release, "OculiDoC-Setup.exe")


def test_release_download_streams_to_disk(tmp_path: Path, monkeypatch) -> None:
    class StreamingResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

        def read(self, size: int = -1) -> bytes:
            assert size > 0
            return super().read(size)

    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda _request, timeout: StreamingResponse(b"installer"),
    )
    destination = tmp_path / "OculiDoC-Setup.exe"

    updater._download("https://example.test/OculiDoC-Setup.exe", destination)

    assert destination.read_bytes() == b"installer"


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
    monkeypatch.setattr(updater, "PUBLIC_REPOSITORY_SSH_443_URL", str(remote))

    result = perform_update(checkout)

    assert result["status"] == "updated"
    assert result["after"] == expected
    assert _git(checkout, "rev-parse", "HEAD") == expected
    assert perform_update(checkout)["status"] == "up_to_date"


def test_updater_refuses_non_main_branch(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "feature/test", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.name", "OculiDoC Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='oculidoc'\n", encoding="utf-8")
    _git(tmp_path, "add", "pyproject.toml")
    _git(tmp_path, "commit", "-qm", "initial")

    with pytest.raises(UpdateError, match="只更新 main"):
        perform_update(tmp_path)
