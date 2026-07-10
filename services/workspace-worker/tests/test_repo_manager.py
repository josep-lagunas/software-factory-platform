"""Tests for :mod:`workspace_worker.repo.manager` — the clone layer (SFP-38).

Two layers:

* **Unit** tests inject a fake runner to assert exact ``git`` argv (token
  injection, credential-strip command) and error redaction — no real git.
* **Integration** tests exercise the real ``git`` binary against a local
  ``file://`` bare remote to verify on-disk state (idempotency, clean
  ``.git/config``, teardown on failure) end-to-end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from workspace_worker.repo.manager import (
    CloneResult,
    RepoManager,
    RepoManagerError,
    _inject_token,
    _strip_userinfo,
)

TOKEN = "ghp_secrettoken_value_123"
HTTPS_URL = "https://github.com/arconta/some-repo.git"


class FakeRunner:
    """Records every git invocation; returns canned :class:`CompletedProcess`."""

    def __init__(
        self,
        *,
        side_effect: Exception | None = None,
        failing_cmd_prefix: tuple[str, ...] | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._side_effect = side_effect
        self._failing_prefix = failing_cmd_prefix

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if self._failing_prefix is not None and tuple(cmd[: len(self._failing_prefix)]) == (
            self._failing_prefix
        ):
            assert self._side_effect is not None
            raise self._side_effect
        if self._side_effect is not None and self._failing_prefix is None:
            raise self._side_effect
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def test_inject_token_adds_userinfo_for_https() -> None:
    authed = _inject_token(HTTPS_URL, TOKEN)
    assert authed == f"https://x-access-token:{TOKEN}@github.com/arconta/some-repo.git"


def test_inject_token_preserves_port_and_path() -> None:
    url = "https://gh.example.com:8443/o/r.git?x=1"
    authed = _inject_token(url, TOKEN)
    assert authed == f"https://x-access-token:{TOKEN}@gh.example.com:8443/o/r.git?x=1"


def test_inject_token_passes_through_non_https() -> None:
    # Token auth is HTTPS-only: file:// (local) and ssh:// (keys) are returned
    # unchanged so the clone proceeds without credentials.
    assert _inject_token("file:///tmp/repo.git", TOKEN) == "file:///tmp/repo.git"
    assert _inject_token("ssh://git@github.com/o/r.git", TOKEN) == "ssh://git@github.com/o/r.git"


def test_strip_userinfo_removes_token() -> None:
    authed = f"https://x-access-token:{TOKEN}@github.com/o/r.git"
    assert _strip_userinfo(authed) == "https://github.com/o/r.git"


def test_strip_userinfo_on_already_clean_url_is_noop() -> None:
    assert _strip_userinfo(HTTPS_URL) == HTTPS_URL


# ---------------------------------------------------------------------------
# clone — command shape (token injected, then stripped)
# ---------------------------------------------------------------------------


def test_clone_runs_clone_then_set_url_with_clean_url(tmp_path: Path) -> None:
    runner = FakeRunner()
    dest = tmp_path / "repo"
    mgr = RepoManager(TOKEN, runner=runner)

    result = mgr.clone(HTTPS_URL, dest)

    assert result == CloneResult(path=dest, cloned=True)
    assert len(runner.calls) == 2
    clone_cmd, strip_cmd = runner.calls
    # 1st: clone with the token-bearing URL.
    assert clone_cmd[:3] == [
        "git",
        "clone",
        f"https://x-access-token:{TOKEN}@github.com/arconta/some-repo.git",
    ]
    assert clone_cmd[3] == str(dest)
    # 2nd: rewrite origin to the token-free URL.
    assert strip_cmd == ["git", "-C", str(dest), "remote", "set-url", "origin", HTTPS_URL]


def test_clone_for_non_https_url_skips_token_injection(tmp_path: Path) -> None:
    runner = FakeRunner()
    mgr = RepoManager(TOKEN, runner=runner)
    file_url = "file:///srv/repos/r.git"
    dest = tmp_path / "repo"

    mgr.clone(file_url, dest)

    clone_cmd = runner.calls[0]
    assert clone_cmd[2] == file_url  # no userinfo added
    assert TOKEN not in " ".join(clone_cmd)


# ---------------------------------------------------------------------------
# clone — idempotency
# ---------------------------------------------------------------------------


def test_clone_is_idempotent_when_dest_is_a_repo(tmp_path: Path) -> None:
    runner = FakeRunner()
    dest = tmp_path / "repo"
    (dest / ".git").mkdir(parents=True)  # existing clone
    mgr = RepoManager(TOKEN, runner=runner)

    result = mgr.clone(HTTPS_URL, dest)

    assert result == CloneResult(path=dest, cloned=False)
    assert runner.calls == []  # no git invocation at all


def test_clone_raises_when_dest_exists_but_not_a_repo(tmp_path: Path) -> None:
    runner = FakeRunner()
    dest = tmp_path / "repo"
    dest.mkdir()  # exists, but no .git
    mgr = RepoManager(TOKEN, runner=runner)

    with pytest.raises(RepoManagerError, match="not a git repository"):
        mgr.clone(HTTPS_URL, dest)
    assert runner.calls == []  # bailed before any git call


# ---------------------------------------------------------------------------
# clone — error handling & token redaction
# ---------------------------------------------------------------------------


def test_clone_redacts_token_from_clone_failure(tmp_path: Path) -> None:
    err = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "clone", f"https://x-access-token:{TOKEN}@github.com/o/r.git"],
        stderr=f"remote: Invalid token {TOKEN}",
    )
    runner = FakeRunner(side_effect=err, failing_cmd_prefix=("git", "clone"))
    mgr = RepoManager(TOKEN, runner=runner)

    with pytest.raises(RepoManagerError) as exc_info:
        mgr.clone(HTTPS_URL, tmp_path / "repo")

    msg = str(exc_info.value)
    assert TOKEN not in msg
    assert "***" in msg
    assert "git clone failed" in msg


def test_clone_tears_down_and_redacts_when_set_url_fails(tmp_path: Path) -> None:
    # First call (clone) "succeeds" but leaves a marker dir; set-url fails.
    err = subprocess.CalledProcessError(
        returncode=1,
        cmd=["git", "remote", "set-url"],
        stderr=f"boom exposed {TOKEN}",
    )
    runner = FakeRunner(side_effect=err, failing_cmd_prefix=("git", "-C"))
    dest = tmp_path / "repo"
    mgr = RepoManager(TOKEN, runner=runner)

    with pytest.raises(RepoManagerError, match="strip credentials") as exc_info:
        mgr.clone(HTTPS_URL, dest)

    assert TOKEN not in str(exc_info.value)
    # The clone was rolled back — dest must not linger with a token in config.
    assert not dest.exists()


# ---------------------------------------------------------------------------
# Integration — real git against a local bare remote (file://)
# ---------------------------------------------------------------------------


def _seed_bare_remote(remote_dir: Path) -> Path:
    """Create a populated bare repo and return its file:// URL."""
    remote_dir.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote_dir)],
        check=True,
        capture_output=True,
    )
    # Seed it from a throwaway working repo with one commit.
    work = remote_dir.parent / "seed-work"
    work.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "config", "user.email", "t@t"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(work), "config", "user.name", "t"], check=True, capture_output=True
    )
    (work / "README").write_text("seed\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-m", "seed"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(work), "push", str(remote_dir), "main"],
        check=True,
        capture_output=True,
    )
    return remote_dir


def test_integration_clone_creates_repo_with_clean_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as _shutil  # noqa: PLC0415 — local import keeps top clean

    remote = _seed_bare_remote(tmp_path / "remote.git")
    _shutil.rmtree(tmp_path / "seed-work")  # tidy the seeding scaffold
    file_url = f"file://{remote}"
    dest = tmp_path / "checkout"

    mgr = RepoManager("")  # no token needed for file://
    result = mgr.clone(file_url, dest)

    assert result.cloned is True
    assert (dest / ".git").is_dir()
    assert (dest / "README").read_text() == "seed\n"
    # The stored origin URL is the token-free form.
    origin = subprocess.run(
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert origin == file_url
    # Belt-and-braces: nothing resembling a token anywhere in .git/config.
    config_text = (dest / ".git" / "config").read_text()
    assert "x-access-token" not in config_text
    assert "ghp_" not in config_text


def test_integration_clone_is_idempotent_real_git(tmp_path: Path) -> None:
    import shutil as _shutil  # noqa: PLC0415

    remote = _seed_bare_remote(tmp_path / "remote.git")
    _shutil.rmtree(tmp_path / "seed-work")
    dest = tmp_path / "checkout"

    mgr = RepoManager("")
    first = mgr.clone(f"file://{remote}", dest)
    second = mgr.clone(f"file://{remote}", dest)

    assert first.cloned is True
    assert second.cloned is False
    assert second.path == dest
