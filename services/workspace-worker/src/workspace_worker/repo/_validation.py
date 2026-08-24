"""Private filesystem/git validity helpers shared by the repo/ managers (SFP-239).

The clone cache and per-job worktree slots under ``SFP_WORKTREE_BASE`` are
worker-owned paths that can be hollowed out behind the process's back — an
aggressive ``/tmp`` cleaner, a partial disk, or a crashed prior run can leave a
directory whose ``.git`` contents are gone. The landed managers gated reuse on
mere *existence*, so a hollow entry passed the check and the run died deep
downstream with git's raw ``fatal: not a git repository``.

This module centralises the two primitives that fix that (SFP-239):

* :func:`is_valid_git_repo` — git's cheapest "is this actually a repository"
  probe, used to gate every reuse path.
* :func:`remove_path` — remove a worker-owned path regardless of whether it is
  a directory tree, a stray file, or a symlink (never following the symlink),
  so a corrupt cache entry can be treated as a miss and rebuilt.

Both are deliberately dependency-free (no logging side effects) so callers
decide how — and whether — to log the recovery.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

__all__ = ["is_valid_git_repo", "remove_path"]

#: Signature of the injectable git runner (mirrors manager.py / worktree.py).
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def is_valid_git_repo(path: Path, runner: Runner) -> bool:
    """Return ``True`` when ``path`` is a working git repository.

    Runs ``git -C <path> rev-parse --git-dir`` — git's canonical cheap probe.
    A non-zero exit means the path is NOT a usable repository: no ``.git`` at
    all, an empty/hollowed ``.git`` directory, or corrupt git metadata. That
    is exactly the hollow-cache state SFP-239 recovers from.

    Invalid is signalled either way a runner reports it: a
    :class:`subprocess.CalledProcessError` (``check=True`` runners — the
    package's default) or a non-zero ``returncode`` on the returned
    :class:`~subprocess.CompletedProcess` (``check=False`` runners). Only git's
    verdict is mapped to ``False``; an :class:`OSError` (e.g. the git binary
    missing) propagates to the caller, matching how every other runner call in
    this package behaves.

    Args:
        path: Directory to probe.
        runner: Injectable git executor (same contract as the managers use).

    Returns:
        ``True`` if the probe exits zero, ``False`` otherwise.
    """
    try:
        result = runner(["git", "-C", str(path), "rev-parse", "--git-dir"])
    except subprocess.CalledProcessError:
        return False
    return result.returncode == 0


def remove_path(path: Path) -> None:
    """Remove the worker-owned entry at ``path`` — directory tree, file, or symlink.

    Used to clear a corrupt cache/worktree entry so it can be rebuilt from
    scratch. Unlike a bare ``shutil.rmtree``, this is safe for every shape the
    slot can be found in:

    * a directory (the normal corrupt-clone/worktree case) → ``rmtree``;
    * a regular file → ``unlink`` (``rmtree`` would raise on it);
    * a symlink → ``unlink`` the link itself — ``rmtree`` refuses symlinks,
      and unlinking is what keeps removal from following the link into an
      unrelated tree.

    Args:
        path: Entry to remove. Missing entries are a no-op.

    Raises:
        OSError: if removal fails (permissions, partial state). Callers are
            expected to surface this as a clear, actionable error naming
            ``path`` rather than crashing.
    """
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path)
