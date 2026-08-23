"""Tests for :mod:`workspace_worker.repo.manager` — the clone layer (SFP-38).

Two layers:

* **Unit** tests inject a fake runner to assert exact ``git`` argv (token
  injection, credential-strip command) and error redaction — no real git.
* **Integration** tests exercise the real ``git`` binary against a local
  ``file://`` bare remote to verify on-disk state (idempotency, clean
  ``.git/config``, teardown on failure) end-to-end.
"""

from __future__ import annotations

import shutil as _shutil
import subprocess
from pathlib import Path

import pytest
from workspace_worker.repo.manager import (
    BaseSyncConflictError,
    BaseSyncResult,
    CloneResult,
    PushResult,
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
    # The chain is suppressed (`from None`): the original CalledProcessError's
    # `.cmd` carries the token-bearing argv and would leak via the traceback.
    assert exc_info.value.__cause__ is None
    import traceback as _tb

    full_tb = "".join(_tb.format_exception(exc_info.value))
    assert TOKEN not in full_tb
    assert "x-access-token" not in full_tb


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
    # Chain suppressed (`from None`) — the set-url CalledProcessError's argv
    # could carry the token via the cloned config; the traceback must stay clean.
    assert exc_info.value.__cause__ is None
    import traceback as _tb

    assert TOKEN not in "".join(_tb.format_exception(exc_info.value))
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


# ---------------------------------------------------------------------------
# push — unit (fake runner): one-shot authed push, NO set-url (SFP-224)
# ---------------------------------------------------------------------------


def test_push_runs_single_push_with_authed_url_and_no_set_url(tmp_path: Path) -> None:
    """push() issues exactly ONE ``git -C <path> push <authed_url> <branch>``.

    Critically it does NOT call ``git remote set-url`` — the token must NEVER be
    written to ``.git/config`` (symmetric to clone()'s credential-strip, but
    here the origin is already clean so there is nothing to strip).
    """
    runner = FakeRunner()
    repo_path = tmp_path / "repo"
    mgr = RepoManager(TOKEN, runner=runner)

    result = mgr.push(repo_path, "sfp-224-x", remote_url=HTTPS_URL)

    assert result == PushResult(path=repo_path, branch="sfp-224-x", pushed=True)
    assert len(runner.calls) == 1  # the push ONLY — no set-url, no get-url
    cmd = runner.calls[0]
    assert cmd[:4] == ["git", "-C", str(repo_path), "push"]
    # The authed URL carries the token as userinfo (transient — argv only).
    assert cmd[4] == f"https://x-access-token:{TOKEN}@github.com/arconta/some-repo.git"
    assert cmd[5] == "sfp-224-x"
    # Belt-and-braces: no set-url invocation anywhere.
    assert not any("set-url" in c for c in (" ".join(c) for c in runner.calls))


def test_push_reads_on_disk_origin_when_remote_url_none(tmp_path: Path) -> None:
    """When remote_url is None, push() reads the token-free origin via get-url."""
    origin_url = "https://github.com/arconta/some-repo.git"
    calls: list[list[str]] = []

    def runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        # get-url returns the token-free origin; push succeeds.
        if cmd[:5] == ["git", "-C", str(tmp_path / "repo"), "remote", "get-url"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=origin_url + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    repo_path = tmp_path / "repo"
    mgr = RepoManager(TOKEN, runner=runner)

    result = mgr.push(repo_path, "sfp-224-x")

    assert result.pushed is True
    # get-url THEN push — exactly two calls.
    assert len(calls) == 2
    assert calls[0] == ["git", "-C", str(repo_path), "remote", "get-url", "origin"]
    push_cmd = calls[1]
    assert push_cmd[:4] == ["git", "-C", str(repo_path), "push"]
    # The token was injected into the throwaway push URL from the clean origin.
    assert push_cmd[4] == f"https://x-access-token:{TOKEN}@github.com/arconta/some-repo.git"


def test_push_redacts_token_from_failure(tmp_path: Path) -> None:
    """A failed push surfaces a redacted RepoManagerError — no token in message or traceback."""
    authed_cmd = [
        "git",
        "-C",
        str(tmp_path / "repo"),
        "push",
        f"https://x-access-token:{TOKEN}@github.com/arconta/some-repo.git",
        "sfp-224-x",
    ]
    err = subprocess.CalledProcessError(
        returncode=128,
        cmd=authed_cmd,
        stderr=f"remote: Invalid token {TOKEN}",
    )
    runner = FakeRunner(side_effect=err, failing_cmd_prefix=("git", "-C"))
    mgr = RepoManager(TOKEN, runner=runner)

    with pytest.raises(RepoManagerError) as exc_info:
        mgr.push(tmp_path / "repo", "sfp-224-x", remote_url=HTTPS_URL)

    msg = str(exc_info.value)
    assert TOKEN not in msg
    assert "***" in msg
    assert "git push failed" in msg
    # Chain suppressed (`from None`) — the push CalledProcessError's argv carries
    # the token-bearing authed URL; the traceback must stay clean.
    assert exc_info.value.__cause__ is None
    import traceback as _tb

    full_tb = "".join(_tb.format_exception(exc_info.value))
    assert TOKEN not in full_tb
    assert "x-access-token" not in full_tb


# ---------------------------------------------------------------------------
# push — integration (real git against a local bare remote, file://)
# ---------------------------------------------------------------------------


def test_integration_push_uploads_commit_and_keeps_config_token_free(
    tmp_path: Path,
) -> None:
    """Real git: clone, commit a file, push() — the commit arrives on the remote
    and ``.git/config`` stays token-free (no set-url ever wrote the token)."""
    remote = _seed_bare_remote(tmp_path / "remote.git")
    _shutil.rmtree(tmp_path / "seed-work")
    file_url = f"file://{remote}"
    clone_dest = tmp_path / "checkout"

    # Clone via RepoManager (writes token-free origin).
    mgr = RepoManager("")  # no token needed for file://
    mgr.clone(file_url, clone_dest)

    # Commit a new file on a branch in the clone.
    branch = "sfp-224-x"
    subprocess.run(
        ["git", "-C", str(clone_dest), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone_dest), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone_dest), "checkout", "-b", branch],
        check=True,
        capture_output=True,
    )
    (clone_dest / "NEW").write_text("pushed\n")
    subprocess.run(["git", "-C", str(clone_dest), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(clone_dest), "commit", "-m", "slice push"],
        check=True,
        capture_output=True,
    )

    # push() uploads the branch to the bare remote.
    result = mgr.push(clone_dest, branch, remote_url=file_url)
    assert result.pushed is True

    # The commit is now reachable on the remote.
    remote_head = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", branch],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    local_head = subprocess.run(
        ["git", "-C", str(clone_dest), "rev-parse", branch],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_head == local_head
    (tmp_path / "remote.git" / "objects").mkdir(exist_ok=True)  # touch for assertion order
    assert (tmp_path / "remote.git" / "objects").exists()

    # Belt-and-braces: no token anywhere in .git/config (push() never set-url).
    config_text = (clone_dest / ".git" / "config").read_text()
    assert "x-access-token" not in config_text
    assert "ghp_" not in config_text


def test_integration_clone_is_idempotent_real_git(tmp_path: Path) -> None:
    remote = _seed_bare_remote(tmp_path / "remote.git")
    _shutil.rmtree(tmp_path / "seed-work")
    dest = tmp_path / "checkout"

    mgr = RepoManager("")
    first = mgr.clone(f"file://{remote}", dest)
    second = mgr.clone(f"file://{remote}", dest)

    assert first.cloned is True
    assert second.cloned is False
    assert second.path == dest


# ---------------------------------------------------------------------------
# sync_base — pre-push base sync (SFP-240). Unit layer: fake runners assert the
# exact git argv (one-shot authed fetch, bot-identity merge, abort ordering,
# conflicted-name listing) and error redaction — no real git.
# ---------------------------------------------------------------------------


class FakeMergeRunner:
    """check=False-shaped fake for the ONE command whose non-zero exit is data.

    Returns a canned :class:`subprocess.CompletedProcess` (default exit 0) and
    records each argv. Tests set ``returncode`` to simulate a conflict.
    """

    def __init__(self, *, returncode: int = 0, stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self._returncode = returncode
        self._stdout = stdout

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self._returncode, stdout=self._stdout, stderr="")


def _cp(stdout: str = "") -> subprocess.CompletedProcess[str]:
    """A successful check=True-shaped result carrying ``stdout``."""
    return subprocess.CompletedProcess([], returncode=0, stdout=stdout, stderr="")


def _args(cmd: list[str]) -> list[str]:
    """Strip the fixed ``git -C <path>`` prefix from a scripted-runner argv.

    Production always invokes the runner as ``["git", "-C", <path>, <sub>, ...]``
    (spaced ``-C`` — git rejects the attached ``-C<path>`` form with exit 129),
    so the subcommand starts at index 3. Matching on the stripped argv keeps the
    fakes independent of the worktree path.
    """
    return cmd[3:] if cmd[:2] == ["git", "-C"] else cmd[1:]


def test_sync_base_fetches_authed_and_merges_with_bot_identity(tmp_path: Path) -> None:
    """Clean merge: authed fetch of the base, then a MERGE (never rebase)
    committed as the bot identity via one-shot ``-c`` argv config."""
    calls: list[list[str]] = []
    heads = iter(["aaaa1111", "bbbb2222"])  # rev-parse HEAD before/after -> differ

    def scripted(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if _args(cmd)[:2] == ["rev-parse", "HEAD"]:
            return _cp(next(heads))
        return _cp()

    merge_runner = FakeMergeRunner(returncode=0, stdout="Merge made by the 'ort' strategy.")
    mgr = RepoManager(TOKEN, runner=scripted, merge_runner=merge_runner)

    result = mgr.sync_base(tmp_path / "wt", "main", remote_url=HTTPS_URL)

    assert result == BaseSyncResult(path=tmp_path / "wt", base_branch="main", merged=True)
    # Command 1: the one-shot authed fetch (token on the argv, refspec base).
    fetch_cmd = calls[0]
    assert fetch_cmd[:3] == ["git", "-C", str(tmp_path / "wt")]
    assert fetch_cmd[3] == "fetch"
    assert fetch_cmd[4] == f"https://x-access-token:{TOKEN}@github.com/arconta/some-repo.git"
    assert fetch_cmd[5] == "main"
    # The merge: MERGE (no rebase), --no-edit, FETCH_HEAD, bot identity via
    # one-shot -c (spaced form — git rejects "-cuser.name=..." with exit 129).
    merge_cmd = merge_runner.calls[0]
    assert "merge" in merge_cmd
    assert "rebase" not in merge_cmd
    assert merge_cmd[merge_cmd.index("merge") + 1 :][:2] == ["--no-edit", "FETCH_HEAD"]
    assert "user.name=sfp-coder-bot" in merge_cmd
    assert "user.email=299957016+sfp-coder-bot@users.noreply.github.com" in merge_cmd
    assert merge_cmd[:2] == ["git", "-C"]  # spaced -C, not the invalid -C<path>
    # No push happened here — sync_base never pushes.
    assert all("push" not in " ".join(c) for c in calls)


def test_sync_base_reads_on_disk_origin_when_remote_url_none(tmp_path: Path) -> None:
    """remote_url=None: the token-free on-disk origin is read via get-url, then
    the token is injected into the throwaway fetch URL only."""
    calls: list[list[str]] = []
    shas = iter(["aaaa1111"])

    def scripted(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if _args(cmd)[:3] == ["remote", "get-url", "origin"]:
            return _cp(HTTPS_URL)
        if _args(cmd)[:2] == ["rev-parse", "HEAD"]:
            return _cp(next(shas, "aaaa1111"))
        return _cp()

    mgr = RepoManager(TOKEN, runner=scripted, merge_runner=FakeMergeRunner())

    mgr.sync_base(tmp_path / "wt", "main", remote_url=None)

    # First command: read the token-free origin. Second: the AUTHED fetch URL
    # built FROM that origin (token injected for this one invocation only).
    assert calls[0][3:6] == ["remote", "get-url", "origin"]
    assert calls[1][3] == "fetch"
    assert calls[1][4] == f"https://x-access-token:{TOKEN}@github.com/arconta/some-repo.git"
    # The on-disk origin was never rewritten (no set-url anywhere).
    assert all("set-url" not in " ".join(c) for c in calls)


def test_sync_base_conflict_aborts_and_names_files(tmp_path: Path) -> None:
    """Conflict path: conflicted names are listed FIRST, then ``git merge
    --abort`` runs, then BaseSyncConflictError raises with the names."""
    merge_runner = FakeMergeRunner(returncode=1, stdout="CONFLICT (content): merge conflict")
    order: list[str] = []

    def scripted(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        joined = " ".join(cmd)
        if _args(cmd)[:2] == ["rev-parse", "HEAD"]:
            order.append("rev-parse")
            return _cp("aaaa1111")
        if "--diff-filter=U" in joined:
            order.append("list-conflicted")
            return _cp("src/a.py\nsrc/b.py\n")
        if _args(cmd)[:2] == ["merge", "--abort"]:
            order.append("merge-abort")
            return _cp()
        return _cp()

    mgr = RepoManager(TOKEN, runner=scripted, merge_runner=merge_runner)

    with pytest.raises(BaseSyncConflictError) as exc_info:
        mgr.sync_base(tmp_path / "wt", "main", remote_url=HTTPS_URL)

    err = exc_info.value
    assert err.conflicted_files == ("src/a.py", "src/b.py")
    assert "src/a.py" in str(err)
    assert "src/b.py" in str(err)
    assert "base stale" in str(err)
    # Ordering: conflicted listing BEFORE the abort (the index still holds the
    # conflicted state when --diff-filter=U runs).
    assert order.index("list-conflicted") < order.index("merge-abort")
    # No half-merge reported: the abort ran exactly once.
    assert order.count("merge-abort") == 1


def test_sync_base_conflict_redacts_token_from_message(tmp_path: Path) -> None:
    """A conflict whose git stderr carries the token must not leak it — neither
    in the message nor in the traceback (chain suppressed where relevant)."""
    merge_runner = FakeMergeRunner(returncode=1)
    err_listing = subprocess.CalledProcessError(
        returncode=1, cmd=["git", "diff", "--name-only", "--diff-filter=U"], stderr=f"boom {TOKEN}"
    )

    def scripted(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if _args(cmd)[:2] == ["rev-parse", "HEAD"]:
            return _cp("aaaa1111")
        if "--diff-filter=U" in " ".join(cmd):
            raise err_listing
        return _cp()

    mgr = RepoManager(TOKEN, runner=scripted, merge_runner=merge_runner)

    # The listing itself failing surfaces as RepoManagerError (fail-closed), not
    # a half-aborted state.
    with pytest.raises(RepoManagerError, match="failed to list conflicted files"):
        mgr.sync_base(tmp_path / "wt", "main", remote_url=HTTPS_URL)


def test_sync_base_fetch_failure_redacts_token(tmp_path: Path) -> None:
    """A failed fetch surfaces a redacted RepoManagerError with no chain."""
    err = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "fetch", f"https://x-access-token:{TOKEN}@github.com/o/r.git", "main"],
        stderr=f"fatal: Authentication failed for {TOKEN}",
    )
    runner = FakeRunner(side_effect=err, failing_cmd_prefix=("git", "-C"))
    mgr = RepoManager(TOKEN, runner=runner, merge_runner=FakeMergeRunner())

    with pytest.raises(RepoManagerError, match="git fetch failed") as exc_info:
        mgr.sync_base(tmp_path / "wt", "main", remote_url=HTTPS_URL)

    assert TOKEN not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_sync_base_origin_read_failure_fails_closed(tmp_path: Path) -> None:
    """remote_url=None and the on-disk origin cannot be read (no remote
    configured): a redacted RepoManagerError, before any fetch/merge runs."""
    err = subprocess.CalledProcessError(
        returncode=2, cmd=["git", "remote", "get-url", "origin"], stderr=f"no such remote {TOKEN}"
    )

    # Production invokes the runner as ["git", "-C", <path>, "remote", ...], so
    # the subcommand lives at argv index 3 (see _args) — a prefix of
    # ("git", "-C", "remote") would never match because <path> sits between.
    def failing_remote_read(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if _args(cmd)[:2] == ["remote", "get-url"]:
            raise err
        return _cp()

    merge_runner = FakeMergeRunner()

    mgr = RepoManager(TOKEN, runner=failing_remote_read, merge_runner=merge_runner)

    with pytest.raises(RepoManagerError, match="failed to read origin") as exc_info:
        mgr.sync_base(tmp_path / "wt", "main", remote_url=None)

    assert TOKEN not in str(exc_info.value)
    # Fail-closed before the merge: the merge runner was never invoked.
    assert merge_runner.calls == []


def test_sync_base_rev_parse_failure_degrades_to_empty_sha(tmp_path: Path) -> None:
    """A rev-parse that fails on a synced worktree (should not happen) degrades
    to '' — the no-op detection under-reports ``merged`` rather than raising."""
    merge_runner = FakeMergeRunner(returncode=0)

    def scripted(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if _args(cmd)[:2] == ["rev-parse", "HEAD"]:
            raise subprocess.CalledProcessError(returncode=128, cmd=list(cmd), stderr="not a repo")
        return _cp()

    mgr = RepoManager(TOKEN, runner=scripted, merge_runner=merge_runner)

    result = mgr.sync_base(tmp_path / "wt", "main", remote_url=HTTPS_URL)

    # Both rev-parses failed -> '' != '' is False -> under-reported as a no-op.
    assert result.merged is False


def test_sync_base_failed_abort_reports_dirty_state_not_conflict(tmp_path: Path) -> None:
    """If ``git merge --abort`` itself fails, the error raised is the ABORT
    failure (worktree possibly half-merged) — never a clean conflict error."""
    merge_runner = FakeMergeRunner(returncode=1)
    abort_err = subprocess.CalledProcessError(
        returncode=128, cmd=["git", "merge", "--abort"], stderr="cannot abort"
    )

    def scripted(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if _args(cmd)[:2] == ["rev-parse", "HEAD"]:
            return _cp("aaaa1111")
        if "--diff-filter=U" in " ".join(cmd):
            return _cp("src/a.py\n")
        if _args(cmd)[:2] == ["merge", "--abort"]:
            raise abort_err
        return _cp()

    mgr = RepoManager(TOKEN, runner=scripted, merge_runner=merge_runner)

    with pytest.raises(RepoManagerError, match="merge --abort.*failed") as exc_info:
        mgr.sync_base(tmp_path / "wt", "main", remote_url=HTTPS_URL)

    assert not isinstance(exc_info.value, BaseSyncConflictError)


def test_sync_base_noop_when_head_did_not_move(tmp_path: Path) -> None:
    """Base already current: git exits 0 and HEAD is UNCHANGED -> merged=False,
    with no locale-dependent stdout parsing."""
    merge_runner = FakeMergeRunner(returncode=0, stdout="Already up to date.")
    same = iter(["aaaa1111", "aaaa1111"])

    def scripted(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if _args(cmd)[:2] == ["rev-parse", "HEAD"]:
            return _cp(next(same))
        return _cp()

    mgr = RepoManager(TOKEN, runner=scripted, merge_runner=merge_runner)

    result = mgr.sync_base(tmp_path / "wt", "main", remote_url=HTTPS_URL)

    assert result.merged is False


def test_sync_base_never_writes_token_to_config(tmp_path: Path) -> None:
    """No ``git config`` / ``remote set-url`` is issued — the on-disk origin is
    never touched by the sync (token lives only on the one fetch argv)."""
    runner = FakeRunner()
    shas = iter(["aaaa1111", "bbbb2222"])

    def scripted(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if _args(cmd)[:2] == ["rev-parse", "HEAD"]:
            return _cp(next(shas))
        return FakeRunner.__call__(runner, cmd)

    mgr = RepoManager(TOKEN, runner=scripted, merge_runner=FakeMergeRunner())

    mgr.sync_base(tmp_path / "wt", "main", remote_url=HTTPS_URL)

    joined = [" ".join(c) for c in runner.calls]
    assert all("set-url" not in j for j in joined)
    assert all("git config" not in j for j in joined)


# ---------------------------------------------------------------------------
# sync_base — integration (real git against a local bare remote, file://)
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None) -> str:
    """Run git, returning stdout (check=True — the test asserts on success).

    Spaced ``-C <path>`` only: git rejects the attached ``-C<path>`` spelling
    with exit 129 ("unknown option"), verified against git 2.54.
    """
    cmd = ["git"] + (["-C", str(cwd)] if cwd else []) + list(args)
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return (proc.stdout or "").strip()


def _seed_remote_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare remote (main @ 'seed') and a ticket-branch worktree.

    The worktree is a real ``git worktree`` off a clone, exactly as the
    pipeline creates them — sync_base merges INTO the checked-out branch.
    """
    remote = _seed_bare_remote(tmp_path / "remote.git")
    _shutil.rmtree(tmp_path / "seed-work")
    clone = tmp_path / "clone"
    mgr = RepoManager("")
    mgr.clone(f"file://{remote}", clone)
    branch = "sfp-240-integration"
    subprocess.run(
        ["git", "-C", str(clone), "worktree", "add", "-b", branch, str(tmp_path / "wt")],
        check=True,
        capture_output=True,
    )
    return remote, tmp_path / "wt"


def _commit_all(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content)
    _git("add", ".", cwd=repo)
    _git("commit", "-m", message, cwd=repo)


def test_integration_sync_base_merges_stale_base_and_pushes_conflict_free(
    tmp_path: Path,
) -> None:
    """Stale base: main advances on the remote AFTER the branch diverges; the
    sync merges it in and the branch then pushes clean (PR would open
    conflict-free against current main)."""
    remote, wt = _seed_remote_and_worktree(tmp_path)
    clone = tmp_path / "clone"

    # Diverge: ticket commit on the branch (worktree), base commit on main.
    _commit_all(wt, "ticket.txt", "ticket\n", "ticket work")
    _commit_all(clone, "base.txt", "base\n", "base advance")
    _git("push", "origin", "main", cwd=clone)

    mgr = RepoManager("")
    result = mgr.sync_base(wt, "main")

    assert result.merged is True
    # Both files are present post-merge — the merged tree carries the base.
    assert (wt / "base.txt").read_text() == "base\n"
    assert (wt / "ticket.txt").read_text() == "ticket\n"
    # The merge commit is authored as the bot identity.
    log = _git("log", "-1", "--format=%an <%ae>", cwd=wt)
    assert log == "sfp-coder-bot <299957016+sfp-coder-bot@users.noreply.github.com>"
    # No conflicted state remains.
    assert _git("diff", "--name-only", "--diff-filter=U", cwd=wt) == ""
    # And the branch pushes clean against current main (no 405-style surprise).
    subprocess.run(
        ["git", "-C", str(wt), "push", f"file://{remote}", "HEAD:refs/heads/sfp-240-integration"],
        check=True,
        capture_output=True,
    )


def test_integration_sync_base_conflict_aborts_fail_closed(tmp_path: Path) -> None:
    """Conflicting base: both sides touch the SAME file; the sync aborts with
    the named file and the worktree is left PRE-merge (no conflict markers, no
    unresolved index, original content intact)."""
    _remote, wt = _seed_remote_and_worktree(tmp_path)
    clone = tmp_path / "clone"

    # Both sides modify the seeded README -> content conflict.
    _commit_all(wt, "README", "ticket side\n", "ticket edit")
    _commit_all(clone, "README", "base side\n", "base edit")
    _git("push", "origin", "main", cwd=clone)

    mgr = RepoManager("")
    with pytest.raises(BaseSyncConflictError) as exc_info:
        mgr.sync_base(wt, "main")

    assert exc_info.value.conflicted_files == ("README",)
    assert "README" in str(exc_info.value)
    # `git merge --abort` ran: NO unresolved index entries remain...
    assert _git("diff", "--name-only", "--diff-filter=U", cwd=wt) == ""
    # ...no MERGE_HEAD (not mid-merge)...
    assert (
        subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            capture_output=True,
        ).returncode
        != 0
    )
    # ...no conflict markers in the working file...
    assert "<<<" not in (wt / "README").read_text()
    # ...and the ticket side's content is intact (pre-merge state restored).
    assert (wt / "README").read_text() == "ticket side\n"
    # Nothing was committed by the aborted merge attempt.
    assert _git("log", "-1", "--format=%s", cwd=wt) == "ticket edit"


def test_integration_sync_base_noop_when_base_current(tmp_path: Path) -> None:
    """Base current: the sync is a cheap no-op — merged=False, HEAD unchanged,
    and the working tree is untouched."""
    _remote, wt = _seed_remote_and_worktree(tmp_path)
    # Ticket commit only; main has NOT advanced since the branch was created.
    _commit_all(wt, "ticket.txt", "ticket\n", "ticket work")

    head_before = _git("rev-parse", "HEAD", cwd=wt)
    mgr = RepoManager("")
    result = mgr.sync_base(wt, "main")

    assert result.merged is False
    assert _git("rev-parse", "HEAD", cwd=wt) == head_before
    assert (wt / "ticket.txt").read_text() == "ticket\n"
    assert _git("status", "--porcelain", cwd=wt) == ""


def test_integration_sync_base_repeated_call_is_idempotent(tmp_path: Path) -> None:
    """Determinism: running the sync twice yields the same outcome — the second
    call is the no-op (the first already merged the base)."""
    remote, wt = _seed_remote_and_worktree(tmp_path)
    clone = tmp_path / "clone"
    _commit_all(wt, "ticket.txt", "ticket\n", "ticket work")
    _commit_all(clone, "base.txt", "base\n", "base advance")
    _git("push", "origin", "main", cwd=clone)

    mgr = RepoManager("")
    first = mgr.sync_base(wt, "main")
    second = mgr.sync_base(wt, "main")

    assert first.merged is True
    assert second.merged is False
    assert _git("status", "--porcelain", cwd=wt) == ""
