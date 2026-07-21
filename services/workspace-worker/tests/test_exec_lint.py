"""Tests for :mod:`workspace_worker.exec.lint` — the lint operation (SFP-64).

Two layers, mirroring ``test_exec_build.py`` (SFP-62):

* **Unit** tests inject a fake runner to assert the exact 3-command ID-062
  suite argv in order (``ruff check``, ``ruff format --check``, ``mypy`` —
  workdir NOT in argv, cwd closed over), that all three ALWAYS run (no
  short-circuit on failure), the success/any-one-fails paths (parametrized),
  the bounded tail (last 200 lines, on both stdout and stderr per command),
  the :class:`LintError` wrapping a raised :class:`~subprocess.CalledProcessError`
  with chained ``__cause__`` (parametrized over which command raises), the
  frozen+slots :class:`LintResult` and :class:`LinterCheck`, a reuse-not-redefine
  AST guard (Runner/_tail/_TAIL_LINES are IMPORTED from exec/build.py), an
  exec/__init__.py docstring-only AST guard, and scope-guard tests asserting no
  container/docker/podman token and no build/test subcommand leaks in.
* **Integration** tests run the real ``lint()`` suite against a temp-copy workdir
  and assert the SHAPE only (3 checks, pinned names in order) — NOT success,
  since a dirty tree may legitimately fail one command.

The FakeRunner here EXTENDS the build FakeRunner: it is configurable
PER-COMMAND (a mapping argv → result) so each of the 3 commands can
independently fail/raise — the build FakeRunner returns one result for all
calls, which is insufficient for a 3-command suite.

The default-runner code path (the nested ``subprocess.run`` closure) is exercised
*only* by the integration tests — the unit tests inject a FakeRunner. So if
``uv`` is absent (``requires_uv`` skips them), coverage of ``exec/lint.py``
drops below 90%; ``uv`` is therefore required in the test environment.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest
from workspace_worker.exec.lint import (
    LinterCheck,
    LintError,
    LintResult,
    lint,
)

#: Skip real-uv integration tests when ``uv`` is unavailable. Unit tests use the
#: FakeRunner and never spawn uv, so they always run. Mirror of ``requires_uv``
#: in test_exec_build.py:41-43.
requires_uv = pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv binary required for integration tests"
)

#: The pinned canonical argv (mirror of ``_LINT_COMMANDS`` in lint.py). These
#: constants are duplicated here ON PURPOSE — pinning the argv in the test means
#: a change to the module constant is detected as a test failure, not silently
#: propagated.
_RUFF_CHECK_ARGV = ["uv", "run", "ruff", "check", "."]
_RUFF_FORMAT_ARGV = ["uv", "run", "ruff", "format", "--check", "."]
_MYPY_ARGV = ["uv", "run", "mypy", "packages", "services"]

#: The exact 3-command argv sequence ``lint()`` must issue, in order.
_EXPECTED_CALLS = [_RUFF_CHECK_ARGV, _RUFF_FORMAT_ARGV, _MYPY_ARGV]

#: The pinned command names, in canonical order.
_LINT_NAMES = ["ruff_check", "ruff_format_check", "mypy"]


class FakeRunner:
    """Records every exec invocation; returns a PER-COMMAND configurable result.

    Extends the build FakeRunner (test_exec_build.py:46-76), which returns one
    canned result for all calls. Lint's suite issues 3 distinct commands, each
    of which can independently succeed/fail/raise — so this FakeRunner maps
    each command's argv to its own result via :meth:`configure`. Unconfigured
    commands default to a clean pass (``returncode=0``, empty output).
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._results: dict[
            tuple[str, ...],
            subprocess.CompletedProcess[str] | BaseException,
        ] = {}

    def configure(
        self,
        argv: list[str],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        error: BaseException | None = None,
    ) -> None:
        """Set the result for a specific command argv.

        If ``error`` is set, the runner RAISES it when this argv is issued
        (e.g. a :class:`~subprocess.CalledProcessError`). Otherwise it returns a
        :class:`~subprocess.CompletedProcess` with the given fields.
        """
        if error is not None:
            self._results[tuple(argv)] = error
        else:
            self._results[tuple(argv)] = subprocess.CompletedProcess(
                argv, returncode=returncode, stdout=stdout, stderr=stderr
            )

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        key = tuple(cmd)
        if key not in self._results:
            # Unconfigured command defaults to a clean pass.
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        result = self._results[key]
        if isinstance(result, BaseException):
            raise result
        return result


# ---------------------------------------------------------------------------
# Unit — argv shape: exact 3-command sequence in order, workdir closed over
# ---------------------------------------------------------------------------


def test_lint_runs_exact_3_command_argv_in_order(tmp_path: Path) -> None:
    runner = FakeRunner()

    lint(tmp_path / "work", runner=runner)

    # Exactly 3 calls; argv is pinned — the canonical ID-062 suite in order.
    assert runner.calls == _EXPECTED_CALLS


def test_lint_workdir_not_in_any_argv(tmp_path: Path) -> None:
    # The workdir path must never appear as an argv token — cwd is a subprocess
    # kwarg, set inside the default runner's closure (PRSpec risk #4). This is
    # what keeps the shared Runner type cwd-free.
    workdir = tmp_path / "job-64-workdir"
    runner = FakeRunner()

    lint(workdir, runner=runner)

    assert len(runner.calls) == 3
    for call in runner.calls:
        assert str(workdir) not in call
        assert "cwd" not in call


def test_lint_uses_injected_runner_verbatim(tmp_path: Path) -> None:
    # An injected runner is used as-is; each command's output is independently
    # captured in its own LinterCheck.
    runner = FakeRunner()
    runner.configure(_RUFF_CHECK_ARGV, stdout="ruff-ok")
    runner.configure(_RUFF_FORMAT_ARGV, stdout="fmt-ok")
    runner.configure(_MYPY_ARGV, stdout="mypy-ok")

    result = lint(tmp_path / "work", runner=runner)

    assert runner.calls == _EXPECTED_CALLS
    assert result.checks[0].stdout_tail == "ruff-ok"
    assert result.checks[1].stdout_tail == "fmt-ok"
    assert result.checks[2].stdout_tail == "mypy-ok"


# ---------------------------------------------------------------------------
# Unit — success / any-one-fails (parametrized) / no short-circuit
# ---------------------------------------------------------------------------


def test_lint_all_pass_success_true(tmp_path: Path) -> None:
    runner = FakeRunner()

    result = lint(tmp_path / "work", runner=runner)

    assert result.success is True
    assert len(result.checks) == 3
    assert all(c.passed for c in result.checks)


@pytest.mark.parametrize(
    "fail_index",
    [0, 1, 2],
    ids=["ruff_check_fails", "ruff_format_check_fails", "mypy_fails"],
)
def test_lint_any_one_command_fails_success_false_all_three_run(
    tmp_path: Path, fail_index: int
) -> None:
    # ANY one of the three failing → success is False. Critically, ALL THREE
    # ALWAYS run — no short-circuit on failure (PRSpec risk #4).
    fail_argv = _EXPECTED_CALLS[fail_index]
    runner = FakeRunner()
    runner.configure(fail_argv, returncode=1, stderr=f"err-{fail_index}")

    result = lint(tmp_path / "work", runner=runner)

    assert result.success is False
    # No short-circuit: all 3 commands were issued.
    assert len(runner.calls) == 3
    assert runner.calls == _EXPECTED_CALLS
    # The failing check is marked not-passed; the others passed.
    assert result.checks[fail_index].passed is False
    assert result.checks[fail_index].exit_code == 1
    for i, check in enumerate(result.checks):
        if i != fail_index:
            assert check.passed is True
            assert check.exit_code == 0


# ---------------------------------------------------------------------------
# Unit — per-check shape: names, exit codes, passed flags
# ---------------------------------------------------------------------------


def test_lint_check_names_pinned_in_order(tmp_path: Path) -> None:
    runner = FakeRunner()

    result = lint(tmp_path / "work", runner=runner)

    assert [c.name for c in result.checks] == _LINT_NAMES


def test_lint_per_check_exit_codes_carried_verbatim(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.configure(_RUFF_CHECK_ARGV, returncode=0)
    runner.configure(_RUFF_FORMAT_ARGV, returncode=1)
    runner.configure(_MYPY_ARGV, returncode=2)

    result = lint(tmp_path / "work", runner=runner)

    assert [c.exit_code for c in result.checks] == [0, 1, 2]
    assert [c.passed for c in result.checks] == [True, False, False]


# ---------------------------------------------------------------------------
# Unit — bounded tail: last 200 lines, last-N not first-N, stdout AND stderr
# ---------------------------------------------------------------------------


def test_lint_stdout_tail_bounded_to_last_200(tmp_path: Path) -> None:
    # 250 lines of stdout must be truncated to exactly the LAST 200.
    stdout = "\n".join(f"L{i}" for i in range(250)) + "\n"
    runner = FakeRunner()
    runner.configure(_RUFF_CHECK_ARGV, stdout=stdout)

    result = lint(tmp_path / "work", runner=runner)

    check = result.checks[0]  # ruff_check
    tail_lines = check.stdout_tail.splitlines()
    assert len(tail_lines) == 200  # bounded to exactly 200
    assert tail_lines[0] == "L50"  # last-N, not first-N (dropped L0..L49)
    assert tail_lines[-1] == "L249"


def test_lint_stderr_tail_also_bounded(tmp_path: Path) -> None:
    # The bound applies to BOTH streams independently, per command.
    stderr = "\n".join(f"E{i}" for i in range(300)) + "\n"
    runner = FakeRunner()
    runner.configure(_RUFF_FORMAT_ARGV, returncode=1, stderr=stderr)

    result = lint(tmp_path / "work", runner=runner)

    check = result.checks[1]  # ruff_format_check
    tail_lines = check.stderr_tail.splitlines()
    assert len(tail_lines) == 200
    assert tail_lines[0] == "E100"  # last 200 of 300
    assert tail_lines[-1] == "E299"


def test_lint_empty_output_yields_empty_tail(tmp_path: Path) -> None:
    runner = FakeRunner()

    result = lint(tmp_path / "work", runner=runner)

    for check in result.checks:
        assert check.stdout_tail == ""
        assert check.stderr_tail == ""


# ---------------------------------------------------------------------------
# Unit — injected runner raising CalledProcessError -> LintError, chained cause
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raise_index",
    [0, 1, 2],
    ids=["ruff_check_raises", "ruff_format_check_raises", "mypy_raises"],
)
def test_lint_called_process_error_wrapped_with_chained_cause(
    tmp_path: Path, raise_index: int
) -> None:
    # An injected runner that RAISES CalledProcessError is wrapped to LintError
    # with the original chained as __cause__ (mirror build.py:162-165).
    raise_argv = _EXPECTED_CALLS[raise_index]
    err = subprocess.CalledProcessError(returncode=1, cmd=raise_argv, stderr="lint failure")
    runner = FakeRunner()
    runner.configure(raise_argv, error=err)

    with pytest.raises(LintError, match="failed") as exc_info:
        lint(tmp_path / "work", runner=runner)

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)
    assert "lint failure" in str(exc_info.value)


def test_lint_called_process_error_safe_when_stderr_none(tmp_path: Path) -> None:
    # CalledProcessError(stderr=None) must not blow up formatting the message.
    err = subprocess.CalledProcessError(returncode=2, cmd=_RUFF_CHECK_ARGV, stderr=None)
    runner = FakeRunner()
    runner.configure(_RUFF_CHECK_ARGV, error=err)

    with pytest.raises(LintError) as exc_info:
        lint(tmp_path / "work", runner=runner)

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)


# ---------------------------------------------------------------------------
# Unit — LintResult + LinterCheck are frozen + slots
# ---------------------------------------------------------------------------


def test_lint_result_is_frozen(tmp_path: Path) -> None:
    result = lint(tmp_path / "work", runner=FakeRunner())

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = False  # type: ignore[misc]


def test_lint_result_uses_slots(tmp_path: Path) -> None:
    result = lint(tmp_path / "work", runner=FakeRunner())

    with pytest.raises(AttributeError):
        result.unexpected = "boom"  # type: ignore[attr-defined]


def test_linter_check_is_frozen() -> None:
    check = LinterCheck(name="ruff_check", passed=True, exit_code=0, stdout_tail="", stderr_tail="")

    with pytest.raises(dataclasses.FrozenInstanceError):
        check.exit_code = 99  # type: ignore[misc]


def test_linter_check_uses_slots() -> None:
    check = LinterCheck(name="mypy", passed=False, exit_code=1, stdout_tail="", stderr_tail="")

    with pytest.raises(AttributeError):
        check.unexpected = "boom"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Unit — reuse-not-redefine AST guard (Runner/_tail/_TAIL_LINES imported)
# ---------------------------------------------------------------------------


def test_lint_imports_not_redefines_runner_tail_tail_lines() -> None:
    # Runner, _tail, _TAIL_LINES must be IMPORTED from exec/build.py, NOT
    # redefined in lint.py. Asserting via the AST is robust — it catches
    # assignments, class defs, and function defs at module level.
    import ast

    import workspace_worker.exec.lint as lint_mod

    lint_path = Path(lint_mod.__file__)
    tree = ast.parse(lint_path.read_text())

    # Collect names DEFINED at module level (not imported).
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
    for name in ("Runner", "_tail", "_TAIL_LINES"):
        assert name not in defined, (
            f"{name} must be IMPORTED from exec/build.py, not redefined in lint.py"
        )

    # Verify they ARE imported from workspace_worker.exec.build.
    import_found = False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == ("workspace_worker.exec.build"):
            imported = {alias.name for alias in node.names}
            if {"Runner", "_tail", "_TAIL_LINES"}.issubset(imported):
                import_found = True
                break
    assert import_found, (
        "Runner/_tail/_TAIL_LINES must be imported from workspace_worker.exec.build"
    )


# ---------------------------------------------------------------------------
# Unit — scope guards: no container argv, only the 3 lint commands
# ---------------------------------------------------------------------------


def test_no_container_argv_token(tmp_path: Path) -> None:
    # Anti-scope-creep guard vs SFP-65: container isolation must not leak into
    # this slice — no container/docker/podman token is ever issued.
    runner = FakeRunner()
    lint(tmp_path / "work", runner=runner)

    for call in runner.calls:
        flat = " ".join(call).lower()
        for token in ("container", "docker", "podman"):
            assert token not in flat


def test_only_lint_commands_no_build_or_test(tmp_path: Path) -> None:
    # Anti-scope-creep guard vs SFP-62/63: this slice runs ONLY the 3 canonical
    # lint commands — never `uv sync` (build) or `pytest` (test).
    runner = FakeRunner()
    lint(tmp_path / "work", runner=runner)

    for call in runner.calls:
        assert "sync" not in call  # build (SFP-62)
        assert "pytest" not in call  # test (SFP-63)
    # The lint tools ARE present.
    flat = " ".join(" ".join(c) for c in runner.calls)
    assert "ruff" in flat
    assert "mypy" in flat


def test_exec_package_init_is_docstring_only() -> None:
    # exec/__init__.py is a docstring-only OWN module owned by SFP-62: SFP-64
    # must NOT have touched it. Asserting via the AST (not hasattr) is robust.
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
# Integration — real uv lint suite against a temp-copy workdir (shape only)
# ---------------------------------------------------------------------------


def _workspace_root() -> Path:
    """Walk up from this test file to the directory holding ``uv.lock``.

    That directory is the uv workspace root the lint commands operate on.
    """
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "uv.lock").is_file() and (parent / "pyproject.toml").is_file():
            return parent
    raise AssertionError("uv workspace root (uv.lock + pyproject.toml) not found")


def _temp_copy_workdir(tmp_path: Path) -> Path:
    """Materialise a temp copy of the repo workspace as the lint workdir.

    The copy excludes heavy/non-essential trees (``.git``, the warm ``.venv``,
    caches, the ``.claude`` worktrees). This keeps the copy cheap while leaving a
    fully-resolvable uv workspace. Critically, the copy has NO ``.venv`` yet,
    proving the default runner set ``cwd=workdir`` when it resolves there.

    Operating on a copy also guarantees the committed tree is never mutated by
    the integration run. Mirror of ``_temp_copy_workdir`` in test_exec_build.py.
    """
    src = _workspace_root()
    dest = tmp_path / "lint-copy"
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".coverage",
            "htmlcov",
            "*.egg-info",
            "dist",
            "build",
            ".claude",
        ),
        symlinks=False,
    )
    return dest


@requires_uv
def test_integration_lint_runs_real_suite_shape_only(tmp_path: Path) -> None:
    workdir = _temp_copy_workdir(tmp_path)

    # Default runner (runner=None) → the nested subprocess.run closure with
    # cwd=workdir, check=False. Real uv runs the 3-command suite from the copy.
    result = lint(workdir)

    # Assert SHAPE only — a dirty tree may legitimately fail one command.
    # Do NOT assert success=True.
    assert isinstance(result, LintResult)
    assert len(result.checks) == 3
    assert [c.name for c in result.checks] == _LINT_NAMES
    for check in result.checks:
        assert isinstance(check, LinterCheck)
        assert check.passed == (check.exit_code == 0)
