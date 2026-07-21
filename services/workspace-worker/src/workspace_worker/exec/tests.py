"""Local Execution Engine — ``tests`` operation (SFP-63 / ID-060 / MAS §9.6).

Provides the Workspace Worker's Local Execution Engine TEST operation —
``run_tests`` — which runs ``uv run pytest --cov`` in a given working directory
on the **HOST** (Phase A; ID-060) and returns a bounded, deterministic
:class:`TestResult` carrying the exit code, the parsed coverage percentage, and
the bounded stdout/stderr tails.

This slice MIRRORS :mod:`workspace_worker.exec.build` (SFP-62) line for line and
diverges only on:

* the argv — ``['uv', 'run', 'pytest', '--cov']`` instead of
  ``['uv', 'sync', '--all-packages']``;
* the result fields — :class:`TestResult` adds ``coverage_pct`` (a derived float
  parsed from the pytest-cov ``TOTAL`` line in the already-bounded stdout tail);
* a :func:`_parse_coverage_pct` helper that reports the coverage percentage.

Everything else — the shared :data:`Runner` type, :func:`_tail`,
:data:`_TAIL_LINES`, the frozen+slots result dataclass, the chained-``__cause__``
error, the cwd-closing default runner with ``check=False`` — is **imported** from
:mod:`workspace_worker.exec.build` and reused verbatim, NOT redefined. Redefining
would break the shared-Runner contract the whole exec layer depends on (SFP-62
introduced that surface precisely so SFP-63/64 reuse it); a unit test asserts
these names are imported, not re-defined.

The 90% coverage gate is enforced OUTSIDE this module by the workspace
``[tool.coverage.report] fail_under=90`` (pyproject.toml): pytest-cov
non-zero-exits when coverage falls below it, so ``success = (exit_code == 0)``
folds the gate in. :attr:`TestResult.coverage_pct` is REPORT-only — it never
drives a pass/fail decision; a unit test pins that a ``TOTAL 92%`` line with a
non-zero exit still yields ``success=False``.

The working directory is a per-job git worktree (SFP-56 / ID-033) of the
token-free clone (SFP-55) — no credentials traverse this module, and there is no
container boundary (container isolation is SFP-65 / Phase B; a scope-guard test
asserts no container argv token leaks into this slice).

Validation profile: ``standard-internal-infra`` (LEVEL_1_INTERNAL, ID-049) — a
host-subprocess internal slice mirroring the already-reviewed SFP-62, with no new
trust boundary, no credential surface, and no unbounded capture (tails bounded at
:data:`_TAIL_LINES`; ``coverage_pct`` is a derived scalar).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from workspace_worker.exec.build import _TAIL_LINES, Runner, _tail

__all__ = ["TestError", "TestResult", "run_tests"]

#: The exact ``uv`` argv this operation issues. ``--cov`` (no source arg) defers
#: source selection to ``[tool.coverage.run] source`` (already enumerated in the
#: workspace pyproject.toml) and the 90% gate to ``[tool.coverage.report]
#: fail_under=90`` — pytest-cov non-zero-exits below it. Pinned by the unit test,
#: mirroring ``_BUILD_ARGV``'s pinning in build.py:74 / test_exec_build.py:91.
_TEST_ARGV = ["uv", "run", "pytest", "--cov"]


def _parse_coverage_pct(stdout_tail: str) -> float | None:
    """Return the pytest-cov ``TOTAL`` coverage percentage, or ``None`` on miss.

    Scans the (already tail-bounded) ``stdout_tail`` for a line whose first
    token is ``TOTAL`` and whose last token is a trailing ``<int>%`` (tolerant of
    column widths — variable statement/miss counts). Returns the percentage as a
    :class:`float`, or ``None`` when no such line is found.

    This helper REPORTS only; it does NOT enforce the 90% gate (the workspace
    ``fail_under=90`` does, folded into the exit code). Returning ``None`` —
    never raising — on a miss means a future pytest-cov table-format change
    degrades gracefully (:attr:`TestResult.coverage_pct` is typed
    ``float | None`` for exactly this reason).
    """
    for line in stdout_tail.splitlines():
        tokens = line.split()
        if len(tokens) >= 2 and tokens[0] == "TOTAL" and tokens[-1].endswith("%"):
            try:
                return float(tokens[-1][:-1])
            except ValueError:
                continue
    return None


class TestError(RuntimeError):
    """Raised when an injected runner reports ``uv run pytest`` failing by raising.

    Mirrors :class:`~workspace_worker.exec.build.BuildError`: the failing command
    context (stderr) is surfaced in the message and the original
    :class:`subprocess.CalledProcessError` is chained as ``__cause__`` via
    ``raise TestError(...) from exc`` (build.py:163-165, worktree.py:193-197).

    Note: this is raised ONLY for an *injected* runner that raises. The
    run_tests-local default runner uses ``check=False`` and never raises on a
    non-zero exit — that becomes :attr:`TestResult.success` = ``False``.
    """

    # pytest collects classes named ``Test*``; these are NOT test classes — they
    # are the result/error types this module exports. ``__test__ = False`` is the
    # idiomatic opt-out so pytest skips them without a collection warning.
    __test__ = False


@dataclass(frozen=True, slots=True)
class TestResult:
    """Outcome of :func:`run_tests` — bounded, deterministic.

    Attributes:
        success: ``True`` iff :attr:`exit_code` == 0. This folds in the 90%
            coverage gate: pytest-cov non-zero-exits when coverage falls below
            ``fail_under=90`` (workspace pyproject.toml), so a coverage failure
            is a non-zero exit and therefore ``success=False``.
        exit_code: The ``uv run pytest`` process exit code (carried verbatim,
            never raised by the default runner — ``check=False``).
        coverage_pct: The pytest-cov ``TOTAL`` coverage percentage parsed from
            :attr:`stdout_tail`, or ``None`` when the ``TOTAL`` line could not be
            parsed. REPORT-only — never drives the pass/fail decision (the exit
            code does).
        stdout_tail: Last :data:`_TAIL_LINES` lines of captured stdout.
        stderr_tail: Last :data:`_TAIL_LINES` lines of captured stderr.
    """

    # Not a pytest test class despite the ``Test*`` name (see TestError.__test__).
    __test__: ClassVar[bool] = False

    success: bool
    exit_code: int
    coverage_pct: float | None
    stdout_tail: str
    stderr_tail: str


def run_tests(workdir: str | Path, *, runner: Runner | None = None) -> TestResult:
    """Run ``uv run pytest --cov`` in ``workdir``; return a bounded result.

    Issues exactly ``['uv', 'run', 'pytest', '--cov']`` with the process cwd set
    to ``workdir`` (Phase A HOST execution; container isolation is SFP-65).

    The 90% coverage gate is folded into :attr:`TestResult.success`: pytest-cov
    non-zero-exits when coverage falls below the workspace
    ``[tool.coverage.report] fail_under=90``, so ``success = (exit_code == 0)``
    already reflects a coverage failure. :attr:`TestResult.coverage_pct` is parsed
    from the pytest-cov ``TOTAL`` line for reporting only — it never drives the
    pass/fail decision (a unit test pins that a ``TOTAL 92%`` line with a
    non-zero exit still yields ``success=False``).

    Args:
        workdir: Working directory to run tests in (a per-job worktree of the
            token-free clone). ``str`` or :class:`~pathlib.Path`.
        runner: Injectable exec runner (mirror of :func:`build`'s ``runner``
            param). When ``None`` (the default) a run_tests-local runner is used
            that closes over ``workdir`` (cwd=workdir, ``check=False``). When
            supplied, it is used **verbatim** and receives exactly
            ``['uv', 'run', 'pytest', '--cov']`` — it owns its own cwd.

    Returns:
        A :class:`TestResult` with ``success = (exit_code == 0)``, the parsed
        ``coverage_pct`` (or ``None``), and the bounded stdout/stderr tails.

    Raises:
        TestError: if an *injected* runner raises
            :class:`subprocess.CalledProcessError`. The original exception is
            chained as ``__cause__``. The default runner never raises on a
            non-zero exit (``check=False``) — that path yields a
            :class:`TestResult` with ``success=False`` instead.
    """
    cwd = str(workdir)

    def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        # check=False: a non-zero exit is a first-class TestResult field, not a
        # raised CalledProcessError (divergence from the repo slices — mirrors
        # build._default_runner, build.py:150-157). cwd=workdir is set here,
        # inside the closure, so the shared Runner type stays cwd-free and
        # reusable for SFP-64.
        return subprocess.run(  # noqa: S603 — trusted argv, pinned by tests
            cmd, cwd=cwd, check=False, capture_output=True, text=True
        )

    run: Runner = runner or _default_runner
    try:
        completed = run(list(_TEST_ARGV))
    except subprocess.CalledProcessError as exc:
        raise TestError(f"uv run pytest --cov failed in {workdir}: {exc.stderr or exc}") from exc

    exit_code = completed.returncode
    stdout_tail = _tail(completed.stdout or "", _TAIL_LINES)
    return TestResult(
        success=exit_code == 0,
        exit_code=exit_code,
        coverage_pct=_parse_coverage_pct(stdout_tail),
        stdout_tail=stdout_tail,
        stderr_tail=_tail(completed.stderr or "", _TAIL_LINES),
    )
