"""Tests for :mod:`workspace_worker.repo._validation` — SFP-239 primitives.

Unit tests use a fake runner to assert the exact probe argv and the False
branch; integration tests exercise the real ``git`` binary to pin the actual
hollow-``.git`` behaviour (the state the dogfood run hit).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from workspace_worker.repo._validation import is_valid_git_repo, remove_path

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git binary required for integration tests"
)


class ProbeRunner:
    """Records invocations; exits non-zero for paths listed in ``invalid``."""

    def __init__(self, invalid: frozenset[str] = frozenset()) -> None:
        self.calls: list[list[str]] = []
        self._invalid = invalid

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        target = Path(cmd[2])
        if str(target) in self._invalid:
            raise subprocess.CalledProcessError(
                returncode=128, cmd=cmd, stderr="fatal: not a git repository"
            )
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=".git", stderr="")


# ---------------------------------------------------------------------------
# is_valid_git_repo — unit
# ---------------------------------------------------------------------------


def test_probe_runs_rev_parse_git_dir(tmp_path: Path) -> None:
    runner = ProbeRunner()

    assert is_valid_git_repo(tmp_path, runner) is True

    assert runner.calls == [["git", "-C", str(tmp_path), "rev-parse", "--git-dir"]]


def test_probe_returns_false_when_git_rejects_path(tmp_path: Path) -> None:
    runner = ProbeRunner(invalid=frozenset({str(tmp_path)}))

    assert is_valid_git_repo(tmp_path, runner) is False


# ---------------------------------------------------------------------------
# is_valid_git_repo — integration (real git)
# ---------------------------------------------------------------------------


@requires_git
def test_probe_accepts_real_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)

    assert is_valid_git_repo(repo, _real_runner) is True


@requires_git
def test_probe_rejects_hollow_git_dir(tmp_path: Path) -> None:
    # The exact dogfood state: directory present, .git present but EMPTY.
    hollow = tmp_path / "hollow"
    (hollow / ".git").mkdir(parents=True)

    assert is_valid_git_repo(hollow, _real_runner) is False


@requires_git
def test_probe_rejects_plain_directory(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert is_valid_git_repo(plain, _real_runner) is False


def _real_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # Mirror the package's default runner contract (check=True): git's
    # "not a git repository" exit 128 must surface as CalledProcessError.
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# remove_path
# ---------------------------------------------------------------------------


def test_remove_path_deletes_directory_tree(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "nested").mkdir(parents=True)
    (tree / "nested" / "file").write_text("x")

    remove_path(tree)

    assert tree.exists() is False


def test_remove_path_deletes_plain_file(tmp_path: Path) -> None:
    # rmtree alone would raise here; a stray file in the cache slot must go too.
    stray = tmp_path / "stray"
    stray.write_text("not a dir")

    remove_path(stray)

    assert stray.exists() is False


def test_remove_path_unlinks_symlink_without_following(tmp_path: Path) -> None:
    # A symlinked slot must NOT recurse into the target — unlink the link only.
    target = tmp_path / "target"
    target.mkdir()
    (target / "precious").write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(target)

    remove_path(link)

    assert link.exists() is False
    assert (target / "precious").read_text() == "keep"


def test_remove_path_missing_is_noop(tmp_path: Path) -> None:
    remove_path(tmp_path / "never-existed")  # must not raise
