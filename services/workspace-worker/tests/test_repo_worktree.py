"""Tests for :mod:`workspace_worker.repo.worktree` — the worktree lifecycle (SFP-56).

Two layers, mirroring ``test_repo_manager.py``:

* **Unit** tests inject a fake runner to assert the exact ``git`` argv (the
  ``worktree add <path> <ref>`` create command — path THEN ref, opaque ref;
  ``--force`` remove + ``prune``) and error surfacing/wrapping — no real git.
* **Integration** tests exercise the real ``git`` binary against a local bare
  remote cloned via the real :class:`RepoManager` (reusing
  ``_seed_bare_remote`` from the sibling test module) so the subprocess
  worktree operations are validated end-to-end: create yields an existing
  checkout, remove cleans it, per-job isolation holds, and errors surface.

Scope guard: ``ref`` is opaque to this slice — a unit test asserts no recorded
git invocation contains ``branch`` (branch lifecycle is SFP-57 / ID-025).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Reuse the sibling bare-remote seeder to avoid drift (PRSpec implementation
# note: prefer importing over copying).
from test_repo_manager import _seed_bare_remote  # noqa: E402
from workspace_worker.repo.manager import RepoManager
from workspace_worker.repo.worktree import (
    WorktreeError,
    WorktreeManager,
    WorktreeResult,
)

#: Skip real-git integration tests when ``git`` is unavailable (risk: the test
#: environment lacks git or a version without worktree support). Unit tests use
#: the FakeRunner and never spawn git, so they always run.
requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git binary required for integration tests"
)


class FakeRunner:
    """Records every git invocation; returns a canned :class:`CompletedProcess`.

    Configure ``fail_on`` (an argv prefix) + ``error`` to raise on commands
    matching that prefix — e.g. to simulate ``git worktree add`` or ``prune``
    failing. Unmatched commands return a zero-exit :class:`CompletedProcess`.
    """

    def __init__(
        self,
        *,
        fail_on: tuple[str, ...] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._fail_on = fail_on
        self._error = error

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if self._fail_on is not None and tuple(cmd[: len(self._fail_on)]) == self._fail_on:
            assert self._error is not None
            raise self._error
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")


def _git(args: list[str]) -> str:
    """Run git, return stripped stdout (used to inspect on-disk state)."""
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------------
# Unit — add: command shape (path THEN ref), opaque ref, no branch command
# ---------------------------------------------------------------------------


def test_add_runs_worktree_add_with_path_then_ref(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    base = tmp_path / "wt"
    mgr = WorktreeManager(repo, runner=runner)

    result = mgr.add("job-1", "deadbeef", base)

    assert result == WorktreeResult(path=base / "job-1", job_id="job-1")
    # Exactly one git call: `git -C <repo> worktree add <path> <ref>` — path
    # BEFORE ref, ref passed verbatim as the last argv element.
    assert runner.calls == [
        ["git", "-C", str(repo), "worktree", "add", str(base / "job-1"), "deadbeef"]
    ]


def test_add_ref_is_opaque_and_no_branch_command_is_issued(tmp_path: Path) -> None:
    # Anti-scope-creep guard vs SFP-57: ref is opaque, no `git branch`/`-b`/
    # `checkout` is ever issued. ref passes through as the last argv element.
    runner = FakeRunner()
    mgr = WorktreeManager(tmp_path / "repo", runner=runner)

    mgr.add("job-1", "some/opaque-ref", tmp_path / "wt")

    assert runner.calls[0][-1] == "some/opaque-ref"  # verbatim, last element
    # No argv token is "branch" — this slice never manipulates branches.
    assert "branch" not in runner.calls[0]


def test_add_does_not_pre_create_base_dir(tmp_path: Path) -> None:
    # base_dir need not pre-exist and is NOT mkdir'd by add(); git worktree add
    # creates parent dirs itself. FakeRunner does not run real git, so base_dir
    # must remain absent after the call.
    runner = FakeRunner()
    base = tmp_path / "wt"
    assert not base.exists()

    mgr = WorktreeManager(tmp_path / "repo", runner=runner)
    mgr.add("job-1", "deadbeef", base)

    assert not base.exists()  # add() did not pre-create it
    assert len(runner.calls) == 1  # only the worktree-add call, no mkdir/setup


def test_add_distinct_paths_per_job(tmp_path: Path) -> None:
    # Per-job isolation (ID-033): distinct job_ids produce distinct paths.
    runner = FakeRunner()
    mgr = WorktreeManager(tmp_path / "repo", runner=runner)
    base = tmp_path / "wt"

    a = mgr.add("job-a", "deadbeef", base)
    b = mgr.add("job-b", "deadbeef", base)

    assert a.path != b.path
    assert a.path == base / "job-a"
    assert b.path == base / "job-b"


# ---------------------------------------------------------------------------
# Unit — add: refuse-to-clobber, path sanitization, error wrapping
# ---------------------------------------------------------------------------


def test_add_refuses_to_clobber_existing_path(tmp_path: Path) -> None:
    runner = FakeRunner()
    base = tmp_path / "wt"
    (base / "job-1").mkdir(parents=True)  # the per-job path already exists
    mgr = WorktreeManager(tmp_path / "repo", runner=runner)

    with pytest.raises(WorktreeError, match="already exists"):
        mgr.add("job-1", "deadbeef", base)

    assert runner.calls == []  # bailed before any git call


def test_add_sanitizes_job_id_path_escape(tmp_path: Path) -> None:
    # A hostile job_id carrying "../" must not break out of base_dir: only the
    # final path component is used.
    runner = FakeRunner()
    base = tmp_path / "wt"
    mgr = WorktreeManager(tmp_path / "repo", runner=runner)

    result = mgr.add("../evil", "deadbeef", base)

    # Path is confined under base_dir: <base>/evil, not <base>/../evil.
    assert result.path == base / "evil"
    assert str(base / "evil") in " ".join(runner.calls[0])


def test_add_rejects_job_id_reducing_to_empty(tmp_path: Path) -> None:
    runner = FakeRunner()
    mgr = WorktreeManager(tmp_path / "repo", runner=runner)

    # ".." is a degenerate component PurePath.name leaves intact — rejected as
    # an unsafe (parent-reference) path component.
    with pytest.raises(WorktreeError, match="unsafe path component"):
        mgr.add("..", "deadbeef", tmp_path / "wt")

    assert runner.calls == []  # bailed before any git call


def test_add_surfaces_git_failure_as_worktree_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "worktree", "add"],
        stderr="not a git repository",
    )
    runner = FakeRunner(fail_on=("git", "-C", str(repo), "worktree", "add"), error=err)
    mgr = WorktreeManager(repo, runner=runner)

    with pytest.raises(WorktreeError, match="failed to create worktree") as exc_info:
        mgr.add("job-1", "deadbeef", tmp_path / "wt")

    # No raw subprocess exception escapes; the original is chained as __cause__.
    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)
    assert "not a git repository" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Unit — remove: command shape (force remove + prune), idempotency, wrapping
# ---------------------------------------------------------------------------


def test_remove_runs_force_remove_then_prune(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    mgr = WorktreeManager(repo, runner=runner)
    wt_path = tmp_path / "wt" / "job-1"

    mgr.remove(wt_path)

    assert runner.calls == [
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(wt_path)],
        ["git", "-C", str(repo), "worktree", "prune"],
    ]


def test_remove_accepts_str_path(tmp_path: Path) -> None:
    runner = FakeRunner()
    mgr = WorktreeManager(tmp_path / "repo", runner=runner)

    mgr.remove(str(tmp_path / "wt" / "job-1"))  # str accepted, not just Path

    assert "remove" in runner.calls[0]


def test_remove_is_idempotent_on_already_gone(tmp_path: Path) -> None:
    # git reports "is not a working tree" for an already-removed/unknown worktree
    # — the intended terminal state of remove(); it must be swallowed, and prune
    # still runs.
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "worktree", "remove"],
        stderr="fatal: '/x' is not a working tree",
    )
    runner = FakeRunner(fail_on=("git", "-C", str(repo), "worktree", "remove"), error=err)
    mgr = WorktreeManager(repo, runner=runner)

    mgr.remove(tmp_path / "wt" / "gone")  # must NOT raise

    # Both remove (swallowed) and prune (ran) were attempted.
    assert [c[-1] for c in runner.calls] == [str(tmp_path / "wt" / "gone"), "prune"]


def test_remove_wraps_other_git_failure_with_chained_cause(tmp_path: Path) -> None:
    # Any git failure that is NOT the already-gone marker wraps to WorktreeError.
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "worktree", "remove"],
        stderr="fatal: not a git repository",
    )
    runner = FakeRunner(fail_on=("git", "-C", str(repo), "worktree", "remove"), error=err)
    mgr = WorktreeManager(repo, runner=runner)

    with pytest.raises(WorktreeError, match="failed to remove worktree") as exc_info:
        mgr.remove(tmp_path / "wt" / "job-1")

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)
    assert "not a git repository" in str(exc_info.value)


def test_remove_swallows_prune_failure(tmp_path: Path) -> None:
    # remove succeeds; prune fails — prune is best-effort, so remove must not
    # raise and must not mask anything.
    repo = tmp_path / "repo"
    prune_err = subprocess.CalledProcessError(
        returncode=1, cmd=["git", "worktree", "prune"], stderr="prune boom"
    )
    runner = FakeRunner(fail_on=("git", "-C", str(repo), "worktree", "prune"), error=prune_err)
    mgr = WorktreeManager(repo, runner=runner)

    mgr.remove(tmp_path / "wt" / "job-1")  # must NOT raise

    assert [c[-1] for c in runner.calls] == [str(tmp_path / "wt" / "job-1"), "prune"]


# ---------------------------------------------------------------------------
# Integration — real git against a local bare remote cloned via RepoManager
# ---------------------------------------------------------------------------


def _clone_remote(tmp_path: Path) -> Path:
    """Seed a bare remote and clone it via the real RepoManager; return clone path."""
    import shutil as _shutil  # noqa: PLC0415 — local import keeps top-level clean

    remote = _seed_bare_remote(tmp_path / "remote.git")
    _shutil.rmtree(tmp_path / "seed-work")  # tidy the seeding scaffold
    dest = tmp_path / "checkout"
    RepoManager("").clone(f"file://{remote}", dest)  # no token for file://
    return dest


def _head_sha(repo: Path) -> str:
    """Opaque ref for worktree add: the clone's HEAD sha (detached checkout
    avoids git's 'branch already checked out' refusal)."""
    return _git(["-C", str(repo), "rev-parse", "HEAD"])


@requires_git
def test_integration_add_creates_worktree_on_disk(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = WorktreeManager(clone)
    base = tmp_path / "wt"
    ref = _head_sha(clone)

    result = mgr.add("job-1", ref, base)

    assert result == WorktreeResult(path=base / "job-1", job_id="job-1")
    assert result.path.is_dir()  # the checkout exists on disk
    assert (result.path / "README").read_text() == "seed\n"  # content checked out
    # Registered with the main repo's worktree list.
    assert str(result.path) in _git(["-C", str(clone), "worktree", "list"])


@requires_git
def test_integration_add_distinct_paths_per_job(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = WorktreeManager(clone)
    base = tmp_path / "wt"
    ref = _head_sha(clone)

    a = mgr.add("job-a", ref, base)
    b = mgr.add("job-b", ref, base)

    assert a.path != b.path and a.path.is_dir() and b.path.is_dir()
    listing = _git(["-C", str(clone), "worktree", "list"])
    assert str(a.path) in listing and str(b.path) in listing


@requires_git
def test_integration_remove_cleans_worktree_and_prunes(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = WorktreeManager(clone)
    result = mgr.add("job-1", _head_sha(clone), tmp_path / "wt")

    mgr.remove(result.path)

    assert not result.path.exists()  # checkout gone
    # Stale worktree metadata pruned from the main repo.
    assert str(result.path) not in _git(["-C", str(clone), "worktree", "list"])


@requires_git
def test_integration_remove_force_tolerates_dirty_worktree(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = WorktreeManager(clone)
    result = mgr.add("job-1", _head_sha(clone), tmp_path / "wt")
    (result.path / "scratch.txt").write_text("dirty\n")  # untracked file

    mgr.remove(result.path)  # default --force tolerates the dirty tree

    assert not result.path.exists()


@requires_git
def test_integration_remove_is_idempotent(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = WorktreeManager(clone)
    result = mgr.add("job-1", _head_sha(clone), tmp_path / "wt")

    mgr.remove(result.path)
    mgr.remove(result.path)  # already gone — must not raise

    assert not result.path.exists()


@requires_git
def test_integration_add_surfaces_error_for_unknown_ref(tmp_path: Path) -> None:
    clone = _clone_remote(tmp_path)
    mgr = WorktreeManager(clone)

    with pytest.raises(WorktreeError, match="failed to create worktree"):
        mgr.add("job-1", "no-such-ref-xyz", tmp_path / "wt")
