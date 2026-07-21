"""Local Execution Engine — ``lint`` operation (SFP-64 / ID-060 / ID-062 / MAS §9.6).

Provides the Workspace Worker's lint Local Execution Engine operation —
``lint`` — which runs the **ID-062 canonical 3-command suite** (ruff check,
ruff format --check, mypy) via the injectable :data:`Runner` IMPORTED from
:mod:`workspace_worker.exec.build` (SFP-62).

``lint`` executes the suite in a given working directory on the **HOST** (Phase
A; ID-060) and returns a bounded, deterministic :class:`LintResult` composed of
per-command :class:`LinterCheck` records (raw bounded tails, not structured
findings). The working directory is a per-job git worktree (SFP-56 / ID-033) of
the token-free clone (SFP-55) — no credentials traverse this module, and there
is no container boundary.

Scope — this is the ``lint`` slice only. The canonical suite is the ID-062
contract::

    1. ``uv run ruff check .``           (name: ``ruff_check``)
    2. ``uv run ruff format --check .``  (name: ``ruff_format_check``)
    3. ``uv run mypy packages services`` (name: ``mypy``)

All three ALWAYS run (no short-circuit on failure). Container isolation
(docker/podman) is explicitly out of scope (SFP-65 / Phase B) — a scope-guard
test asserts no container argv token ever leaks into this slice.

Convention — mirrors :mod:`workspace_worker.exec.build` (SFP-62) line for line:
the shared :data:`Runner` type, frozen+slots dataclasses, and a
:class:`LintError` that wraps :class:`subprocess.CalledProcessError` with the
original chained as ``__cause__`` via ``raise ... from exc``.

Reuse — :data:`Runner`, :func:`_tail`, and :data:`_TAIL_LINES` are IMPORTED from
:mod:`workspace_worker.exec.build` (SFP-62); they are NOT redefined here. This
is the whole point of SFP-62 introducing the shared abstraction first; a
redefinition would silently diverge.

Design note — cwd + check=False divergence (identical to SFP-62):

* **cwd.** The shared :data:`Runner` type carries no cwd parameter — it must
  stay cwd-free so all operations reuse it verbatim. But the lint commands need
  ``cwd=workdir`` (a ``subprocess.run`` kwarg, not an argv token). The faithful
  resolution (PRSpec risk #4): the lint-local *default* runner is a nested
  function that closes over ``workdir``, so the :data:`Runner` signature is
  unchanged while cwd is still set. An *injected* runner is used verbatim and is
  responsible for its own cwd. workdir is NEVER an argv token (closed over).
* **check=False.** ``exit_code`` is a first-class :class:`LinterCheck` field,
  so a non-zero exit is a *result*, not a raised exception. The lint-local
  default runner therefore runs ``subprocess.run(..., check=False)`` and the
  exit code flows into :class:`LinterCheck`. An *injected* runner that raises
  :class:`~subprocess.CalledProcessError` is still wrapped to :class:`LintError`
  with chained ``__cause__`` (mirrors build.py:162-165) — this is the contract
  for callers that prefer the raise-on-failure form.

Determinism — captured stdout/stderr per command are bounded to their last 200
lines via :func:`_tail` (:data:`_TAIL_LINES`), so a :class:`LinterCheck` is
bounded in size regardless of tool output volume; no unbounded capture leaks
into the result or logs.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from workspace_worker.exec.build import (
    _TAIL_LINES,  # noqa: F401 — reuse contract: imported, not redefined (AST-guarded)
    Runner,
    _tail,
)

__all__ = ["LintError", "LintResult", "LinterCheck", "lint"]

#: The canonical ID-062 lint suite as an ordered tuple of ``(name, argv)``
#: pairs. ALL THREE ALWAYS run (no short-circuit on failure). The names are
#: pinned enum values: ``'ruff_check'`` | ``'ruff_format_check'`` | ``'mypy'``.
#: Argv tokenization mirrors ``_BUILD_ARGV`` in build.py:74 (split
#: list-of-strings, not a shell string). workdir is NOT a token — cwd is closed
#: over inside the default runner.
_LINT_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("ruff_check", ["uv", "run", "ruff", "check", "."]),
    ("ruff_format_check", ["uv", "run", "ruff", "format", "--check", "."]),
    ("mypy", ["uv", "run", "mypy", "packages", "services"]),
)


class LintError(RuntimeError):
    """Raised when an injected runner reports a lint command failing by raising.

    Mirrors :class:`~workspace_worker.exec.build.BuildError`: the failing
    command context (name, argv, stderr) is surfaced in the message and the
    original :class:`subprocess.CalledProcessError` is chained as ``__cause__``
    via ``raise LintError(...) from exc`` (build.py:162-165).

    Note: this is raised ONLY for an *injected* runner that raises. The
    lint-local default runner uses ``check=False`` and never raises on a
    non-zero exit — that becomes :attr:`LinterCheck.passed` = ``False``.
    """


@dataclass(frozen=True, slots=True)
class LinterCheck:
    """Outcome of ONE lint command in the ID-062 suite (ID-062).

    Attributes:
        name: The pinned command name (``'ruff_check'`` |
            ``'ruff_format_check'`` | ``'mypy'``).
        passed: ``True`` iff :attr:`exit_code` == 0.
        exit_code: The command's exit code (carried verbatim, never raised by
            the default runner — ``check=False``).
        stdout_tail: Last :data:`_TAIL_LINES` lines of captured stdout.
        stderr_tail: Last :data:`_TAIL_LINES` lines of captured stderr.
    """

    name: str
    passed: bool
    exit_code: int
    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True, slots=True)
class LintResult:
    """Aggregate outcome of :func:`lint` — bounded, deterministic.

    Attributes:
        success: ``True`` iff every :class:`LinterCheck` passed
            (``all(c.passed for c in checks)``).
        checks: The per-command :class:`LinterCheck` records, in canonical
            order (ruff_check, ruff_format_check, mypy). All three are ALWAYS
            present — no short-circuit on failure.
    """

    success: bool
    checks: tuple[LinterCheck, ...]


def lint(workdir: str | Path, *, runner: Runner | None = None) -> LintResult:
    """Run the ID-062 canonical 3-command lint suite in ``workdir``.

    Issues the three commands — ``uv run ruff check .``,
    ``uv run ruff format --check .``, and ``uv run mypy packages services`` —
    IN ORDER, ALL THREE ALWAYS (no short-circuit on failure), and returns a
    :class:`LintResult` with one :class:`LinterCheck` per command.

    Args:
        workdir: Working directory to lint (a per-job worktree of the
            token-free clone). ``str`` or :class:`~pathlib.Path`.
        runner: Injectable exec runner (IMPORTED from
            :mod:`workspace_worker.exec.build`). When ``None`` (the default) a
            lint-local runner is used that closes over ``workdir``
            (cwd=workdir, ``check=False``). When supplied, it is used
            **verbatim** and receives exactly each canonical argv — it owns its
            own cwd. The SAME runner is invoked for all three commands.

    Returns:
        A :class:`LintResult` with ``success = all(c.passed for c in checks)``
        and the bounded per-command :class:`LinterCheck` records.

    Raises:
        LintError: if an *injected* runner raises
            :class:`subprocess.CalledProcessError`. The original exception is
            chained as ``__cause__``. The default runner never raises on a
            non-zero exit (``check=False``) — that path yields a
            :class:`LinterCheck` with ``passed=False`` instead.
    """
    cwd = str(workdir)

    def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        # check=False: a non-zero exit is a first-class LinterCheck field, not a
        # raised CalledProcessError (mirror of build.py:150-157). cwd=workdir is
        # set here, inside the closure, so the shared Runner type stays
        # cwd-free and workdir never appears as an argv token.
        return subprocess.run(  # noqa: S603 — trusted argv, pinned by tests
            cmd, cwd=cwd, check=False, capture_output=True, text=True
        )

    run: Runner = runner or _default_runner
    checks: list[LinterCheck] = []
    for name, argv in _LINT_COMMANDS:
        try:
            completed = run(list(argv))
        except subprocess.CalledProcessError as exc:
            raise LintError(
                f"{name} ({' '.join(argv)}) failed in {workdir}: {exc.stderr or exc}"
            ) from exc

        exit_code = completed.returncode
        checks.append(
            LinterCheck(
                name=name,
                passed=(exit_code == 0),
                exit_code=exit_code,
                stdout_tail=_tail(completed.stdout or ""),
                stderr_tail=_tail(completed.stderr or ""),
            )
        )

    return LintResult(
        success=all(c.passed for c in checks),
        checks=tuple(checks),
    )
