"""Tests for :mod:`workspace_worker.exec.build` — the build operation (SFP-62).

Two layers, mirroring ``test_repo_worktree.py``:

* **Unit** tests inject a fake runner to assert the exact ``uv`` argv
  (``['uv', 'sync', '--all-packages']`` — workdir NOT in argv, cwd closed over),
  that an injected runner is used verbatim, the success/non-zero-exit paths,
  the bounded tail (last 200 lines, on both stdout and stderr), the
  :class:`BuildError` wrapping a raised :class:`~subprocess.CalledProcessError`
  with chained ``__cause__``, and the frozen+slots :class:`BuildResult`. A
  scope-guard test asserts no container/docker/podman token and no test/lint
  subcommand leaks in (those are SFP-63/64/65).
* **Integration** tests run the real ``uv sync --all-packages`` against a
  temp-copy workdir and assert ``success`` is ``True`` and a ``.venv`` is created
  *inside* the workdir — the only observable that proves the default runner set
  ``cwd=workdir`` (a FakeRunner can't reach it).

The default-runner code path (the nested ``subprocess.run`` closure) is exercised
*only* by the integration tests — the unit tests inject a FakeRunner. So if
``uv`` is absent (``requires_uv`` skips them), coverage of ``exec/build.py``
drops below 90%; ``uv`` is therefore required in the test environment.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest
from workspace_worker.exec.build import (
    BuildError,
    BuildResult,
    build,
)

#: Skip real-uv integration tests when ``uv`` is unavailable. Unit tests use the
#: FakeRunner and never spawn uv, so they always run. Mirror of
#: ``requires_git`` in test_repo_worktree.py:39-41.
requires_uv = pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv binary required for integration tests"
)


class FakeRunner:
    """Records every exec invocation; returns a CONFIGURABLE canned result.

    Unlike the worktree FakeRunner (which hardcodes ``returncode=0``), build's
    success/failure paths depend on the captured ``returncode``/``stdout``/
    ``stderr`` content, so those are configurable here. Set ``error`` to make the
    runner *raise* (e.g. a :class:`subprocess.CalledProcessError`) instead of
    returning.
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


# ---------------------------------------------------------------------------
# Unit — argv shape: exact argv, workdir closed over (not in argv), verbatim use
# ---------------------------------------------------------------------------


def test_build_runs_exact_uv_sync_argv(tmp_path: Path) -> None:
    runner = FakeRunner()

    build(tmp_path / "work", runner=runner)

    # Exactly one call; argv is pinned — workdir is NOT a token (cwd is closed
    # over inside the default runner, not passed as an argv element).
    assert runner.calls == [["uv", "sync", "--all-packages"]]


def test_build_workdir_not_in_argv(tmp_path: Path) -> None:
    # The workdir path must never appear as an argv token — cwd is a subprocess
    # kwarg, set inside the default runner's closure, not passed to uv. This is
    # what keeps the shared Runner type cwd-free (PRSpec risk #4).
    workdir = tmp_path / "job-7-workdir"
    runner = FakeRunner()

    build(workdir, runner=runner)

    assert len(runner.calls) == 1
    assert str(workdir) not in runner.calls[0]
    assert "cwd" not in runner.calls[0]


def test_build_uses_injected_runner_verbatim(tmp_path: Path) -> None:
    # An injected runner is used as-is (no wrapping, no extra argv).
    runner = FakeRunner(returncode=0, stdout="resolved")

    result = build(tmp_path / "work", runner=runner)

    assert runner.calls == [["uv", "sync", "--all-packages"]]
    assert result.stdout_tail == "resolved"


# ---------------------------------------------------------------------------
# Unit — success / non-zero exit (check=False: exit code is a result, not raise)
# ---------------------------------------------------------------------------


def test_build_success_exit_zero(tmp_path: Path) -> None:
    runner = FakeRunner(returncode=0, stdout="Installed", stderr="")

    result = build(tmp_path / "work", runner=runner)

    assert result == BuildResult(success=True, exit_code=0, stdout_tail="Installed", stderr_tail="")


@pytest.mark.parametrize("exit_code", [1, 2, 130])
def test_build_nonzero_exit_is_a_result_not_raised(tmp_path: Path, exit_code: int) -> None:
    # check=False (default runner contract): a non-zero exit is captured into
    # BuildResult, never raised. exit_code is carried verbatim (1 = generic
    # failure, 2 = usage, 130 = SIGINT/128+2).
    runner = FakeRunner(returncode=exit_code, stdout="partial", stderr=f"err-{exit_code}")

    result = build(tmp_path / "work", runner=runner)

    assert result.success is False
    assert result.exit_code == exit_code  # verbatim, no normalization
    assert result.stderr_tail == f"err-{exit_code}"


# ---------------------------------------------------------------------------
# Unit — bounded tail: last 200 lines, last-N not first-N, stdout AND stderr
# ---------------------------------------------------------------------------


def test_build_stdout_tail_bounded_to_last_200(tmp_path: Path) -> None:
    # 250 lines of stdout must be truncated to exactly the LAST 200.
    stdout = "\n".join(f"L{i}" for i in range(250)) + "\n"
    runner = FakeRunner(returncode=0, stdout=stdout)

    result = build(tmp_path / "work", runner=runner)

    tail_lines = result.stdout_tail.splitlines()
    assert len(tail_lines) == 200  # bounded to exactly 200
    assert tail_lines[0] == "L50"  # last-N, not first-N (dropped L0..L49)
    assert tail_lines[-1] == "L249"


def test_build_stderr_tail_also_bounded(tmp_path: Path) -> None:
    # The bound applies to BOTH streams independently.
    stderr = "\n".join(f"E{i}" for i in range(300)) + "\n"
    runner = FakeRunner(returncode=1, stderr=stderr)

    result = build(tmp_path / "work", runner=runner)

    tail_lines = result.stderr_tail.splitlines()
    assert len(tail_lines) == 200
    assert tail_lines[0] == "E100"  # last 200 of 300
    assert tail_lines[-1] == "E299"


def test_build_empty_output_yields_empty_tail(tmp_path: Path) -> None:
    runner = FakeRunner(returncode=0, stdout="", stderr="")

    result = build(tmp_path / "work", runner=runner)

    assert result.stdout_tail == ""
    assert result.stderr_tail == ""


# ---------------------------------------------------------------------------
# Unit — injected runner raising CalledProcessError -> BuildError, chained cause
# ---------------------------------------------------------------------------


def test_build_called_process_error_wrapped_with_chained_cause(tmp_path: Path) -> None:
    # An injected runner that RAISES CalledProcessError is wrapped to BuildError
    # with the original chained as __cause__ (mirrors worktree.py:193-197).
    err = subprocess.CalledProcessError(
        returncode=1, cmd=["uv", "sync", "--all-packages"], stderr="lock conflict"
    )
    runner = FakeRunner(error=err)

    with pytest.raises(BuildError, match="uv sync --all-packages failed") as exc_info:
        build(tmp_path / "work", runner=runner)

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)
    assert "lock conflict" in str(exc_info.value)


def test_build_called_process_error_safe_when_stderr_none(tmp_path: Path) -> None:
    # CalledProcessError(stderr=None) must not blow up formatting the message.
    err = subprocess.CalledProcessError(returncode=2, cmd=["uv", "sync"], stderr=None)
    runner = FakeRunner(error=err)

    with pytest.raises(BuildError) as exc_info:
        build(tmp_path / "work", runner=runner)

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)


# ---------------------------------------------------------------------------
# Unit — BuildResult is frozen + slots (mirror WorktreeResult discipline)
# ---------------------------------------------------------------------------


def test_build_result_is_frozen(tmp_path: Path) -> None:
    result = build(tmp_path / "work", runner=FakeRunner(returncode=0))

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.exit_code = 99  # type: ignore[misc]


def test_build_result_uses_slots(tmp_path: Path) -> None:
    result = build(tmp_path / "work", runner=FakeRunner(returncode=0))

    # slots=True forbids setting attributes that are not declared fields.
    with pytest.raises(AttributeError):
        result.unexpected = "boom"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Unit — scope guards: no container argv, only the sync subcommand
# ---------------------------------------------------------------------------


def test_no_container_argv_token(tmp_path: Path) -> None:
    # Anti-scope-creep guard vs SFP-65: container isolation must not leak into
    # this slice — no container/docker/podman token is ever issued.
    runner = FakeRunner()
    build(tmp_path / "work", runner=runner)

    flat = " ".join(runner.calls[0]).lower()
    for token in ("container", "docker", "podman"):
        assert token not in flat


def test_only_sync_subcommand_no_test_or_lint(tmp_path: Path) -> None:
    # Anti-scope-creep guard vs SFP-63/64: this slice runs ONLY `uv sync` — never
    # `uv run pytest` (test) or `ruff`/`mypy` (lint).
    runner = FakeRunner()
    build(tmp_path / "work", runner=runner)

    argv = runner.calls[0]
    assert argv[0] == "uv"
    assert argv[1] == "sync"  # the only uv subcommand this slice owns
    assert "pytest" not in argv
    assert "ruff" not in argv
    assert "mypy" not in argv


def test_exec_package_init_is_docstring_only() -> None:
    # exec/__init__.py is a docstring-only OWN module: it must NOT auto-export
    # the operation symbols (callers import the submodule explicitly). Asserting
    # via the AST (not hasattr) is robust — importing the build submodule sets
    # it as a package attribute as a side effect, which would mask an auto-export.
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
# Integration — real uv sync against a temp-copy workdir (.venv lands inside)
# ---------------------------------------------------------------------------


def _workspace_root() -> Path:
    """Walk up from this test file to the directory holding ``uv.lock``.

    That directory is the uv workspace root ``uv sync --all-packages`` operates
    on (root ``pyproject.toml`` declares ``[tool.uv.workspace]`` with no
    ``[build-system]`` → build is resolve+install).
    """
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "uv.lock").is_file() and (parent / "pyproject.toml").is_file():
            return parent
    raise AssertionError("uv workspace root (uv.lock + pyproject.toml) not found")


def _temp_copy_workdir(tmp_path: Path) -> Path:
    """Materialise a temp copy of the repo workspace as the build workdir.

    The copy excludes heavy/non-essential trees (``.git``, the warm ``.venv``,
    caches, the ``.claude`` worktrees). This keeps the copy cheap while leaving a
    fully-resolvable uv workspace (``pyproject.toml`` + ``uv.lock`` + every
    member package's source). Critically, the copy has NO ``.venv`` yet, so a
    ``.venv`` appearing after ``build`` proves the default runner set
    ``cwd=workdir`` (it lands *inside* the workdir, not at the real repo root).

    Operating on a copy also guarantees the committed ``uv.lock`` is never
    mutated by the integration run.
    """
    src = _workspace_root()
    dest = tmp_path / "worktree-copy"
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
def test_integration_build_succeeds_and_creates_venv_inside_workdir(
    tmp_path: Path,
) -> None:
    workdir = _temp_copy_workdir(tmp_path)
    assert not (workdir / ".venv").exists()  # precondition: no venv yet

    # Default runner (runner=None) → the nested subprocess.run closure with
    # cwd=workdir, check=False. Real uv resolves the workspace from the copy.
    result = build(workdir)

    assert result.success is True
    assert result.exit_code == 0
    # The venv landed INSIDE the workdir — the only observable that proves
    # cwd=workdir (a FakeRunner cannot reach this).
    assert (workdir / ".venv").is_dir()
