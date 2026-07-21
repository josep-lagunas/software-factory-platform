"""Tests for :mod:`workspace_worker.exec.tests` — the test operation (SFP-63).

Two layers, mirroring ``test_exec_build.py``:

* **Unit** tests inject a fake runner to assert the exact ``uv`` argv
  (``['uv', 'run', 'pytest', '--cov']`` — workdir NOT in argv, cwd closed over),
  that an injected runner is used verbatim, the success/non-zero-exit paths, the
  bounded tail (last 200 lines, on both stdout and stderr), the
  :class:`TestError` wrapping a raised :class:`~subprocess.CalledProcessError`
  with chained ``__cause__``, the frozen+slots :class:`TestResult`, the
  ``coverage_pct`` parsing (``TOTAL`` line found → ``float``; absent → ``None``),
  and the REPORT-only contract (a non-zero exit with a ``TOTAL 92%`` line still
  yields ``success=False`` — proving the gate is the exit code, not the parsed
  pct). A reuse-not-redefine AST guard asserts ``Runner``/``_tail``/
  ``_TAIL_LINES`` are IMPORTED from ``exec.build`` (never re-defined). Scope-guard
  tests assert no container/docker/podman token and no ``sync``/``ruff``/``mypy``
  subcommand leaks in; ``exec/__init__.py`` is re-asserted docstring-only via AST.
* **Integration** tests run the real ``uv run pytest --cov`` against a minimal
  self-contained fixture workspace (see :func:`_fixture_workspace` for why a
  fixture is used instead of a temp-copy of the repo) and assert ``success`` is
  ``True`` and ``coverage_pct`` is populated — the only observable that proves
  the default runner set ``cwd=workdir`` (a ``.venv`` lands inside the workdir)
  AND the coverage parser works against real pytest-cov output (a FakeRunner
  can't reach either).

The default-runner code path (the nested ``subprocess.run`` closure) is exercised
*only* by the integration tests — the unit tests inject a FakeRunner. So if
``uv`` is absent (``requires_uv`` skips them), coverage of ``exec/tests.py``
drops below 90%; ``uv`` is therefore required in the test environment.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest
from workspace_worker.exec.tests import (
    TestError,
    TestResult,
    run_tests,
)

#: Skip real-uv integration tests when ``uv`` is unavailable. Unit tests use the
#: FakeRunner and never spawn uv, so they always run. Mirror of ``requires_uv``
#: in test_exec_build.py:41-43. ``uv`` present → ``uv run`` syncs the workspace
#: dev group (pytest-cov included), so this single guard covers both binaries.
requires_uv = pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv binary required for integration tests"
)


class FakeRunner:
    """Records every exec invocation; returns a CONFIGURABLE canned result.

    Mirrors the FakeRunner in test_exec_build.py:46-76. Success/failure paths
    depend on the captured ``returncode``/``stdout``/``stderr`` content, so those
    are configurable here. Set ``error`` to make the runner *raise* (e.g. a
    :class:`subprocess.CalledProcessError`) instead of returning.
    """

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        error: BaseException | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._error = error

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        if self._error is not None:
            raise self._error
        return subprocess.CompletedProcess(
            cmd, returncode=self._returncode, stdout=self._stdout, stderr=self._stderr
        )


# A realistic pytest-cov TOTAL line tail — used by several parse tests. The
# column widths vary; the parser anchors on the `TOTAL` token + trailing `%`.
_COV_TOTAL_TAIL = (
    "Name                              Stmts   Miss  Cover\n"
    "-----------------------------------------------------\n"
    "workspace_worker/exec/tests.py       40      0   100%\n"
    "-----------------------------------------------------\n"
    "TOTAL                                40      0   92%\n"
)


# ---------------------------------------------------------------------------
# Unit — argv shape: exact argv, workdir closed over (not in argv), verbatim use
# ---------------------------------------------------------------------------


def test_run_tests_runs_exact_uv_run_pytest_cov_argv(tmp_path: Path) -> None:
    runner = FakeRunner()

    run_tests(tmp_path / "work", runner=runner)

    # Exactly one call; argv is pinned — workdir is NOT a token (cwd is closed
    # over inside the default runner, not passed as an argv element).
    assert runner.calls == [["uv", "run", "pytest", "--cov"]]


def test_run_tests_workdir_not_in_argv(tmp_path: Path) -> None:
    # The workdir path must never appear as an argv token — cwd is a subprocess
    # kwarg, set inside the default runner's closure, not passed to uv. This is
    # what keeps the shared Runner type cwd-free (PRSpec risk #4).
    workdir = tmp_path / "job-7-workdir"
    runner = FakeRunner()

    run_tests(workdir, runner=runner)

    assert len(runner.calls) == 1
    assert str(workdir) not in runner.calls[0]
    assert "cwd" not in runner.calls[0]


def test_run_tests_uses_injected_runner_verbatim(tmp_path: Path) -> None:
    # An injected runner is used as-is (no wrapping, no extra argv).
    runner = FakeRunner(returncode=0, stdout="collected")

    result = run_tests(tmp_path / "work", runner=runner)

    assert runner.calls == [["uv", "run", "pytest", "--cov"]]
    assert result.stdout_tail == "collected"


# ---------------------------------------------------------------------------
# Unit — success / non-zero exit (check=False: exit code is a result, not raise)
# ---------------------------------------------------------------------------


def test_run_tests_success_exit_zero(tmp_path: Path) -> None:
    runner = FakeRunner(returncode=0, stdout="3 passed", stderr="")

    result = run_tests(tmp_path / "work", runner=runner)

    assert result == TestResult(
        success=True,
        exit_code=0,
        coverage_pct=None,
        stdout_tail="3 passed",
        stderr_tail="",
    )


@pytest.mark.parametrize("exit_code", [1, 2, 130])
def test_run_tests_nonzero_exit_is_a_result_not_raised(tmp_path: Path, exit_code: int) -> None:
    # check=False (default runner contract): a non-zero exit is captured into
    # TestResult, never raised. exit_code is carried verbatim (1 = generic
    # failure, 2 = usage, 130 = SIGINT/128+2).
    runner = FakeRunner(returncode=exit_code, stdout="partial", stderr=f"err-{exit_code}")

    result = run_tests(tmp_path / "work", runner=runner)

    assert result.success is False
    assert result.exit_code == exit_code  # verbatim, no normalization
    assert result.stderr_tail == f"err-{exit_code}"


# ---------------------------------------------------------------------------
# Unit — coverage_pct parse: TOTAL line found, absent, and REPORT-only contract
# ---------------------------------------------------------------------------


def test_run_tests_coverage_pct_parsed_from_total_line(tmp_path: Path) -> None:
    # The pytest-cov TOTAL line in the (bounded) stdout tail is parsed to a float.
    runner = FakeRunner(returncode=0, stdout=_COV_TOTAL_TAIL)

    result = run_tests(tmp_path / "work", runner=runner)

    assert result.success is True
    assert result.coverage_pct == 92.0


def test_run_tests_coverage_pct_none_when_no_total(tmp_path: Path) -> None:
    # When the stdout carries no TOTAL line (e.g. --cov absent, or a future
    # pytest-cov format change), coverage_pct is None. success is still derived
    # purely from exit_code — a None never drives pass/fail.
    runner = FakeRunner(returncode=0, stdout="3 passed in 0.01s")

    result = run_tests(tmp_path / "work", runner=runner)

    assert result.success is True  # success is exit_code-only
    assert result.coverage_pct is None


def test_run_tests_coverage_pct_skips_malformed_total(tmp_path: Path) -> None:
    # A TOTAL line whose trailing ``%`` token is not numeric (e.g. ``N/A%``) is
    # skipped — the parser returns None rather than raising. Guards the
    # ``except ValueError`` branch: a degenerate line degrades gracefully.
    malformed = "TOTAL  40  0  N/A%\n"
    runner = FakeRunner(returncode=0, stdout=malformed)

    result = run_tests(tmp_path / "work", runner=runner)

    assert result.coverage_pct is None


def test_run_tests_coverage_pct_report_only_nonzero_exit(tmp_path: Path) -> None:
    # ANTI-GAMING: a non-zero exit (e.g. coverage < fail_under forced a
    # non-zero exit) WITH a `TOTAL 92%` line still yields success=False. This
    # proves the 90% gate is folded into exit_code (via pyproject fail_under=90),
    # NOT enforced from the parsed coverage_pct — the pct is REPORT-only.
    runner = FakeRunner(returncode=1, stdout=_COV_TOTAL_TAIL, stderr="coverage")

    result = run_tests(tmp_path / "work", runner=runner)

    assert result.success is False  # gate is the exit code, not the parsed pct
    assert result.exit_code == 1
    assert result.coverage_pct == 92.0  # reported, even though success is False


# ---------------------------------------------------------------------------
# Unit — bounded tail: last 200 lines, last-N not first-N, stdout AND stderr
# ---------------------------------------------------------------------------


def test_run_tests_stdout_tail_bounded_to_last_200(tmp_path: Path) -> None:
    # 250 lines of stdout must be truncated to exactly the LAST 200.
    stdout = "\n".join(f"L{i}" for i in range(250)) + "\n"
    runner = FakeRunner(returncode=0, stdout=stdout)

    result = run_tests(tmp_path / "work", runner=runner)

    tail_lines = result.stdout_tail.splitlines()
    assert len(tail_lines) == 200  # bounded to exactly 200
    assert tail_lines[0] == "L50"  # last-N, not first-N (dropped L0..L49)
    assert tail_lines[-1] == "L249"


def test_run_tests_stderr_tail_also_bounded(tmp_path: Path) -> None:
    # The bound applies to BOTH streams independently.
    stderr = "\n".join(f"E{i}" for i in range(300)) + "\n"
    runner = FakeRunner(returncode=1, stderr=stderr)

    result = run_tests(tmp_path / "work", runner=runner)

    tail_lines = result.stderr_tail.splitlines()
    assert len(tail_lines) == 200
    assert tail_lines[0] == "E100"  # last 200 of 300
    assert tail_lines[-1] == "E299"


def test_run_tests_empty_output_yields_empty_tail(tmp_path: Path) -> None:
    runner = FakeRunner(returncode=0, stdout="", stderr="")

    result = run_tests(tmp_path / "work", runner=runner)

    assert result.stdout_tail == ""
    assert result.stderr_tail == ""


# ---------------------------------------------------------------------------
# Unit — injected runner raising CalledProcessError -> TestError, chained cause
# ---------------------------------------------------------------------------


def test_run_tests_called_process_error_wrapped_with_chained_cause(
    tmp_path: Path,
) -> None:
    # An injected runner that RAISES CalledProcessError is wrapped to TestError
    # with the original chained as __cause__ (mirrors worktree.py:193-197).
    err = subprocess.CalledProcessError(
        returncode=1, cmd=["uv", "run", "pytest", "--cov"], stderr="collection error"
    )
    runner = FakeRunner(error=err)

    with pytest.raises(TestError, match="uv run pytest --cov failed") as exc_info:
        run_tests(tmp_path / "work", runner=runner)

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)
    assert "collection error" in str(exc_info.value)


def test_run_tests_called_process_error_safe_when_stderr_none(
    tmp_path: Path,
) -> None:
    # CalledProcessError(stderr=None) must not blow up formatting the message.
    err = subprocess.CalledProcessError(returncode=2, cmd=["uv", "run", "pytest"], stderr=None)
    runner = FakeRunner(error=err)

    with pytest.raises(TestError) as exc_info:
        run_tests(tmp_path / "work", runner=runner)

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)


# ---------------------------------------------------------------------------
# Unit — TestResult is frozen + slots (mirror BuildResult discipline)
# ---------------------------------------------------------------------------


def test_run_tests_result_is_frozen(tmp_path: Path) -> None:
    result = run_tests(tmp_path / "work", runner=FakeRunner(returncode=0))

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.exit_code = 99  # type: ignore[misc]


def test_run_tests_result_uses_slots(tmp_path: Path) -> None:
    result = run_tests(tmp_path / "work", runner=FakeRunner(returncode=0))

    # slots=True forbids setting attributes that are not declared fields.
    with pytest.raises(AttributeError):
        result.unexpected = "boom"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Unit — reuse-not-redefine: Runner/_tail/_TAIL_LINES IMPORTED from exec.build
# ---------------------------------------------------------------------------


def test_reuses_runner_tail_tail_lines_from_build() -> None:
    # The shared Runner type, _tail, and _TAIL_LINES must be IMPORTED from
    # exec.build — NOT re-defined in tests.py. Redefining would break the
    # shared-Runner contract the whole exec layer depends on (SFP-62 introduced
    # that surface so SFP-63/64 reuse it). Asserting via the AST is robust — a
    # name resolved at runtime via import would mask a re-definition.
    import ast

    import workspace_worker.exec.tests as tests_mod

    src = Path(tests_mod.__file__).read_text()
    tree = ast.parse(src)

    expected = {"Runner", "_tail", "_TAIL_LINES"}

    # 1. All three names are imported FROM exec.build.
    imported_from_build: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "workspace_worker.exec.build":
            imported_from_build.update(alias.name for alias in node.names)
    assert expected <= imported_from_build, (
        f"expected {expected} imported from workspace_worker.exec.build, got {imported_from_build}"
    )

    # 2. None of them are re-defined at module level (no Assign / AnnAssign /
    #    FunctionDef / ClassDef binds these names).
    redefined: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in expected:
            redefined.add(node.name)
        if isinstance(node, ast.ClassDef) and node.name in expected:
            redefined.add(node.name)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in expected:
                    redefined.add(tgt.id)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in expected:
                redefined.add(node.target.id)
    assert not redefined, f"shared names re-defined in tests.py: {redefined}"


# ---------------------------------------------------------------------------
# Unit — scope guards: no container argv, only the pytest subcommand
# ---------------------------------------------------------------------------


def test_no_container_argv_token(tmp_path: Path) -> None:
    # Anti-scope-creep guard vs SFP-65: container isolation must not leak into
    # this slice — no container/docker/podman token is ever issued.
    runner = FakeRunner()
    run_tests(tmp_path / "work", runner=runner)

    flat = " ".join(runner.calls[0]).lower()
    for token in ("container", "docker", "podman"):
        assert token not in flat


def test_only_pytest_subcommand_no_sync_or_lint(tmp_path: Path) -> None:
    # Anti-scope-creep guard vs SFP-62/64: this slice runs ONLY `uv run pytest`
    # — never `uv sync` (build, SFP-62) or `ruff`/`mypy` (lint, SFP-64).
    runner = FakeRunner()
    run_tests(tmp_path / "work", runner=runner)

    argv = runner.calls[0]
    assert argv[0] == "uv"
    assert argv[1] == "run"
    assert argv[2] == "pytest"  # the only test runner this slice owns
    assert argv[3] == "--cov"
    assert "sync" not in argv
    assert "ruff" not in argv
    assert "mypy" not in argv


def test_exec_package_init_is_docstring_only() -> None:
    # exec/__init__.py is a docstring-only OWN module (owned by SFP-62): it must
    # NOT auto-export the operation symbols (callers import the submodule
    # explicitly). SFP-63 must not touch it. Asserting via the AST (not hasattr)
    # is robust — importing a submodule sets it as a package attribute as a side
    # effect, which would mask an auto-export. Copied from test_exec_build.py.
    import ast

    import workspace_worker.exec as exec_pkg

    init_path = Path(exec_pkg.__file__)
    assert init_path.name == "__init__.py"
    tree = ast.parse(init_path.read_text())
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assigns = [n for n in tree.body if isinstance(n, ast.Assign)]
    assert imports == []  # no re-exports / auto-exports
    assert assigns == []  # no __all__, no bound symbols — docstring only


# ---------------------------------------------------------------------------
# Integration — real uv run pytest --cov against a minimal fixture workspace
# ---------------------------------------------------------------------------


def _fixture_workspace(tmp_path: Path) -> Path:
    """A minimal, self-contained pytest workspace for integration testing.

    Creates a tiny uv project (one passing test + coverage configured) so
    ``run_tests`` issues the real ``uv run pytest --cov`` against it. This proves
    the two integration points a FakeRunner cannot reach:

    * the **default-runner closure** — ``subprocess.run(..., cwd=workdir)`` — by
      observing a fresh ``.venv`` land *inside* the fixture workdir (the only
      observable that proves cwd was set correctly); and
    * the **coverage parser** — :func:`_parse_coverage_pct` — by reading the
      percentage off a real pytest-cov ``TOTAL`` line.

    A minimal fixture is used instead of a temp-copy of the SFP repo (the pattern
    test_exec_build.py uses) because the SFP workspace's full-root ``uv run
    pytest`` currently hits a *pre-existing* multi-package ``tests/`` collection
    collision (``sfp-messaging``/``sfp-observability`` — ``ModuleNotFoundError: No
    module named 'tests.test_bus'``) that makes it exit 2 regardless of this
    module. That collision is unrelated to SFP-63 and is flagged for a separate
    fix; the fixture isolates this slice's contract from it.
    """
    workdir = tmp_path / "fixture-workspace"
    src = workdir / "src" / "fixture_pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "mathlib.py").write_text("def half(n: int) -> int:\n    return n // 2\n")
    tests = workdir / "tests"
    tests.mkdir()
    (tests / "test_mathlib.py").write_text(
        "from fixture_pkg.mathlib import half\n\ndef test_half():\n    assert half(10) == 5\n"
    )
    (workdir / "pyproject.toml").write_text(
        "[project]\n"
        'name = "fixture-pkg"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.13"\n\n'
        "[dependency-groups]\n"
        'dev = ["pytest>=8", "pytest-cov>=6"]\n\n'
        "[tool.pytest.ini_options]\n"
        'pythonpath = ["src"]\n\n'
        "[tool.coverage.run]\n"
        'source = ["src/fixture_pkg"]\n\n'
        "[tool.coverage.report]\n"
        "fail_under = 90\n"
    )
    return workdir


@requires_uv
def test_integration_run_tests_succeeds_and_reports_coverage(
    tmp_path: Path,
) -> None:
    workdir = _fixture_workspace(tmp_path)
    assert not (workdir / ".venv").exists()  # precondition: no venv yet

    # Default runner (runner=None) → the nested subprocess.run closure with
    # cwd=workdir, check=False. Real uv resolves+syncs a fresh .venv INSIDE the
    # workdir, runs pytest --cov, and pytest-cov prints a real TOTAL line.
    result = run_tests(workdir)

    assert result.success is True
    assert result.exit_code == 0
    # coverage_pct parsed from a real pytest-cov TOTAL line (not a FakeRunner
    # canned string). The fixture's one test fully covers mathlib.half, so the
    # gate (fail_under=90) is met → exit 0 → success.
    assert result.coverage_pct is not None
    assert isinstance(result.coverage_pct, float)
    assert result.coverage_pct >= 90.0
    # The venv landed INSIDE the workdir — the only observable that proves the
    # default runner set cwd=workdir (a FakeRunner cannot reach this).
    assert (workdir / ".venv").is_dir()
