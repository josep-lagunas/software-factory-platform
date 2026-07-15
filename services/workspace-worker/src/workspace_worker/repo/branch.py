"""Repository Manager — branch lifecycle (SFP-57 / ID-025).

Owns the *branch* slice of the Repository Manager: LOCAL branch
create/checkout/delete via plain ``subprocess`` git (credential-free, mirroring
:mod:`workspace_worker.repo.worktree`), and REMOTE branch *deletion* delegated
to an injected :class:`~workspace_worker.repo.git.adapter.GitProviderAdapter`.

Design — local vs remote split (ID-034 / ID-035):

* **Local** branch ops are plain ``git`` subprocess calls against the locally
  cloned repository. That clone's ``origin`` is already token-free (SFP-55), so
  the local slice performs **no network I/O and handles no credentials** —
  identical to :class:`~workspace_worker.repo.worktree.WorktreeManager`.
* **Remote** deletion is delegated: the caller injects a *constructed*
  :class:`~workspace_worker.repo.git.adapter.GitProviderAdapter`
  (``remote=...``). :class:`BranchManager` **never receives, stores, or
  references a token** (ID-035) — it holds only the adapter handle and calls
  ``remote.delete_ref(owner, repo, "heads/<branch>")``. Token resolution,
  bearer auth, retry, and redaction all live in the adapter (SFP-58).

Naming — :meth:`BranchManager.branch_name` derives ``sfp-<ticket-number>-<slug>``
lowercased (e.g. ``sfp-57-repo-branch-lifecycle``). (ID-025 is the *PR-as-review-
unit* decision; it is **not** the branch-naming rule — corrected here.)

Design choice for remote delete (R1 resolution): :meth:`BranchManager.delete`
accepts optional ``owner``/``repo`` keyword arguments. They are required *only*
when a remote delete will actually occur (``remote is not None`` **and**
``keep_remote is False``); requesting a remote delete without both raises
:class:`ValueError`. This keeps the ticket-specified constructor signature
``BranchManager(repo_path, *, runner, remote)`` intact while giving the caller a
place to supply the repository coordinates at delete time.

The git executor is injectable (``runner``) so unit tests can assert the exact
argv without spawning a process; integration tests drive the real ``git`` binary
end-to-end against a local repo.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # TYPE_CHECKING only: BranchManager depends solely on the adapter's
    # `delete_ref` method (duck-typed), never on its token-bearing state — this
    # keeps the "BranchManager never references a token" guarantee (ID-035)
    # literal at runtime (zero import coupling to the adapter module).
    from workspace_worker.repo.git.adapter import GitProviderAdapter

__all__ = ["BranchError", "BranchManager", "BranchResult"]

#: Signature of the injectable git runner (defaults to :func:`subprocess.run`).
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

#: Substring of git's stderr when ``git branch -D <name>`` targets a branch that
#: is not present — whether never created or already deleted. Real git emits
#: ``error: branch '<name>' not found`` (exit 1 on stderr; verified against real
#: git), so ``"not found"`` paired with the ``"branch"`` token is the stable,
#: name-independent already-gone signal (mirrors
#: :data:`workspace_worker.repo.worktree._ALREADY_GONE_MARKER`).
_ALREADY_GONE_MARKER = "not found"


class BranchError(RuntimeError):
    """Raised when a local branch operation fails.

    The failing git command context (stderr) is surfaced in the message. No
    secret ever flows through this slice — the local repo's ``origin`` is already
    token-free (SFP-55) — so no redaction is required here.
    """


@dataclass(frozen=True, slots=True)
class BranchResult:
    """Outcome of :meth:`BranchManager.create_branch`.

    Attributes:
        name: The branch name that was created.
        ref: The ref the branch was created from (e.g. ``HEAD`` or a commit SHA).
    """

    name: str
    ref: str


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # capture_output so stderr is available for error messages without leaking
    # to the console/logs unstructured.
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _is_already_gone(exc: subprocess.CalledProcessError) -> bool:
    """True if ``exc`` is git reporting the branch no longer exists.

    ``git branch -D`` emits ``error: branch '<name>' not found`` for every
    already-gone variant — never created, or already deleted. Reaching that state
    during :meth:`BranchManager.delete` is the intended terminal state, not a
    failure, so the caller swallows only this case (see
    :data:`_ALREADY_GONE_MARKER`).
    """
    stderr = (exc.stderr or "").lower()
    return "branch" in stderr and _ALREADY_GONE_MARKER in stderr


def _sanitize_branch_name(name: str) -> str:
    """Return a shell-safe single token derived from ``name``.

    The branch name is interpolated into a ``git`` argv as a single token;
    ``name`` is caller-supplied and could carry an option payload (e.g.
    ``"--force"``) or a path-escape payload (e.g. ``"../etc"``). A leading
    ``-`` is rejected outright (defuses option injection into ``git branch``),
    then only the final path component (:attr:`PurePath.name`) is kept so the
    result cannot carry a directory. The degenerate components
    :attr:`PurePath.name` leaves intact — ``"."``, ``".."``, and ``""`` — are
    rejected::

        PurePath("sfp-57-x").name  -> "sfp-57-x"
        PurePath("../etc").name    -> "etc"
        PurePath("--force").name   -> "--force"  (rejected by the leading-`-` guard)

    Note: a multi-component name like ``feature/x`` reduces to ``x``. The
    :class:`BranchManager` targets single-component ``sfp-*`` ephemeral branches
    (the naming rule above), so this is the intended input shape.
    """
    if not name or name.startswith("-"):
        raise BranchError(f"branch name is empty or option-like: {name!r}")
    comp = PurePath(name).name
    if comp in ("", ".", ".."):
        raise BranchError(f"branch name reduces to an unsafe component: {name!r}")
    return comp


class BranchManager:
    """Local branch lifecycle + remote-delete delegation (SFP-57 / ID-025).

    Performs LOCAL branch create/checkout/delete via plain ``git`` subprocess
    calls (credential-free — the local clone's ``origin`` is already token-free
    per SFP-55), and delegates REMOTE branch *deletion* to an injected
    :class:`~workspace_worker.repo.git.adapter.GitProviderAdapter`. It never
    holds the token (ID-035).

    Args:
        repo_path: Path of the repository (the clone produced by
            :class:`~workspace_worker.repo.manager.RepoManager`). Must itself be
            a git repository.
        runner: Injectable git executor. Defaults to ``subprocess.run`` with
            ``check=True`` and captured output. Each call receives the full
            ``git`` argv; tests inject a fake to assert commands without
            spawning real git.
        remote: Optional constructed
            :class:`~workspace_worker.repo.git.adapter.GitProviderAdapter` used
            only for remote branch deletion. When ``None``, :meth:`delete` is
            local-only. BranchManager never reads a token from ``remote``.
    """

    def __init__(
        self,
        repo_path: Path,
        *,
        runner: Runner | None = None,
        remote: GitProviderAdapter | None = None,
    ) -> None:
        self._repo_path = repo_path
        self._runner: Runner = runner or _default_runner
        self._remote = remote

    def create_branch(self, name: str, *, ref: str = "HEAD") -> BranchResult:
        """Create a local branch ``name`` at ``ref``.

        Refuses to clobber an existing branch: a ``git branch --list <name>``
        pre-check is run first, and if it returns any match the create is
        **not** attempted and :class:`BranchError` is raised (no destructive git
        call). Otherwise issues exactly ``git -C <repo> branch <name> <ref>``.

        Args:
            name: Branch name to create (sanitised to a single safe token).
            ref: Ref to start the branch from (opaque — a branch, tag, or commit
                SHA); defaults to ``HEAD``.

        Returns:
            The :class:`BranchResult` describing the created branch.

        Raises:
            BranchError: if the branch already exists (pre-check hit, no create
                call made), if ``name`` is unsafe, or if the create fails. The
                git stderr is surfaced and the original
                :class:`subprocess.CalledProcessError` is chained on failure.
        """
        safe = _sanitize_branch_name(name)

        # Refuse-to-clobber pre-check: `git branch --list <name>` prints the
        # branch line(s) iff the branch exists (empty stdout == absent). Doing
        # this before the create avoids clobbering and gives a deterministic
        # error rather than parsing git's 'already exists' stderr (R3).
        listing = self._runner(["git", "-C", str(self._repo_path), "branch", "--list", safe])
        if (listing.stdout or "").strip():
            raise BranchError(f"branch already exists: {safe}")

        try:
            self._runner(["git", "-C", str(self._repo_path), "branch", safe, ref])
        except subprocess.CalledProcessError as exc:
            raise BranchError(
                f"failed to create branch {safe!r} at {ref}: {exc.stderr or exc}"
            ) from exc

        return BranchResult(name=safe, ref=ref)

    def checkout(self, name: str) -> None:
        """Check out the local branch ``name``.

        Issues exactly ``git -C <repo> checkout <name>``.

        Args:
            name: Branch name to check out (sanitised to a single safe token).

        Raises:
            BranchError: if ``name`` is unsafe or the checkout fails. The git
                stderr is surfaced and the original
                :class:`subprocess.CalledProcessError` is chained on failure.
        """
        safe = _sanitize_branch_name(name)
        try:
            self._runner(["git", "-C", str(self._repo_path), "checkout", safe])
        except subprocess.CalledProcessError as exc:
            raise BranchError(f"failed to checkout branch {safe!r}: {exc.stderr or exc}") from exc

    def delete(
        self,
        branch: str,
        *,
        keep_remote: bool = False,
        owner: str | None = None,
        repo: str | None = None,
    ) -> None:
        """Delete the local branch ``branch`` and, optionally, the remote ref.

        Always deletes the LOCAL branch via ``git -C <repo> branch -D <branch>``
        and is idempotent on an already-gone branch (git's
        ``error: branch '<name>' not found`` is swallowed; every other git
        failure wraps to :class:`BranchError` with the original chained as
        ``__cause__``).

        Then, **iff** ``remote`` was injected **and** ``keep_remote`` is
        ``False``, delegates the remote delete as
        ``remote.delete_ref(owner, repo, "heads/<branch>")``. ``owner`` and
        ``repo`` are required in that case (a remote delete was requested);
        missing either raises :class:`ValueError` before any remote call. When
        ``remote`` is ``None`` no remote call is made and ``owner``/``repo`` are
        not required. ``keep_remote=True`` suppresses the remote delete even when
        ``remote`` is set (local delete still runs).

        Args:
            branch: Branch name to delete (sanitised to a single safe token; the
                same token is mapped to ``heads/<branch>`` for the remote ref).
            keep_remote: When ``True``, never call ``remote.delete_ref`` even if
                ``remote`` is set.
            owner: Repository owner — required iff a remote delete will occur.
            repo: Repository name — required iff a remote delete will occur.

        Raises:
            ValueError: if a remote delete is requested (``remote`` is set and
                ``keep_remote`` is ``False``) but ``owner`` or ``repo`` is
                missing.
            BranchError: if the local delete fails for any reason other than the
                already-gone state. The git stderr is surfaced and the original
                :class:`subprocess.CalledProcessError` is chained.
        """
        safe = _sanitize_branch_name(branch)

        # Local delete — always attempted; idempotent on already-gone.
        try:
            self._runner(["git", "-C", str(self._repo_path), "branch", "-D", safe])
        except subprocess.CalledProcessError as exc:
            if not _is_already_gone(exc):
                raise BranchError(f"failed to delete branch {safe!r}: {exc.stderr or exc}") from exc

        # Remote delete — only when a remote was injected AND the caller did not
        # ask to keep it. owner/repo are mandatory here (R1 resolution).
        if self._remote is not None and not keep_remote:
            if not owner or not repo:
                raise ValueError("owner and repo are required to delete the remote ref")
            self._remote.delete_ref(owner, repo, f"heads/{safe}")

    @staticmethod
    def branch_name(ticket_number: int | str, slug: str) -> str:
        """Derive the canonical branch name ``sfp-<ticket-number>-<slug>``.

        The naming rule for SFP ephemeral branches (ID-025 is the *PR-as-review-
        unit* decision, not this rule). Lowercased so mixed-case slugs fold to
        the canonical form::

            BranchManager.branch_name(57, "Repo-Branch-Lifecycle")
              -> "sfp-57-repo-branch-lifecycle"
        """
        return f"sfp-{ticket_number}-{slug}".lower()
