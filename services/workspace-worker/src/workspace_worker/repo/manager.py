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

Idempotent: a second call against an existing clone (``dest/.git`` present) is
a no-op and returns ``cloned=False``.

This is the *clone* slice only. Worktree lifecycle (SFP-39), fetch/sync, and
cleanup land in follow-on tickets. The token reaches this module already
resolved from configuration (ID-016 / SFP-12 / SFP-78); this module never reads
secrets directly.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

__all__ = ["CloneResult", "RepoManager", "RepoManagerError"]

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

        Idempotent: if ``dest/.git`` already exists, returns immediately with
        ``cloned=False``. If ``dest`` exists without a ``.git`` directory,
        raises :class:`RepoManagerError` (refuses to clobber a non-repo
        directory).

        Args:
            repo_url: Remote URL (HTTPS for token auth; file:// for local).
            dest: Local destination path. Created by ``git clone``.

        Returns:
            The :class:`CloneResult` describing the outcome.

        Raises:
            RepoManagerError: if the clone or credential-strip fails, or if
                ``dest`` exists but is not a git repository. The token is
                redacted from the message.
        """
        # Idempotent fast-path: an existing clone is a no-op.
        if (dest / ".git").exists():
            return CloneResult(path=dest, cloned=False)
        # Refuse to clobber a non-repo directory — surface the state explicitly
        # rather than letting `git clone` produce a confusing nested error.
        if dest.exists():
            raise RepoManagerError(f"destination exists and is not a git repository: {dest}")

        authed_url = _inject_token(repo_url, self._token)
        clean_url = _strip_userinfo(repo_url)

        try:
            self._runner(["git", "clone", authed_url, str(dest)])
        except subprocess.CalledProcessError as exc:
            raise RepoManagerError(
                _redact(
                    f"git clone failed for {clean_url}: "
                    + _redact(str(exc.stderr or exc), self._token),
                    self._token,
                )
            ) from exc

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
            ) from exc

        return CloneResult(path=dest, cloned=True)
