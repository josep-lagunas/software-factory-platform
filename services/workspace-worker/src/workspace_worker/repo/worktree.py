"""Repository Manager — worktree lifecycle (SFP-56 / ID-033).

Manages *ephemeral, per-job* git worktrees for the Workspace Worker. Per the SFP
execution contract (ID-033), each job works in its own isolated git worktree;
worktrees are **never shared across jobs**. This module owns the create/remove
*directory* lifecycle of those worktrees on top of a locally-cloned repository
(produced by :mod:`workspace_worker.repo.manager`, SFP-55).

Lifecycle — one worktree per job, removed on completion:

* :meth:`WorktreeManager.add` materialises a fresh worktree for ``job_id`` at
  ``<base_dir>/<job_id>`` by checking out the caller-supplied ``ref`` (opaque —
  a branch, tag, or commit), and returns a :class:`WorktreeResult` describing
  it. A distinct path per ``job_id`` is what guarantees one-worktree-per-job
  isolation (ID-033).
* :meth:`WorktreeManager.remove` tears that worktree down again
  (``git worktree remove --force`` + ``shutil.rmtree`` + ``git worktree
  prune``) and is idempotent on an already-removed worktree.

Scope — this is the *worktree directory* slice only. Cloning the repository
(SFP-55, Done), fetch/sync, and **branch creation/deletion** (SFP-57 / ID-025)
are out of scope. ``ref`` is treated as opaque caller input and is passed to
``git worktree add`` verbatim; this module never creates, renames, or deletes a
branch. This module performs no network I/O and handles no credentials — the
local repo's ``origin`` is already token-free (SFP-55).

Implementation is plain ``subprocess`` git (real git, not a vendor SDK). The git
executor is injectable (``runner``) so unit tests can assert the exact argv
without spawning a process; integration tests drive the real ``git`` binary
end-to-end against a local repo.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePath

__all__ = ["WorktreeError", "WorktreeManager", "WorktreeResult"]

#: Signature of the injectable git runner (defaults to :func:`subprocess.run`).
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

#: git's invariant stderr marker when asked to remove a path that is not a
#: registered worktree — whether it was already removed, was never one, or no
#: longer exists on disk. Emitted for every "already gone" variant (verified
#: against real git), so it is the stable signal for remove()'s idempotency.
_ALREADY_GONE_MARKER = "is not a working tree"


class WorktreeError(RuntimeError):
    """Raised when a worktree create/remove operation fails.

    The failing git command context (stderr) is surfaced in the message. No
    secret ever flows through this slice — the local repo's ``origin`` is
    already token-free (SFP-55) — so no redaction is required here.
    """


@dataclass(frozen=True, slots=True)
class WorktreeResult:
    """Outcome of :meth:`WorktreeManager.add`.

    Attributes:
        path: Filesystem path of the worktree checkout (``<base_dir>/<job_id>``).
        job_id: Identifier of the owning job. Names the worktree directory and
            is what guarantees one-worktree-per-job isolation (ID-033).
    """

    path: Path
    job_id: str


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # capture_output so stderr is available for error messages without leaking
    # to the console/logs unstructured.
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _is_already_gone(exc: subprocess.CalledProcessError) -> bool:
    """True if ``exc`` is git reporting the worktree is no longer registered.

    ``git worktree remove`` emits ``fatal: '<path>' is not a working tree`` for
    every already-gone variant — already removed, never a worktree, or path
    absent. Reaching that state during :meth:`WorktreeManager.remove` is the
    intended terminal state, not a failure, so the caller swallows only this
    case (see :data:`_ALREADY_GONE_MARKER`).
    """
    return _ALREADY_GONE_MARKER in (exc.stderr or "").lower()


def _sanitize_job_id(job_id: str) -> str:
    """Return a path-escape-safe single component derived from ``job_id``.

    The worktree lives at ``<base_dir>/<job_id>``; ``job_id`` is caller-supplied
    and could carry a path-escape payload (e.g. ``"../etc"``). Taking only the
    final path component (:attr:`PurePath.name`) strips every leading directory
    so the result cannot break out of ``base_dir``::

        PurePath("job-7").name   -> "job-7"
        PurePath("../etc").name  -> "etc"
        PurePath("a/b").name     -> "b"

    The degenerate components ``PurePath.name`` leaves intact — ``"."``,
    ``".."``, and ``""`` — are rejected: they are self/parent references (or no
    component at all) and would otherwise target outside the per-job slot.
    """
    name = PurePath(job_id).name
    if name in ("", ".", ".."):
        raise WorktreeError(f"job_id reduces to an unsafe path component: {job_id!r}")
    return name


class WorktreeManager:
    """Creates and removes ephemeral per-job git worktrees (ID-033).

    Each worktree is a distinct checkout of the repository, living under a
    caller-supplied base directory. One job maps to exactly one worktree;
    worktrees are never shared across jobs.

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

    def add(self, job_id: str, ref: str, base_dir: Path) -> WorktreeResult:
        """Create a fresh, isolated worktree for ``job_id``.

        Materialises ``<base_dir>/<job_id>`` as a new git worktree of the main
        repository, checking out the caller-supplied ``ref`` (opaque — a branch,
        tag, or commit) into it. ``base_dir`` need not pre-exist: ``git worktree
        add`` creates the parent directories. A distinct path per ``job_id`` is
        what keeps jobs isolated (ID-033).

        ``ref`` is passed to git verbatim as the last argv element; this module
        issues no ``git branch``/``git checkout`` and never creates or deletes a
        branch (that is SFP-57 / ID-025).

        Args:
            job_id: Identifier of the owning job. Determines the worktree path
                (``<base_dir>/<job_id>``) and must be unique per concurrent job.
                Sanitised to a single path component to defuse path escape.
            ref: Opaque git ref to check out in the worktree (e.g. a branch,
                tag, or commit SHA). Not validated as a branch.
            base_dir: Parent directory under which the per-job worktree lives.
                Created on demand by ``git worktree add``.

        Returns:
            The :class:`WorktreeResult` (its ``path`` is the worktree directory).

        Raises:
            WorktreeError: if ``<base_dir>/<job_id>`` already exists (refuses to
                clobber — no git call is made), if ``job_id`` reduces to no path
                component, or if ``git worktree add`` fails (e.g. the main repo
                is not a git repository, or ``ref`` is unknown). The git stderr
                is surfaced.
        """
        worktree_path = base_dir / _sanitize_job_id(job_id)

        # Refuse to clobber an existing per-job path — mirror manager.py's
        # refuse-to-clobber discipline: surface the state explicitly rather than
        # letting `git worktree add` produce a confusing nested error. No git
        # call is made in this branch.
        if worktree_path.exists():
            raise WorktreeError(f"worktree path already exists: {worktree_path}")

        # `git worktree add <path> <ref>` checks out `ref` into the worktree and
        # creates any missing parent directories (base_dir need not pre-exist).
        # argv order is path THEN ref; `ref` is opaque (no `-b`, no branch ops).
        try:
            self._runner(
                [
                    "git",
                    "-C",
                    str(self._repo_path),
                    "worktree",
                    "add",
                    str(worktree_path),
                    ref,
                ]
            )
        except subprocess.CalledProcessError as exc:
            raise WorktreeError(
                f"failed to create worktree for job {job_id!r} "
                f"at {worktree_path}: {exc.stderr or exc}"
            ) from exc

        return WorktreeResult(path=worktree_path, job_id=job_id)

    def remove(self, worktree_path: str | Path) -> None:
        """Remove a worktree, its on-disk checkout, and stale metadata.

        Runs ``git worktree remove --force``, then ``shutil.rmtree`` (ignoring
        errors) so the checkout is gone even if git's bookkeeping is stale, then
        ``git worktree prune`` so dangling administrative entries are reclaimed.

        Idempotent: a second call (or one against a worktree already torn down
        out-of-band) reaches git as ``fatal: '<path>' is not a working tree``.
        That already-gone state is the intended terminal state of ``remove()``,
        so it is swallowed; every *other* git failure wraps to
        :class:`WorktreeError` with the original exception chained as
        ``__cause__``. ``rmtree`` and ``prune`` still run on the swallowed path
        so the directory and metadata are cleaned regardless.

        Args:
            worktree_path: Filesystem path of the worktree to remove (the
                ``path`` of the :class:`WorktreeResult` returned by :meth:`add`).

        Raises:
            WorktreeError: if ``git worktree remove`` fails for any reason other
                than the already-gone state. The git stderr is surfaced and the
                original :class:`subprocess.CalledProcessError` is chained.
        """
        path = Path(worktree_path)
        try:
            self._runner(
                [
                    "git",
                    "-C",
                    str(self._repo_path),
                    "worktree",
                    "remove",
                    "--force",
                    str(path),
                ]
            )
        except subprocess.CalledProcessError as exc:
            # Swallow ONLY the already-gone case: git was asked to remove a
            # worktree it no longer tracks ("is not a working tree"), which is
            # exactly the end state remove() aims for. Any other git failure is
            # real (bad repo path, permissions, ...) and must surface.
            if not _is_already_gone(exc):
                raise WorktreeError(
                    f"failed to remove worktree at {path}: {exc.stderr or exc}"
                ) from exc

        # Always attempt to delete the checkout dir — git's view may be stale, so
        # rmtree is the source of truth for "directory is gone". ignore_errors
        # makes this a no-op when there is nothing to delete.
        shutil.rmtree(path, ignore_errors=True)

        # Best-effort: prune reclaims stale administrative metadata only. A
        # failure here must never mask the outcome of the removal above.
        try:
            self._runner(["git", "-C", str(self._repo_path), "worktree", "prune"])
        except subprocess.CalledProcessError:
            pass
