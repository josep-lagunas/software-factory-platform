"""Tests for :mod:`workspace_worker.repo.branch` — the branch lifecycle (SFP-57).

Two layers, mirroring ``test_repo_worktree.py``:

* **Unit** tests inject a fake runner to assert the exact ``git`` argv (the
  ``branch --list`` refuse-to-clobber pre-check, the ``branch <name> <ref>``
  create, ``checkout``, ``branch -D`` delete) plus error wrapping/idempotency —
  no real git. Remote deletion is exercised through an injected
  :class:`FakeRemoteGitProvider` (NO token, NO network) so BranchManager's
  delegation and the ``branch -> heads/<branch>`` mapping are validated without
  touching the adapter or the network.
* **Integration** tests exercise the real ``git`` binary against a local bare
  remote cloned via the real :class:`RepoManager` (reusing
  ``_seed_bare_remote`` from the sibling test module) so the subprocess branch
  operations are validated end-to-end: create yields a listed branch, checkout
  switches HEAD, delete removes it, and delete is idempotent when called twice.

AC6 (no token in BranchManager) is demonstrated structurally by every
delegation test: BranchManager is constructed with a token-free
:class:`FakeRemoteGitProvider` and never receives a secret.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Reuse the sibling bare-remote seeder to avoid drift (mirrors test_repo_worktree).
from test_repo_manager import _seed_bare_remote  # noqa: E402
from workspace_worker.repo.branch import (
    BranchError,
    BranchManager,
    BranchResult,
)
from workspace_worker.repo.manager import RepoManager

#: Skip real-git integration tests when ``git`` is unavailable. Unit tests use
#: the FakeRunner and never spawn git, so they always run.
requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git binary required for integration tests"
)


class FakeRunner:
    """Records every git invocation; returns a canned :class:`CompletedProcess`.

    Configure ``fail_on`` (an argv prefix) + ``error`` to raise on commands
    matching that prefix — e.g. to simulate ``git branch -D`` failing. Configure
    ``stdout_for`` (an argv prefix) + ``stdout`` to return custom stdout for
    commands matching that prefix — e.g. to make the ``branch --list`` pre-check
    report an existing branch. Unmatched commands return a zero-exit
    :class:`CompletedProcess` with empty stdout/stderr.
    """

    def __init__(
        self,
        *,
        fail_on: tuple[str, ...] | None = None,
        error: Exception | None = None,
        stdout_for: tuple[str, ...] | None = None,
        stdout: str = "",
    ) -> None:
        self.calls: list[list[str]] = []
        self._fail_on = fail_on
        self._error = error
        self._stdout_for = stdout_for
        self._stdout = stdout

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if self._fail_on is not None and tuple(cmd[: len(self._fail_on)]) == self._fail_on:
            assert self._error is not None
            raise self._error
        if self._stdout_for is not None and tuple(cmd[: len(self._stdout_for)]) == self._stdout_for:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=self._stdout, stderr="")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")


class FakeRemoteGitProvider:
    """Token-free, network-free stand-in for the Git Provider Adapter.

    Records every ``delete_ref`` delegation so BranchManager's remote-delete
    path (and the ``branch -> heads/<branch>`` mapping) can be asserted without
    a real adapter or HTTP. Holds NO token — BranchManager never sees one
    (ID-035 / AC6).
    """

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.delete_calls: list[tuple[str, str, str]] = []
        self._fail_with = fail_with

    def delete_ref(self, owner: str, repo: str, ref: str) -> None:
        self.delete_calls.append((owner, repo, ref))
        if self._fail_with is not None:
            raise self._fail_with


def _git(args: list[str]) -> str:
    """Run git, return stripped stdout (used to inspect on-disk state)."""
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------------
# Unit — create_branch: command shape, default/explicit ref, refuse-to-clobber
# ---------------------------------------------------------------------------


def test_create_branch_issues_precheck_then_branch_command(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    mgr = BranchManager(repo, runner=runner)

    result = mgr.create_branch("feat")

    assert result == BranchResult(name="feat", ref="HEAD")
    # Refuse-to-clobber pre-check runs first, then the create at default HEAD.
    assert runner.calls == [
        ["git", "-C", str(repo), "branch", "--list", "feat"],
        ["git", "-C", str(repo), "branch", "feat", "HEAD"],
    ]


def test_create_branch_with_explicit_ref(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    mgr = BranchManager(repo, runner=runner)

    result = mgr.create_branch("feat", ref="deadbeef")

    assert result == BranchResult(name="feat", ref="deadbeef")
    assert runner.calls[-1] == ["git", "-C", str(repo), "branch", "feat", "deadbeef"]


def test_create_branch_refuses_to_clobber_existing(tmp_path: Path) -> None:
    # The pre-check reports the branch already exists (non-empty listing) —
    # create_branch must bail with BranchError and NOT issue the create.
    repo = tmp_path / "repo"
    runner = FakeRunner(
        stdout_for=("git", "-C", str(repo), "branch", "--list"),
        stdout="  feat\n",
    )
    mgr = BranchManager(repo, runner=runner)

    with pytest.raises(BranchError, match="already exists"):
        mgr.create_branch("feat")

    # Exactly one call — the pre-check. No destructive `git branch <name> <ref>`.
    assert runner.calls == [["git", "-C", str(repo), "branch", "--list", "feat"]]


def test_create_branch_wraps_git_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=128, cmd=["git", "branch"], stderr="not a git repository"
    )
    runner = FakeRunner(fail_on=("git", "-C", str(repo), "branch", "feat"), error=err)
    mgr = BranchManager(repo, runner=runner)

    with pytest.raises(BranchError, match="failed to create branch") as exc_info:
        mgr.create_branch("feat")

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)
    assert "not a git repository" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Unit — name sanitization (option injection + path escape)
# ---------------------------------------------------------------------------


def test_create_branch_rejects_option_like_name(tmp_path: Path) -> None:
    runner = FakeRunner()
    mgr = BranchManager(tmp_path / "repo", runner=runner)

    with pytest.raises(BranchError, match="option-like"):
        mgr.create_branch("--force")

    assert runner.calls == []  # bailed before any git call


def test_create_branch_rejects_degenerate_name(tmp_path: Path) -> None:
    runner = FakeRunner()
    mgr = BranchManager(tmp_path / "repo", runner=runner)

    with pytest.raises(BranchError, match="unsafe component"):
        mgr.create_branch("..")

    assert runner.calls == []  # bailed before any git call


# ---------------------------------------------------------------------------
# Unit — checkout
# ---------------------------------------------------------------------------


def test_checkout_issues_checkout_command(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    mgr = BranchManager(repo, runner=runner)

    mgr.checkout("feat")

    assert runner.calls == [["git", "-C", str(repo), "checkout", "feat"]]


def test_checkout_wraps_git_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=1, cmd=["git", "checkout"], stderr="pathspec 'nope' did not match"
    )
    runner = FakeRunner(fail_on=("git", "-C", str(repo), "checkout"), error=err)
    mgr = BranchManager(repo, runner=runner)

    with pytest.raises(BranchError, match="failed to checkout branch") as exc_info:
        mgr.checkout("nope")

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)


# ---------------------------------------------------------------------------
# Unit — delete: local-only shape, idempotency, error wrapping
# ---------------------------------------------------------------------------


def test_delete_local_only_issues_branch_d(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    mgr = BranchManager(repo, runner=runner)  # remote=None

    mgr.delete("feat")

    assert runner.calls == [["git", "-C", str(repo), "branch", "-D", "feat"]]


def test_delete_is_idempotent_on_already_gone(tmp_path: Path) -> None:
    # git reports "error: branch '<name>' not found" for a non-existent branch
    # — the intended terminal state of delete(); it must be swallowed (verified
    # against real git: exit 1, that exact stderr marker).
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=1, cmd=["git", "branch", "-D"], stderr="error: branch 'feat' not found"
    )
    runner = FakeRunner(fail_on=("git", "-C", str(repo), "branch", "-D"), error=err)
    mgr = BranchManager(repo, runner=runner)

    mgr.delete("feat")  # must NOT raise

    assert [c[-1] for c in runner.calls] == ["feat"]


def test_delete_wraps_other_git_failure_with_chained_cause(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=128, cmd=["git", "branch", "-D"], stderr="fatal: not a git repository"
    )
    runner = FakeRunner(fail_on=("git", "-C", str(repo), "branch", "-D"), error=err)
    mgr = BranchManager(repo, runner=runner)

    with pytest.raises(BranchError, match="failed to delete branch") as exc_info:
        mgr.delete("feat")

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)
    assert "not a git repository" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Unit — delete: remote delegation, keep_remote, owner/repo requirement (R1)
# ---------------------------------------------------------------------------


def test_delete_with_remote_delegates_delete_ref(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    fake_remote = FakeRemoteGitProvider()
    mgr = BranchManager(repo, runner=runner, remote=fake_remote)

    mgr.delete("feat", owner="arconta", repo="sfp")

    # Local -D issued AND remote delete delegated with branch -> heads/<branch>.
    assert ["git", "-C", str(repo), "branch", "-D", "feat"] in runner.calls
    assert fake_remote.delete_calls == [("arconta", "sfp", "heads/feat")]
    assert len(fake_remote.delete_calls) == 1


def test_delete_keep_remote_suppresses_remote_call(tmp_path: Path) -> None:
    runner = FakeRunner()
    fake_remote = FakeRemoteGitProvider()
    mgr = BranchManager(tmp_path / "repo", runner=runner, remote=fake_remote)

    mgr.delete("feat", keep_remote=True, owner="arconta", repo="sfp")

    assert fake_remote.delete_calls == []  # remote NOT touched
    # Local delete still ran.
    assert any(c[4:] == ["-D", "feat"] for c in runner.calls)


def test_delete_with_no_remote_issues_no_remote_call(tmp_path: Path) -> None:
    runner = FakeRunner()
    fake_remote = FakeRemoteGitProvider()
    mgr = BranchManager(tmp_path / "repo", runner=runner)  # remote=None

    mgr.delete("feat")

    assert fake_remote.delete_calls == []  # no remote object was even held
    assert runner.calls == [["git", "-C", str(tmp_path / "repo"), "branch", "-D", "feat"]]


def test_delete_raises_value_error_when_owner_repo_missing(tmp_path: Path) -> None:
    # R1 resolution: a remote delete is requested (remote set, keep_remote=False)
    # but owner/repo are not supplied -> ValueError before any remote call.
    runner = FakeRunner()
    fake_remote = FakeRemoteGitProvider()
    mgr = BranchManager(tmp_path / "repo", runner=runner, remote=fake_remote)

    with pytest.raises(ValueError, match="owner and repo"):
        mgr.delete("feat")  # remote set, keep_remote defaults False, no owner/repo

    assert fake_remote.delete_calls == []  # ValueError pre-empted the delegation


# ---------------------------------------------------------------------------
# Unit — branch-name derivation (AC7)
# ---------------------------------------------------------------------------


def test_branch_name_derivation() -> None:
    assert BranchManager.branch_name(57, "Repo-Branch-Lifecycle") == "sfp-57-repo-branch-lifecycle"
    # ticket_number may be int or str; slug is case-folded.
    assert BranchManager.branch_name("33", "Reviewer-Schema") == "sfp-33-reviewer-schema"


# ---------------------------------------------------------------------------
# Integration — real git against a local bare remote cloned via RepoManager
# ---------------------------------------------------------------------------


def _clone_remote(tmp_path: Path) -> Path:
    """Seed a bare remote and clone it via the real RepoManager; return clone path."""
    remote = _seed_bare_remote(tmp_path / "remote.git")
    shutil.rmtree(tmp_path / "seed-work")  # tidy the seeding scaffold
    dest = tmp_path / "checkout"
    RepoManager("").clone(f"file://{remote}", dest)  # no token for file://
    return dest


@requires_git
def test_integration_create_branch_creates_real_branch(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = BranchManager(clone)

    result = mgr.create_branch("feat", ref="HEAD")

    assert result == BranchResult(name="feat", ref="HEAD")
    assert "feat" in _git(["-C", str(clone), "branch", "--list"])


@requires_git
def test_integration_checkout_switches_head(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = BranchManager(clone)
    mgr.create_branch("feat", ref="HEAD")

    mgr.checkout("feat")

    assert _git(["-C", str(clone), "rev-parse", "--abbrev-ref", "HEAD"]) == "feat"


@requires_git
def test_integration_delete_removes_branch(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = BranchManager(clone)
    mgr.create_branch("feat", ref="HEAD")

    mgr.delete("feat")

    assert "feat" not in _git(["-C", str(clone), "branch", "--list"])


@requires_git
def test_integration_delete_is_idempotent(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = BranchManager(clone)
    mgr.create_branch("feat", ref="HEAD")

    mgr.delete("feat")
    mgr.delete("feat")  # already gone — must not raise

    assert "feat" not in _git(["-C", str(clone), "branch", "--list"])
