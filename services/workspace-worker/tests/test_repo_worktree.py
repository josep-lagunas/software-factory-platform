"""Tests for :mod:`workspace_worker.repo.worktree` — the worktree lifecycle (SFP-56).

Two layers, mirroring ``test_repo_manager.py``:

* **Unit** tests inject a fake runner to assert the exact ``git`` argv (the
  ``-b <branch>`` create command, ``--force``/prune on remove) and error
  surfacing — no real git.
* **Integration** tests exercise the real ``git`` binary against a local seeded
  repository so the subprocess worktree operations are validated end-to-end:
  create yields an existing checkout, remove cleans it, per-job isolation holds,
  and errors surface.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from workspace_worker.repo.worktree import (
    Worktree,
    WorktreeManager,
    WorktreeManagerError,
)


class FakeRunner:
    """Records every git invocation; returns a canned :class:`CompletedProcess`.

    Configure ``failing_prefix`` + ``error`` to raise on commands matching a
    prefix (e.g. to simulate ``git worktree prune`` failing).
    """

    def __init__(
        self,
        *,
        failing_prefix: tuple[str, ...] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._failing_prefix = failing_prefix
        self._error = error

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if (
            self._failing_prefix is not None
            and tuple(cmd[: len(self._failing_prefix)]) == self._failing_prefix
        ):
            assert self._error is not None
            raise self._error
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# Integration scaffold — a real local repo with one commit (worktrees branch
# off HEAD, so HEAD must exist).
# ---------------------------------------------------------------------------


def _seed_repo(repo_dir: Path) -> Path:
    """Create a real non-bare git repo with one commit; return its path."""
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "t@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "tester"],
        check=True,
        capture_output=True,
    )
    (repo_dir / "README").write_text("seed\n")
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    return repo_dir


def _git(args: list[str]) -> str:
    """Run git, return stripped stdout (used to inspect on-disk state)."""
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------------
# Unit — command shape (create)
# ---------------------------------------------------------------------------


def test_create_runs_worktree_add_with_new_branch(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    mgr = WorktreeManager(repo, runner=runner)
    base = tmp_path / "wt"

    wt = mgr.create("job-1", base, "feat-1")

    assert wt == Worktree(job_id="job-1", path=base / "job-1", branch="feat-1")
    # The per-job isolation guarantee: a NEW branch is created (-b) and the
    # worktree lives at <base>/<job_id>.
    assert runner.calls == [
        ["git", "-C", str(repo), "worktree", "add", "-b", "feat-1", str(base / "job-1")]
    ]


def test_create_makes_base_dir(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    base = tmp_path / "wt"  # does not pre-exist
    assert not base.exists()

    mgr = WorktreeManager(repo, runner=runner)
    mgr.create("job-1", base, "feat-1")

    assert base.is_dir()  # create materialised the base dir itself


# ---------------------------------------------------------------------------
# Unit — command shape (remove) + prune
# ---------------------------------------------------------------------------


def test_remove_runs_force_remove_then_prune(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    mgr = WorktreeManager(repo, runner=runner)
    wt = Worktree(job_id="job-1", path=tmp_path / "wt" / "job-1", branch="feat-1")

    mgr.remove(wt)

    assert runner.calls == [
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "remove",
            "--force",
            str(wt.path),
        ],
        ["git", "-C", str(repo), "worktree", "prune"],
    ]


def test_remove_without_force_omits_force_flag(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    mgr = WorktreeManager(repo, runner=runner)
    wt = Worktree(job_id="job-1", path=tmp_path / "wt" / "job-1", branch="feat-1")

    mgr.remove(wt, force=False)

    assert runner.calls[0] == [
        "git",
        "-C",
        str(repo),
        "worktree",
        "remove",
        str(wt.path),
    ]


def test_remove_swallows_prune_failure(tmp_path: Path) -> None:
    # remove succeeds, prune fails -> remove must NOT raise (best-effort prune).
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=1, cmd=["git", "worktree", "prune"], stderr="prune boom"
    )
    runner = FakeRunner(failing_prefix=("git", "-C", str(repo), "worktree", "prune"), error=err)
    mgr = WorktreeManager(repo, runner=runner)
    wt = Worktree(job_id="job-1", path=tmp_path / "wt" / "job-1", branch="feat-1")

    mgr.remove(wt)  # must not raise

    # Both commands were attempted: remove (ok) then prune (raised, swallowed).
    assert [c[-1] for c in runner.calls] == [str(wt.path), "prune"]


# ---------------------------------------------------------------------------
# Unit — error surfacing
# ---------------------------------------------------------------------------


def test_create_surfaces_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "worktree", "add"],
        stderr="not a git repository",
    )
    runner = FakeRunner(failing_prefix=("git", "-C", str(repo), "worktree", "add"), error=err)
    mgr = WorktreeManager(repo, runner=runner)

    with pytest.raises(WorktreeManagerError, match="failed to create worktree"):
        mgr.create("job-1", tmp_path / "wt", "feat-1")


def test_remove_surfaces_error_and_still_prunes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    err = subprocess.CalledProcessError(
        returncode=1, cmd=["git", "worktree", "remove"], stderr="no such worktree"
    )
    runner = FakeRunner(failing_prefix=("git", "-C", str(repo), "worktree", "remove"), error=err)
    mgr = WorktreeManager(repo, runner=runner)
    wt = Worktree(job_id="job-1", path=tmp_path / "wt" / "job-1", branch="feat-1")

    with pytest.raises(WorktreeManagerError, match="failed to remove worktree"):
        mgr.remove(wt)

    # The finally-block prune still ran despite the remove failure.
    assert runner.calls[-1] == ["git", "-C", str(repo), "worktree", "prune"]


# ---------------------------------------------------------------------------
# Integration — real git against a local repo
# ---------------------------------------------------------------------------


def test_integration_create_yields_existing_worktree_dir(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo)
    base = tmp_path / "wt"

    wt = mgr.create("job-7", base, "feat-7")

    assert wt.path == base / "job-7"
    assert wt.path.is_dir()  # the checkout exists on disk
    # Registered with the main repo's worktree list.
    listing = _git(["-C", str(repo), "worktree", "list"])
    assert str(wt.path) in listing


def test_integration_create_checks_out_requested_branch(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo)

    wt = mgr.create("job-1", tmp_path / "wt", "feat-branch")

    assert _git(["-C", str(wt.path), "branch", "--show-current"]) == "feat-branch"
    # The branch object now exists in the main repo.
    assert "feat-branch" in _git(["-C", str(repo), "branch", "--list"])


def test_integration_per_job_isolation(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo)
    base = tmp_path / "wt"

    wt_a = mgr.create("job-a", base, "branch-a")
    wt_b = mgr.create("job-b", base, "branch-b")

    assert wt_a.path != wt_b.path  # distinct paths — never shared (ID-033)
    assert wt_a.path.is_dir() and wt_b.path.is_dir()
    listing = _git(["-C", str(repo), "worktree", "list"])
    assert str(wt_a.path) in listing and str(wt_b.path) in listing


def test_integration_remove_cleans_worktree_and_prunes(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo)
    wt = mgr.create("job-1", tmp_path / "wt", "feat-1")

    mgr.remove(wt)

    assert not wt.path.exists()  # checkout gone
    # Stale worktree metadata pruned from the main repo.
    listing = _git(["-C", str(repo), "worktree", "list"])
    assert str(wt.path) not in listing


def test_integration_remove_force_tolerates_dirty_worktree(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo)
    wt = mgr.create("job-1", tmp_path / "wt", "feat-1")
    # Dirty the worktree with an untracked file — non-force remove would refuse.
    (wt.path / "scratch.txt").write_text("dirty\n")

    mgr.remove(wt)  # default force=True -> best-effort cleanup succeeds

    assert not wt.path.exists()


def test_integration_create_surfaces_error_when_repo_path_not_a_repo(
    tmp_path: Path,
) -> None:
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()  # exists but has no .git
    mgr = WorktreeManager(not_a_repo)

    with pytest.raises(WorktreeManagerError, match="failed to create worktree"):
        mgr.create("job-1", tmp_path / "wt", "feat-1")


def test_integration_remove_surfaces_error_for_unknown_path(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo)
    ghost = Worktree(job_id="ghost", path=tmp_path / "never-created", branch="feat")

    with pytest.raises(WorktreeManagerError, match="failed to remove worktree"):
        mgr.remove(ghost)
