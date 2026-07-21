"""Local Execution Engine — ``build`` operation (SFP-62 / ID-060 / MAS §9.6).

Provides the Workspace Worker's first Local Execution Engine operation —
``build`` — and the **shared ExecRunner abstraction** that the sibling operations
(SFP-63 test, SFP-64 lint) will import and reuse without re-defining.

``build`` resolves+installs the uv workspace (``uv sync --all-packages``) in a
given working directory on the **HOST** (Phase A; ID-060) and returns a bounded,
deterministic :class:`BuildResult`. The working directory is a per-job git
worktree (SFP-56 / ID-033) of the token-free clone (SFP-55) — no credentials
traverse this module, and there is no container boundary.

Scope — this is the ``build`` slice only. The ``test`` (SFP-63) and ``lint``
(SFP-64) operations are separate modules that *import* :data:`Runner` from here;
container isolation (docker/podman) is explicitly out of scope (SFP-65 / Phase
B) — a scope-guard test asserts no container argv token ever leaks into this
slice.

Convention — mirrors :mod:`workspace_worker.repo.worktree` (SFP-56) line for
line: the :data:`Runner` type signature, a frozen+slots :class:`BuildResult`
dataclass, and a :class:`BuildError` that wraps :class:`subprocess.CalledProcessError`
with the original chained as ``__cause__`` via ``raise ... from exc``.

Design note — cwd + check=False divergence from the repo slices:

* The repo slices (``manager.py``/``worktree.py``) set cwd via ``git -C`` and use
  ``check=True`` (a non-zero exit raises :class:`~subprocess.CalledProcessError`,
  wrapped to their error type). ``build`` diverges on both counts, by design:

  * **cwd.** The shared :data:`Runner` type ``Callable[[list[str]],
    CompletedProcess[str]]`` carries no cwd parameter (worktree.py:44) — it must
    stay cwd-free so SFP-63/64 reuse it verbatim. But ``uv`` needs ``cwd=workdir``
    (a ``subprocess.run`` kwarg, not an argv token). The faithful resolution
    (PRSpec risk #4): the build-local *default* runner is a nested function that
    closes over ``workdir``, so the :data:`Runner` signature is unchanged while
    cwd is still set. An *injected* runner is used verbatim and is responsible
    for its own cwd.
  * **check=False.** ``exit_code`` is a first-class :class:`BuildResult` field,
    so a non-zero exit is a *result*, not a raised exception. The build-local
    default runner therefore runs ``subprocess.run(..., check=False)`` and the
    exit code flows into :class:`BuildResult`. An *injected* runner that raises
    :class:`~subprocess.CalledProcessError` is still wrapped to :class:`BuildError`
    with chained ``__cause__`` (mirrors worktree.py:193-197) — this is the
    contract for callers that prefer the raise-on-failure form.

Determinism — captured stdout/stderr are bounded to their last 200 lines via
:func:`_tail` (:data:`_TAIL_LINES`), so a :class:`BuildResult` is bounded in
size regardless of ``uv`` output volume; no unbounded capture leaks into the
result or logs.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = ["BuildError", "BuildResult", "Runner", "build"]

#: Signature of the injectable exec runner (shared with SFP-63 test + SFP-64
#: lint; mirror of ``repo/worktree.py:44``). Carries NO cwd parameter on
#: purpose — cwd is handled inside :func:`build`'s default runner (a closure),
#: so this type stays reusable across operations that each own their own cwd.
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

#: Maximum number of trailing lines retained in a :class:`BuildResult` tail.
#: Bounds captured stdout/stderr to a deterministic size regardless of volume.
_TAIL_LINES = 200

#: The exact ``uv`` argv this operation issues. ``--all-packages`` resolves the
#: whole uv workspace (no ``[build-system]`` at the workspace root → build is
#: resolve+install, not sdist/wheel). Pinned by the unit test.
_BUILD_ARGV = ["uv", "sync", "--all-packages"]


def _tail(text: str, max_lines: int = _TAIL_LINES) -> str:
    """Return the last ``max_lines`` lines of ``text`` (deterministic bound).

    Splits on line boundaries and keeps only the final ``max_lines`` entries, so
    a :class:`BuildResult` tail is bounded regardless of how verbose ``uv`` was.
    A falsy/empty ``text`` yields ``""``.
    """
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:]) if lines else ""


class BuildError(RuntimeError):
    """Raised when an injected runner reports ``uv sync`` failing by raising.

    Mirrors :class:`~workspace_worker.repo.worktree.WorktreeError`: the failing
    command context (stderr) is surfaced in the message and the original
    :class:`subprocess.CalledProcessError` is chained as ``__cause__`` via
    ``raise BuildError(...) from exc`` (worktree.py:193-197).

    Note: this is raised ONLY for an *injected* runner that raises. The
    build-local default runner uses ``check=False`` and never raises on a
    non-zero exit — that becomes :attr:`BuildResult.success` = ``False``.
    """


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Outcome of :func:`build` — bounded, deterministic.

    Attributes:
        success: ``True`` iff :attr:`exit_code` == 0.
        exit_code: The ``uv`` process exit code (carried verbatim, never raised
            by the default runner — ``check=False``).
        stdout_tail: Last :data:`_TAIL_LINES` lines of captured stdout.
        stderr_tail: Last :data:`_TAIL_LINES` lines of captured stderr.
    """

    success: bool
    exit_code: int
    stdout_tail: str
    stderr_tail: str


def build(workdir: str | Path, *, runner: Runner | None = None) -> BuildResult:
    """Run ``uv sync --all-packages`` in ``workdir``; return a bounded result.

    Resolves+installs the uv workspace into a venv inside ``workdir`` (Phase A
    HOST execution; container isolation is SFP-65). Issues exactly
    ``['uv', 'sync', '--all-packages']`` with the process cwd set to
    ``workdir``.

    Args:
        workdir: Working directory to build in (a per-job worktree of the
            token-free clone). ``str`` or :class:`~pathlib.Path`.
        runner: Injectable exec runner (mirror of the repo slices' ``runner``
            param). When ``None`` (the default) a build-local runner is used
            that closes over ``workdir`` (cwd=workdir, ``check=False``). When
            supplied, it is used **verbatim** and receives exactly
            ``['uv', 'sync', '--all-packages']`` — it owns its own cwd.

    Returns:
        A :class:`BuildResult` with ``success = (exit_code == 0)`` and the
        bounded stdout/stderr tails.

    Raises:
        BuildError: if an *injected* runner raises
            :class:`subprocess.CalledProcessError`. The original exception is
            chained as ``__cause__``. The default runner never raises on a
            non-zero exit (``check=False``) — that path yields a
            :class:`BuildResult` with ``success=False`` instead.
    """
    cwd = str(workdir)

    def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        # check=False: a non-zero exit is a first-class BuildResult field, not a
        # raised CalledProcessError (divergence from the repo slices — see the
        # module docstring). cwd=workdir is set here, inside the closure, so the
        # shared Runner type stays cwd-free and reusable for SFP-63/64.
        return subprocess.run(  # noqa: S603 — trusted argv, pinned by tests
            cmd, cwd=cwd, check=False, capture_output=True, text=True
        )

    run: Runner = runner or _default_runner
    try:
        completed = run(list(_BUILD_ARGV))
    except subprocess.CalledProcessError as exc:
        raise BuildError(
            f"uv sync --all-packages failed in {workdir}: {exc.stderr or exc}"
        ) from exc

    exit_code = completed.returncode
    return BuildResult(
        success=exit_code == 0,
        exit_code=exit_code,
        stdout_tail=_tail(completed.stdout or ""),
        stderr_tail=_tail(completed.stderr or ""),
    )
