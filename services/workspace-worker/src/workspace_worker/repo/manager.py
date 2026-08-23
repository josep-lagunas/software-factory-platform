"""Repository Manager — clone layer (SFP-38 / SFP-55).

Clones the target repository to a local path inside the Workspace Worker,
authenticating to GitHub via the configured token injected into the clone URL
(ID-034, ID-035, MAS §9.6).

Security model — the token never lands on disk:

* The clone is performed with the token injected into the URL as userinfo
  (``https://x-access-token:<token>@host/...``). This is the transient,
  in-memory form used only for the ``git clone`` invocation.
* Immediately after a successful clone, the on-disk ``origin`` remote is
  rewritten to the token-free URL via ``git remote set-url`` so ``.git/config``
  carries no secret.
* If that rewrite fails, the freshly-cloned tree is torn down (``rmtree``)
  rather than left with a token-bearing config — honoring "NEVER write the
  token to disk" on every code path.
* The token is redacted from any error message surfaced by this module.

Idempotent: a second call against an existing, *valid* clone is a no-op and
returns ``cloned=False``. The reuse path validates the cache entry with
``git rev-parse --git-dir`` (SFP-239): a hollow/corrupt cached clone (directory
present, ``.git`` purged or unusable) is treated as a cache miss — removed and
re-cloned — instead of failing downstream with git's raw ``fatal: not a git
repository``.

This is the *clone* + *push* + *base-sync* slice. Worktree lifecycle (SFP-39)
and cleanup live in sibling modules. The token reaches this module already
resolved from configuration (ID-016 / SFP-12 / SFP-78); this module never reads
secrets directly.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

__all__ = [
    "BaseSyncConflictError",
    "BaseSyncResult",
    "CloneResult",
    "PushResult",
    "RepoManager",
    "RepoManagerError",
]

from workspace_worker.repo._validation import is_valid_git_repo, remove_path

__all__ = ["CloneResult", "PushResult", "RepoManager", "RepoManagerError"]

#: Module logger — cache-recovery events land here for ops observability.
_log = logging.getLogger(__name__)

#: GitHub's conventional username for PAT authentication over HTTPS.
_TOKEN_USER = "x-access-token"

#: Placeholder substituted for the token anywhere it would appear in errors/logs.
_REDACTED = "***"

#: Signature of the injectable git runner (defaults to :func:`subprocess.run`).
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

#: Signature of the injectable git runner with ``check=False`` semantics —
#: the call returns the :class:`subprocess.CompletedProcess` regardless of exit
#: code, so the caller can treat a non-zero exit as DATA (a merge conflict).
RunnerCheckFalse = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

#: The bot committer identity for merge commits created by the pipeline
#: (SFP-240). This is the EXISTING factory identity documented for the Coder
#: role (.claude/agents/sfp-coder.md — the repo's ``user.name`` /
#: ``user.email`` for ``sfp-coder-bot``), reused — not a new mechanism. It is
#: passed as one-shot ``-c`` config on the merge argv, so no ``git config``
#: is written and the Coder's own commit identity config is left untouched.
_MERGE_BOT_NAME = "sfp-coder-bot"
_MERGE_BOT_EMAIL = "299957016+sfp-coder-bot@users.noreply.github.com"


class RepoManagerError(RuntimeError):
    """Raised when a repository operation fails.

    The token is guaranteed absent from the message (see :func:`_redact`).
    """


@dataclass(frozen=True, slots=True)
class CloneResult:
    """Outcome of :meth:`RepoManager.clone`.

    Attributes:
        path: The local path of the repository (``dest``).
        cloned: ``True`` if the repository was cloned during this call;
            ``False`` if it already existed and the call was an idempotent skip.
    """

    path: Path
    cloned: bool


@dataclass(frozen=True, slots=True)
class PushResult:
    """Outcome of :meth:`RepoManager.push` (SFP-224).

    Mirrors :class:`CloneResult`. The push is a one-shot authenticated
    ``git push`` whose authed URL lives only on the argv; the on-disk ``origin``
    is left untouched (the token is NEVER written to ``.git/config``).

    Attributes:
        path: The local repository path pushed from (the worktree).
        branch: The branch name that was pushed.
        pushed: ``True`` if the push was issued during this call.
    """

    path: Path
    branch: str
    pushed: bool


@dataclass(frozen=True, slots=True)
class BaseSyncResult:
    """Outcome of :meth:`RepoManager.sync_base` (SFP-240).

    Attributes:
        path: The worktree the base was merged into.
        base_branch: The base branch that was fetched + merged (e.g. ``main``).
        merged: ``True`` when the merge committed new base commits onto the
            branch (including a fast-forward). ``False`` for the no-op case —
            the branch base already contained ``origin/<base_branch>``.
    """

    path: Path
    base_branch: str
    merged: bool


class BaseSyncConflictError(RepoManagerError):
    """Raised by :meth:`RepoManager.sync_base` when the base merge conflicts.

    The worktree is left in its PRE-merge state — ``git merge --abort`` ran
    before raising, so no half-merged/conflicted index remains. The conflicted
    file names are carried on :attr:`conflicted_files` and embedded in the
    message; the token is guaranteed absent from both (redacted).
    """

    def __init__(self, message: str, conflicted_files: tuple[str, ...]) -> None:
        super().__init__(message)
        self.conflicted_files = conflicted_files


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # capture_output so stderr never escapes unredacted to the console/logs.
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _default_merge_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # check=False: a merge conflict exits non-zero and that exit code is DATA
    # for sync_base — it must come back, not raise. Output is captured so a
    # token-bearing string could never escape to the console (defensive: the
    # merge itself never sees a token, but stderr echo of argv is possible).
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _inject_token(repo_url: str, token: str) -> str:
    """Return ``repo_url`` with ``x-access-token:<token>`` injected as userinfo.

    Token injection applies to HTTPS URLs only — token auth is HTTPS-specific.
    Non-HTTPS URLs (e.g. ``file://`` for local dev/integration, ``ssh://`` for
    key-based auth) are returned unchanged; the caller will clone without a
    token, which is correct for those transports. The original scheme / host /
    port / path / query / fragment are preserved.
    """
    parts = urlsplit(repo_url)
    if parts.scheme != "https":
        return repo_url
    host = parts.hostname or ""
    authed_netloc = f"{_TOKEN_USER}:{token}@{host}"
    if parts.port is not None:
        authed_netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, authed_netloc, parts.path, parts.query, parts.fragment))


def _strip_userinfo(repo_url: str) -> str:
    """Return ``repo_url`` with any userinfo removed (token-free clean URL)."""
    parts = urlsplit(repo_url)
    host = parts.hostname or ""
    clean_netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, clean_netloc, parts.path, parts.query, parts.fragment))


def _redact(text: str, token: str) -> str:
    """Replace every occurrence of ``token`` in ``text`` with ``***``.

    A token that is empty/falsy disables redaction (nothing to leak).
    """
    return text.replace(token, _REDACTED) if token else text


class RepoManager:
    """Clones a remote repository locally using a GitHub token for auth.

    The token is held in memory only and is never persisted: the clone runs with
    the token injected into the URL, then the on-disk ``origin`` remote is
    rewritten to the token-free URL. See the module docstring for the full
    security model.

    Args:
        token: GitHub access token (PAT) used for HTTPS authentication. Already
            resolved from configuration by the caller (ID-016).
        runner: Injectable git executor. Defaults to ``subprocess.run`` with
            ``check=True`` and captured output. Each call receives the full
            ``git`` argv; tests inject a fake to assert commands without
            spawning real git.
        merge_runner: Injectable executor for the ONE command whose non-zero
            exit is expected data rather than an error — the base merge in
            :meth:`sync_base` (a conflict exits non-zero). Same signature as
            ``runner`` but the returned
            :class:`subprocess.CompletedProcess.returncode` is inspected
            instead of raising. Defaults to ``subprocess.run(check=False)``.
            Separated from ``runner`` so a single fake can drive both call
            shapes deterministically in tests.
    """

    def __init__(
        self,
        token: str,
        *,
        runner: Runner | None = None,
        merge_runner: RunnerCheckFalse | None = None,
    ) -> None:
        self._token = token
        self._runner: Runner = runner or _default_runner
        self._merge_runner: RunnerCheckFalse = merge_runner or _default_merge_runner

    def clone(self, repo_url: str, dest: Path) -> CloneResult:
        """Clone ``repo_url`` into ``dest``, authenticating via the token.

        Idempotent: if ``dest`` already holds a valid clone, returns
        immediately with ``cloned=False``. A ``dest`` that exists but fails the
        validity probe (``git rev-parse --git-dir``) — a hollow clone whose
        ``.git`` was purged — is treated as a cache miss: it is removed and
        re-cloned (SFP-239). This no longer refuses on "exists but is not a
        repo"; the cache location is worker-owned and the entry is rebuilt
        rather than treated as untouchable caller data.

        Args:
            repo_url: Remote URL (HTTPS for token auth; file:// for local).
            dest: Local destination path. Created by ``git clone``.

        Returns:
            The :class:`CloneResult` describing the outcome.

        Raises:
            RepoManagerError: if the clone or credential-strip fails, if the
                corrupt cache entry cannot be removed for re-clone, or if the
                token-bearing URL would otherwise surface. The token is
                redacted from the message.
        """
        # Idempotent fast-path: an existing VALID clone is a no-op (SFP-239).
        # Existence alone is not enough — the cache under SFP_WORKTREE_BASE can
        # be hollowed out (/tmp cleanup, partial disk, crashed run), and a
        # hollow entry must not poison the run with a raw
        # "fatal: not a git repository" from some downstream git call. Probe
        # with git's cheapest repository check; a miss means remove + re-clone.
        if (dest / ".git").exists() or dest.exists():
            if is_valid_git_repo(dest, self._runner):
                return CloneResult(path=dest, cloned=False)
            # Corrupt cache entry → treat as a cache miss: remove the hollow
            # tree and fall through to the existing clone logic below. Removal
            # failure (permissions/partial state) must surface as a clear,
            # actionable error naming the path, not a crash mid-recovery.
            _log.warning("clone cache entry %s is not a valid git repo; re-cloning", dest)
            try:
                remove_path(dest)
            except OSError as exc:
                raise RepoManagerError(
                    f"corrupt clone cache entry at {dest} could not be removed "
                    f"for re-clone (remove it manually): {exc}"
                ) from exc

        authed_url = _inject_token(repo_url, self._token)
        clean_url = _strip_userinfo(repo_url)

        try:
            self._runner(["git", "clone", authed_url, str(dest)])
        except subprocess.CalledProcessError as exc:
            # `from None` (not `from exc`): the chained CalledProcessError's
            # `.cmd` carries the token-bearing argv (`x-access-token:<PAT>@...`)
            # and is never redacted — a traceback would leak the PAT. The
            # redacted message above already carries the (redacted) git stderr.
            raise RepoManagerError(
                _redact(
                    f"git clone failed for {clean_url}: "
                    + _redact(str(exc.stderr or exc), self._token),
                    self._token,
                )
            ) from None

        # Rewrite the on-disk `origin` to the token-free URL so .git/config
        # never carries the token. If this fails, tear down the clone — a
        # successful clone left the authed (token-bearing) URL in config, and
        # "never write the token to disk" takes priority over keeping the clone.
        try:
            self._runner(["git", "-C", str(dest), "remote", "set-url", "origin", clean_url])
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            raise RepoManagerError(
                _redact(
                    f"failed to strip credentials from cloned repo {dest}: "
                    + _redact(str(exc.stderr or exc), self._token),
                    self._token,
                )
            ) from None

        return CloneResult(path=dest, cloned=True)

    def push(
        self,
        repo_path: str | Path,
        branch: str,
        *,
        remote_url: str | None = None,
    ) -> PushResult:
        """Push ``branch`` from ``repo_path`` to its origin, authenticating in-memory.

        Symmetric to :meth:`clone` (SFP-224). Builds the authed URL via
        :func:`_inject_token` and runs ``git -C <repo_path> push <authed_url>
        <branch>`` through ``self._runner``. The token is held in memory only —
        it appears transiently on the push argv and is NEVER written to
        ``.git/config``: this method issues NO ``git remote set-url``, so the
        on-disk ``origin`` stays token-free exactly as :meth:`clone` left it.

        This is the object-upload path for locally-committed Coder work. The Git
        Provider Adapter's ``push_branch`` (Git Data refs API) CANNOT upload
        locally-committed objects — only this slice can, via a real ``git push``
        (ID-034 / ID-035 / MAS §9.6). The slice is therefore on the critical
        path of the end-to-end vertical slice.

        Args:
            repo_path: Local repository path to push from (the per-job worktree).
            branch: Branch name to push (carried verbatim as the push refspec).
            remote_url: Remote URL to push to. When ``None`` (the default) the
                on-disk ``origin`` URL is read via
                ``git -C repo_path remote get-url origin`` — which is the
                token-free URL :meth:`clone` wrote — and the token is injected
                into a throwaway authed URL for the single push invocation.

        Returns:
            The :class:`PushResult` (``pushed=True`` on a successful push).

        Raises:
            RepoManagerError: if reading the on-disk origin or the push itself
                fails. The token is redacted from the message.
        """
        path = Path(repo_path)

        # Resolve the remote URL: caller-supplied, else read the on-disk origin
        # (the token-free URL clone() wrote). The token is injected ONLY into
        # the throwaway authed URL below — origin stays clean.
        if remote_url is None:
            try:
                origin = self._runner(["git", "-C", str(path), "remote", "get-url", "origin"])
            except subprocess.CalledProcessError as exc:
                raise RepoManagerError(
                    _redact(
                        f"failed to read origin for {path}: "
                        + _redact(str(exc.stderr or exc), self._token),
                        self._token,
                    )
                ) from None
            remote_url = (origin.stdout or "").strip()

        authed_url = _inject_token(remote_url, self._token)
        clean_url = _strip_userinfo(remote_url)

        # One-shot authenticated push. The authed URL lives only on this argv;
        # NO `git remote set-url` — the on-disk origin is never touched, so the
        # token never lands in .git/config.
        try:
            self._runner(["git", "-C", str(path), "push", authed_url, branch])
        except subprocess.CalledProcessError as exc:
            # `from None`: the push argv carries the token-bearing authed URL;
            # the chained CalledProcessError.cmd would leak the PAT via traceback.
            raise RepoManagerError(
                _redact(
                    f"git push failed for {clean_url} branch={branch}: "
                    + _redact(str(exc.stderr or exc), self._token),
                    self._token,
                )
            ) from None

        return PushResult(path=path, branch=branch, pushed=True)

    def sync_base(
        self,
        worktree_path: str | Path,
        base_branch: str,
        *,
        remote_url: str | None = None,
    ) -> BaseSyncResult:
        """Fetch ``origin/<base_branch>`` and MERGE it into the worktree (SFP-240).

        Runs the pre-push base-sync stage for the ticket pipeline:

        1. one-shot authenticated ``git fetch <authed_url> <base_branch>``;
        2. ``git merge --no-edit FETCH_HEAD`` in the worktree — MERGE, never
           rebase (a rebase would rewrite the Coder's commits);
        3. on a clean merge the merge COMMIT is attributed to the existing bot
           identity via one-shot ``-c user.name/-c user.email`` argv config
           (no ``git config`` write — the Coder's own identity config is left
           untouched);
        4. on a conflict: the conflicted names are read from the index, then
           ``git merge --abort`` restores the pre-merge state, then
           :class:`BaseSyncConflictError` raises fail-closed.

        No-op case: when the branch base already contains the fetched tip, git
        exits 0 without creating a commit. The sync stays UNCONDITIONAL and
        cheap (no base-tracking conditionals — SFP-240 implementation notes);
        the no-op is detected from the tree, not from localized stdout: the
        merge is only "real" when ``HEAD`` moved, i.e. when the pre-merge and
        post-merge commits differ. A fast-forward (nothing new on the ticket
        branch itself yet) also reports ``merged=True`` — the base did land.

        Route justification (pinned by SFP-240): the LOCAL-merge route is chosen
        over :meth:`workspace_worker.repo.git.adapter.GitProviderAdapter.sync_branch`
        (SFP-59, the ``update-branch`` API) because it surfaces named-file
        conflict errors LOCALLY, before the push, and yields a pushed tree that
        is byte-identical to the locally verified one. Object upload afterwards
        remains :meth:`RepoManager.push` — never the adapter's Git Data refs API
        (RESOLUTION 4 in this module's docstring).

        Conflict handling is fail-closed: the conflicted file names are read
        from the index FIRST (``git diff --name-only --diff-filter=U``), THEN
        ``git merge --abort`` restores the pre-merge state, and only then is
        :class:`BaseSyncConflictError` raised. No semantic auto-resolution is
        attempted. If the abort itself fails, its error is raised INSTEAD of
        the conflict error — a half-merged worktree must never be reported as a
        cleanly-aborted conflict.

        Auth model (mirrors :meth:`push`): the token is injected only into the
        throwaway fetch URL on the argv; the merge runs against FETCH_HEAD with
        no network. No ``git remote set-url`` is issued, so the on-disk
        ``origin`` stays token-free.

        Args:
            worktree_path: Local worktree path to sync (the per-job worktree).
            base_branch: Base branch name to fetch + merge (e.g. ``main``).
                Carried verbatim as the fetch refspec, exactly as :meth:`push`
                carries the branch name.
            remote_url: Remote URL to fetch from. When ``None`` (the default)
                the on-disk ``origin`` URL is read via
                ``git -C worktree remote get-url origin`` (token-free, as
                :meth:`clone` wrote it) and the token is injected into a
                throwaway authed URL for the single fetch invocation.

        Returns:
            The :class:`BaseSyncResult` — ``merged=False`` for the no-op case
            (base already current), ``merged=True`` otherwise.

        Raises:
            BaseSyncConflictError: the merge conflicted; conflicted file names
                are on ``conflicted_files`` and in the (redacted) message. The
                worktree is pre-merge (``git merge --abort`` already ran).
            RepoManagerError: the fetch, the clean-merge commit, the conflicted
                listing, or the conflict-abort failed. The token is redacted
                from the message.
        """
        path = Path(worktree_path)

        # (1) Resolve the fetch URL: caller-supplied, else the on-disk origin.
        # Exactly push()'s pattern — origin stays clean, auth lives on one argv.
        if remote_url is None:
            try:
                origin = self._runner(["git", "-C", str(path), "remote", "get-url", "origin"])
            except subprocess.CalledProcessError as exc:
                raise RepoManagerError(
                    _redact(
                        f"failed to read origin for {path}: "
                        + _redact(str(exc.stderr or exc), self._token),
                        self._token,
                    )
                ) from None
            remote_url = (origin.stdout or "").strip()
        authed_url = _inject_token(remote_url, self._token)

        # (2) One-shot authenticated fetch of the base branch (refspec, not a
        # full remote sync). FETCH_HEAD points at the fetched tip afterwards.
        try:
            self._runner(["git", "-C", str(path), "fetch", authed_url, base_branch])
        except subprocess.CalledProcessError as exc:
            # `from None`: the fetch argv carries the token-bearing authed URL;
            # a chained CalledProcessError would leak the PAT via .cmd.
            raise RepoManagerError(
                _redact(
                    f"git fetch failed for {_strip_userinfo(remote_url)} branch={base_branch}: "
                    + _redact(str(exc.stderr or exc), self._token),
                    self._token,
                )
            ) from None

        # (3) Merge FETCH_HEAD into the worktree's checked-out branch, committed
        # as the existing bot identity (one-shot -c config; no config write).
        # The merge runs through the check=False runner: a conflict exits
        # non-zero and that exit code is DATA, not an error to raise here.
        pre_merge_head = self._rev_parse(path, "HEAD")
        # Spaced forms ONLY (``-C <path>``, ``-c <name>=<value>``): git rejects
        # the attached ``-C<path>`` / ``-c<name>=<value>`` spellings with exit
        # 129 "unknown option" — verified against git 2.54.
        merge = self._merge_runner(
            [
                "git",
                "-C",
                str(path),
                "-c",
                f"user.name={_MERGE_BOT_NAME}",
                "-c",
                f"user.email={_MERGE_BOT_EMAIL}",
                "merge",
                "--no-edit",
                "FETCH_HEAD",
            ]
        )
        if merge.returncode == 0:
            post_merge_head = self._rev_parse(path, "HEAD")
            return BaseSyncResult(
                path=path,
                base_branch=base_branch,
                merged=post_merge_head != pre_merge_head,
            )

        # (4) Merge failed. Parse the conflicted names FIRST (the index still
        # holds the conflicted state here), then abort fail-closed, then raise.
        conflicted = self._list_conflicted(path)
        try:
            self._runner(["git", "-C", str(path), "merge", "--abort"])
        except subprocess.CalledProcessError as abort_exc:
            # The abort failed: the worktree may be half-merged. Report THAT —
            # never claim a clean conflict-abort while a dirty state remains.
            raise RepoManagerError(
                _redact(
                    f"merge conflicted in {len(conflicted)} file(s) AND "
                    f"'git merge --abort' failed for {path} — worktree may be "
                    "half-merged, inspect before re-running: "
                    + _redact(str(abort_exc.stderr or abort_exc), self._token),
                    self._token,
                )
            ) from None
        files = ", ".join(conflicted) if conflicted else "(none reported by git)"
        raise BaseSyncConflictError(
            _redact(
                f"base stale: merge conflicts in {files} for base '{base_branch}' in {path}",
                self._token,
            ),
            tuple(conflicted),
        )

    def _rev_parse(self, path: Path, ref: str) -> str:
        """Resolve ``ref`` to a commit SHA (empty string when unresolvable).

        Used to detect the merge no-op case from the TREE (HEAD moved or not)
        rather than from localized human-readable stdout like
        ``"Already up to date."``. An unresolvable ref (should not happen for
        HEAD in a synced worktree) degrades to ``""`` — treated as "no commit
        to compare", which can only under-report ``merged`` for that edge, not
        misreport a real merge.
        """
        try:
            resolved = self._runner(["git", "-C", str(path), "rev-parse", ref])
        except subprocess.CalledProcessError:
            return ""
        return (resolved.stdout or "").strip()

    def _list_conflicted(self, path: Path) -> list[str]:
        """Return the conflicted path names from the worktree's index.

        ``git diff --name-only --diff-filter=U`` lists paths whose index state
        is unresolved (both-modified / added-by-both). Names are ordered as git
        reports them (deterministic for a given index). Read failures surface
        as a plain :class:`RepoManagerError` — no secret traverses this
        command, so no redaction is required (mirrors worktree.py's local ops).
        """
        try:
            listing = self._runner(
                ["git", "-C", str(path), "diff", "--name-only", "--diff-filter=U"]
            )
        except subprocess.CalledProcessError as exc:
            raise RepoManagerError(
                f"failed to list conflicted files in {path}: {exc.stderr or exc}"
            ) from None
        return [line for line in (listing.stdout or "").splitlines() if line.strip()]
