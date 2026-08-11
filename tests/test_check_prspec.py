"""Smoke tests for the ``tools/check_prspec.py`` thin CLI wrapper (ID-021).

The load-bearing validation logic was promoted into the typed
:mod:`sfp_contracts.validation.prspec` module by SFP-236; its 70 collect-all
behavioural cases live at
``packages/sfp-contracts/tests/validation/test_prspec.py``. This file keeps a
thin CLI smoke test covering the wrapper's ``--file`` / stdin / ``--sample``
interface and its end-to-end behaviour (exit codes, stdout/stderr shape, no
traceback on malformed JSON), plus the docs-presence assertions. It exercises
the wrapper through the same subprocess path CI uses.
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import check_prspec  # noqa: E402  (path inserted above)

EXAMPLE = TOOLS / "prspec_example.json"
INVALID = TOOLS / "prspec_invalid.json"
PLANNER_MD = ROOT / ".claude" / "agents" / "sfp-planner.md"
README_MD = ROOT / "README.md"


def _spec():
    """Fresh deep copy of the valid example."""
    return json.loads(EXAMPLE.read_text())


def _run_cli(args, stdin_data=None):
    """Run the real CLI in a subprocess (end-to-end)."""
    return subprocess.run(
        [sys.executable, str(TOOLS / "check_prspec.py"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        input=stdin_data,
    )


# ============================================================
# --file path
# ============================================================


def test_cli_file_exit_0_on_example():
    r = _run_cli(["--file", str(EXAMPLE)])
    assert r.returncode == 0, r.stderr
    assert "valid" in r.stdout.lower()


def test_cli_file_exit_1_on_invalid():
    r = _run_cli(["--file", str(INVALID)])
    assert r.returncode == 1
    assert "violation" in r.stderr.lower()
    # At least one missing-key name shows up on stderr.
    assert "pr_spec_id" in r.stderr or "title" in r.stderr


def test_main_prints_violations_branch_in_process(capsys):
    """In-process coverage for main()'s violations-print branch (lines that
    subprocess CLI runs don't attribute to the coverage parent process)."""
    rc = check_prspec.main(["--file", str(INVALID)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "violation" in err.lower()


# ============================================================
# stdin path (no --file)
# ============================================================


def test_cli_stdin_path():
    data = json.dumps(_spec())
    r = _run_cli([], stdin_data=data)
    assert r.returncode == 0, r.stderr
    assert "valid" in r.stdout.lower()


def test_cli_stdin_invalid_exits_1():
    r = _run_cli([], stdin_data='{"ticket": "SFP-X"}')
    assert r.returncode == 1
    assert "violation" in r.stderr.lower()


# In-process stdin coverage (so the no-file branch is measured by coverage).
def test_main_reads_stdin(monkeypatch):
    spec = _spec()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(spec)))
    assert check_prspec.main([]) == 0


# ============================================================
# --sample (in-process + CLI)
# ============================================================


def test_sample_in_process(capsys):
    assert check_prspec.main(["--sample"]) == 0
    out = capsys.readouterr().out
    assert "sample" in out.lower()


def test_sample_cli():
    r = _run_cli(["--sample"])
    assert r.returncode == 0, r.stderr
    assert "sample" in r.stdout.lower()


# ============================================================
# malformed JSON -> non-zero, NO traceback
# ============================================================


def test_malformed_json_in_process(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json,,, }")
    rc = check_prspec.main(["--file", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "malformed json" in err.lower()


def test_malformed_json_cli(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ broken")
    r = _run_cli(["--file", str(bad)])
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert "malformed json" in r.stderr.lower()


def test_missing_file_in_process(tmp_path, capsys):
    rc = check_prspec.main(["--file", str(tmp_path / "nope.json")])
    assert rc == 1
    assert "Traceback" not in capsys.readouterr().err


# ============================================================
# docs reference the linter
# ============================================================


def test_planner_md_references_linter():
    text = PLANNER_MD.read_text()
    assert "check_prspec.py" in text or "validate(" in text or "prspec_example" in text, (
        "planner.md does not reference the linter"
    )


def test_readme_mentions_linter():
    text = README_MD.read_text()
    assert "tools/check_prspec.py" in text
    assert "--file" in text


# ============================================================
# coverage gate >= 90% (scoped to tools/check_prspec.py)
# ============================================================
#
# This test re-runs the suite under coverage in a subprocess. To avoid
# infinite recursion (the subprocess would re-import this test, including this
# gate itself), we pass SFP_COVERAGE_CHILD=1 into the child and SKIP this test
# when that var is set.


@pytest.mark.skipif(
    os.environ.get("SFP_COVERAGE_CHILD") == "1", reason="inner coverage child run — skip the gate"
)
def test_coverage_threshold(tmp_path):
    # NOTE: the thin wrapper now delegates to sfp_contracts.validation.prspec,
    # so coverage of the wrapper itself stays high as long as the CLI surface
    # is exercised. The substantive coverage lives against the package module.
    cov_file = tmp_path / ".coverage"
    env_cov = {
        "COVERAGE_FILE": str(cov_file),
        "SFP_COVERAGE_CHILD": "1",
        # Ignore the workspace [tool.coverage] config: its `source` list
        # excludes tools/ (see SFP-27), which would yield "No data to report".
        # This test specifies everything via CLI (--include, --fail-under).
        "COVERAGE_RCFILE": "/dev/null",
    }
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--include=tools/check_prspec.py",
            "-m",
            "pytest",
            "tests/test_check_prspec.py",
            "-q",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**env_cov},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "report",
            "--include=tools/check_prspec.py",
            "--fail-under=90",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**env_cov},
    )
    assert r2.returncode == 0, f"check_prspec.py coverage below 90%:\n{r2.stdout}"
