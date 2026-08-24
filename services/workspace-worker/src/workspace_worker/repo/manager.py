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

This is the *clone* slice only. Worktree lifecycle (SFP-39), fetch/sync, and
cleanup land in follow-on tickets. The token reaches this module already
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


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # capture_output so stderr never escapes unredacted to the console/logs.
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


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
    """

    def __init__(self, token: str, *, runner: Runner | None = None) -> None:
        self._token = token
        self._runner: Runner = runner or _default_runner

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
