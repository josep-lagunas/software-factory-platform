"""Repository Manager — worktree lifecycle (SFP-56 / ID-033).

Manages *ephemeral, per-job* git worktrees for the Workspace Worker. Per the
SFP execution contract (ID-033), each job works in its own isolated git
worktree; worktrees are **never shared across jobs**. This module owns the
create/remove lifecycle of those worktrees on top of a locally-cloned
repository (produced by :mod:`workspace_worker.repo.manager`, SFP-38).

Lifecycle — one worktree per job, removed on completion:

* :meth:`WorktreeManager.create` materialises a fresh worktree for ``job_id``
  at ``<base_dir>/<job_id>`` on a newly-created branch (per-job isolation:
  distinct branch + distinct checkout), and returns a :class:`Worktree`
  describing it.
* :meth:`WorktreeManager.remove` tears that worktree down again
  (``git worktree remove`` + ``prune``), surfacing any failure.

Implementation is plain ``subprocess`` git (real git, not a vendor SDK). The
git executor is injectable (``runner``) so unit tests can assert the exact
argv without spawning a process; integration tests drive the real ``git``
binary end-to-end against a local repo.

Scope — this is the *worktree* slice only. Cloning the repository (SFP-38),
fetch/sync, and branch-merge live in sibling modules. This module performs no
network I/O and handles no credentials.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Worktree", "WorktreeManager", "WorktreeManagerError"]

#: Signature of the injectable git runner (defaults to :func:`subprocess.run`).
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class WorktreeManagerError(RuntimeError):
    """Raised when a worktree create/remove operation fails."""


@dataclass(frozen=True, slots=True)
class Worktree:
    """A created git worktree owned by a single job.

    Attributes:
        job_id: Identifier of the owning job. Names the worktree directory
            (``<base_dir>/<job_id>``) and is what guarantees one-worktree-per-job
            isolation (ID-033).
        path: Filesystem path of the worktree checkout (``<base_dir>/<job_id>``).
        branch: The branch checked out in the worktree.
    """

    job_id: str
    path: Path
    branch: str


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # capture_output so stderr is available for error messages without leaking
    # to the console/logs unstructured.
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


class WorktreeManager:
    """Creates and removes ephemeral per-job git worktrees (ID-033).

    Each worktree is a distinct checkout of the repository on its own branch,
    living under a caller-supplied base directory. One job maps to exactly one
    worktree; worktrees are never shared across jobs.

    Args:
        repo_path: Path of the main repository (the clone produced by
            :class:`~workspace_worker.repo.manager.RepoManager`). Must itself be
            a git repository — worktrees are linked off its ``.git``.
        runner: Injectable git executor. Defaults to ``subprocess.run`` with
            ``check=True`` and captured output. Each call receives the full
            ``git`` argv; tests inject a fake to assert commands without
            spawning real git.
    """

    def __init__(self, repo_path: Path, *, runner: Runner | None = None) -> None:
        self._repo_path = repo_path
        self._runner: Runner = runner or _default_runner

    def create(self, job_id: str, base_dir: Path, branch: str) -> Worktree:
        """Create a fresh, isolated worktree for ``job_id``.

        Materialises ``<base_dir>/<job_id>`` as a new git worktree of the main
        repository on a newly-created ``branch`` (off the repository's current
        ``HEAD``). A new branch per job is what keeps jobs isolated: each job
        gets its own branch and its own checkout, so concurrent jobs never
        collide (ID-033).

        ``base_dir`` is created if it does not already exist.

        Args:
            job_id: Identifier of the owning job. Determines the worktree path
                (``<base_dir>/<job_id>``) and must be unique per concurrent job.
            base_dir: Parent directory under which the per-job worktree lives.
                A temporary base dir keeps all of a run's worktrees in one place
                that is easy to tear down.
            branch: Name of the new branch to create and check out in the
                worktree.

        Returns:
            The :class:`Worktree` (its ``path`` is the worktree directory).

        Raises:
            WorktreeManagerError: if ``git worktree add`` fails (e.g. the main
                repo is not a git repository, the branch already exists, or the
                worktree path is already in use). The git stderr is surfaced.
        """
        base_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = base_dir / job_id

        # `-b branch` creates a NEW branch off HEAD and checks it out in the
        # worktree — the per-job isolation guarantee (distinct branch + tree).
        try:
            self._runner(
                [
                    "git",
                    "-C",
                    str(self._repo_path),
                    "worktree",
                    "add",
                    "-b",
                    branch,
                    str(worktree_path),
                ]
            )
        except subprocess.CalledProcessError as exc:
            raise WorktreeManagerError(
                f"failed to create worktree for job {job_id!r} "
                f"at {worktree_path}: {exc.stderr or exc}"
            ) from exc

        return Worktree(job_id=job_id, path=worktree_path, branch=branch)

    def remove(self, worktree: Worktree, *, force: bool = True) -> None:
        """Remove a worktree and its administrative metadata.

        Runs ``git worktree remove`` then ``git worktree prune`` so neither the
        checkout nor dangling worktree metadata is left behind.

        Args:
            worktree: The worktree to remove (as returned by :meth:`create`).
            force: If ``True`` (default), pass ``--force`` so removal tolerates
                a dirty worktree (untracked/modified files) — best-effort
                cleanup of a job's scratch tree. If ``False``, removal fails on
                a dirty tree.

        Raises:
            WorktreeManagerError: if ``git worktree remove`` fails (e.g. the
                path is not a registered worktree). Pruning still runs and is
                itself best-effort — a prune failure is swallowed rather than
                masking the original error.
        """
        cmd: list[str] = ["git", "-C", str(self._repo_path), "worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(worktree.path))

        try:
            self._runner(cmd)
        except subprocess.CalledProcessError as exc:
            raise WorktreeManagerError(
                f"failed to remove worktree for job {worktree.job_id!r} "
                f"at {worktree.path}: {exc.stderr or exc}"
            ) from exc
        finally:
            # Best-effort: prune stale worktree metadata regardless of outcome.
            # A prune failure must never mask the remove error above.
            try:
                self._runner(["git", "-C", str(self._repo_path), "worktree", "prune"])
            except subprocess.CalledProcessError:
                pass
