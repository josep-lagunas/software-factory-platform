"""Tests for tools/check_prspec.py — the SFP-193 PRSpec structural linter.

Covers TC-001..TC-017. The validate() function is imported in-process (so its
coverage is measured); the CLI is exercised both in-process (via main()) and
end-to-end via subprocess.
"""

import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# --- import the linter directly from tools/ ---------------------------------
ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import check_prspec  # noqa: E402  (path inserted above)

EXAMPLE = TOOLS / "prspec_example.json"
INVALID = TOOLS / "prspec_invalid.json"
PLANNER_MD = ROOT / ".claude" / "agents" / "sfp-planner.md"
README_MD = ROOT / "README.md"

REQUIRED_KEYS = [
    "pr_spec_id",
    "ticket",
    "title",
    "branch_name",
    "validation_profile_acknowledged",
    "files",
    "implementation_steps",
    "dependencies",
    "risks",
    "commit_plan",
    "pr_title",
    "pr_body_must_include",
    "acceptance_criteria_mapping",
    "verification",
    "read_allowlist",
    "rig_reference",
]


# --- helpers ---------------------------------------------------------------


def _spec():
    """Fresh deep copy of the valid example."""
    return copy.deepcopy(json.loads(EXAMPLE.read_text()))


def _violations_mentioning(violations, needle):
    return [v for v in violations if needle in v]


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
# TC-001 — the bundled example is valid
# ============================================================


def test_tc_001_example_is_valid():
    assert check_prspec.validate(json.loads(EXAMPLE.read_text())) == []


# ============================================================
# TC-002 — removing each required key yields a violation that NAMES the key
# ============================================================


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_tc_002_each_missing_required_key_named(key):
    spec = _spec()
    del spec[key]
    violations = check_prspec.validate(spec)
    assert violations, f"removing {key!r} produced no violation"
    # Anti-gaming: the key NAME must appear in at least one violation.
    assert _violations_mentioning(violations, key), (
        f"no violation mentions the key name {key!r}: {violations}"
    )


# ============================================================
# TC-003 — no short-circuit: multiple missing keys -> >= that many violations
# ============================================================


def test_tc_003_no_short_circuit():
    spec = _spec()
    for key in ("pr_spec_id", "title", "rig_reference"):
        del spec[key]
    violations = check_prspec.validate(spec)
    assert len(violations) >= 3, violations
    # Each removed key is named.
    for key in ("pr_spec_id", "title", "rig_reference"):
        assert _violations_mentioning(violations, key)


# ============================================================
# TC-004 — modify-anchor matrix (exactly-one-of before/line_range)
# ============================================================


def test_tc_004_anchor_no_anchor_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify"}]  # no anchor key
    v = check_prspec.validate(spec)
    assert any("anchor" in m and "modify" in m for m in v), v


def test_tc_004_anchor_before_only_ok():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"before": "literal text"}}]
    assert check_prspec.validate(spec) == []


def test_tc_004_anchor_line_range_only_ok():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [3, 8]}}]
    assert check_prspec.validate(spec) == []


def test_tc_004_anchor_both_rejected():
    spec = _spec()
    spec["files"] = [
        {"path": "x.py", "action": "modify", "anchor": {"before": "x", "line_range": [1, 2]}}
    ]
    v = check_prspec.validate(spec)
    assert any("exactly one" in m.lower() and "both" in m.lower() for m in v), v


def test_tc_004_anchor_neither_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {}}]
    v = check_prspec.validate(spec)
    assert any("exactly one" in m.lower() and "neither" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_start_below_one_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [0, 5]}}]
    v = check_prspec.validate(spec)
    assert any("start" in m.lower() and "1" in m for m in v), v


def test_tc_004_anchor_line_range_end_below_start_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [5, 3]}}]
    v = check_prspec.validate(spec)
    assert any("end" in m.lower() and "start" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_non_int_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [1, "2"]}}]
    v = check_prspec.validate(spec)
    assert any("int" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_three_elements_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [1, 2, 3]}}]
    v = check_prspec.validate(spec)
    assert any("2-element" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_one_element_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [1]}}]
    v = check_prspec.validate(spec)
    assert any("2-element" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_bools_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [True, False]}}]
    v = check_prspec.validate(spec)
    assert any("int" in m.lower() and "bool" in m.lower() for m in v), v


def test_tc_004_anchor_before_empty_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"before": "   "}}]
    v = check_prspec.validate(spec)
    assert any("before" in m.lower() and "non-empty" in m.lower() for m in v), v


def test_tc_004_anchor_not_a_dict_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": "literal"}]
    v = check_prspec.validate(spec)
    assert any("anchor" in m.lower() and "dict" in m.lower() for m in v), v


def test_tc_004_create_with_anchor_ok():
    # create/delete with an anchor present is tolerated (ignored, not rejected).
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "create", "anchor": {"before": "ignored"}}]
    assert check_prspec.validate(spec) == []


# ============================================================
# TC-005 — verification shape
# ============================================================


def test_tc_005_verification_missing_body_rejected():
    spec = _spec()
    spec["verification"] = {"type": "script"}
    v = check_prspec.validate(spec)
    assert any("verification.body" in m for m in v), v


def test_tc_005_verification_empty_body_rejected():
    spec = _spec()
    spec["verification"] = {"type": "script", "body": ""}
    v = check_prspec.validate(spec)
    assert any("verification.body" in m for m in v), v


def test_tc_005_verification_bad_type_rejected():
    spec = _spec()
    spec["verification"] = {"type": "magic", "body": "do thing"}
    v = check_prspec.validate(spec)
    assert any("verification.type" in m for m in v), v


def test_tc_005_verification_missing_type_rejected():
    spec = _spec()
    spec["verification"] = {"body": "do thing"}
    v = check_prspec.validate(spec)
    assert any("verification.type" in m for m in v), v


def test_tc_005_verification_command_ok():
    spec = _spec()
    spec["verification"] = {"type": "command", "body": "make check"}
    assert check_prspec.validate(spec) == []


# ============================================================
# TC-006 — read_allowlist presence / non-empty
# ============================================================


def test_tc_006_read_allowlist_empty_rejected():
    spec = _spec()
    spec["read_allowlist"] = []
    v = check_prspec.validate(spec)
    assert any("read_allowlist" in m for m in v), v


def test_tc_006_read_allowlist_not_list_rejected():
    spec = _spec()
    spec["read_allowlist"] = "src/"
    v = check_prspec.validate(spec)
    assert any("read_allowlist" in m for m in v), v


# ============================================================
# TC-007 — rig_reference presence / non-empty
# ============================================================


def test_tc_007_rig_reference_empty_rejected():
    spec = _spec()
    spec["rig_reference"] = ""
    v = check_prspec.validate(spec)
    assert any("rig_reference" in m for m in v), v


def test_tc_007_rig_reference_not_str_rejected():
    spec = _spec()
    spec["rig_reference"] = None
    v = check_prspec.validate(spec)
    assert any("rig_reference" in m for m in v), v


# ============================================================
# TC-008 — files[] shape
# ============================================================


def test_tc_008_files_not_a_list_rejected():
    spec = _spec()
    spec["files"] = {"path": "x.py"}
    v = check_prspec.validate(spec)
    assert any("files" in m and "list" in m for m in v), v


def test_tc_008_files_entry_not_dict_rejected():
    spec = _spec()
    spec["files"] = ["x.py"]
    v = check_prspec.validate(spec)
    assert any("files[0]" in m and "dict" in m for m in v), v


def test_tc_008_files_entry_missing_path_rejected():
    spec = _spec()
    spec["files"] = [{"action": "create"}]
    v = check_prspec.validate(spec)
    assert any("files[0]" in m and "path" in m for m in v), v


def test_tc_008_files_entry_bad_action_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "rename"}]
    v = check_prspec.validate(spec)
    assert any("files[0]" in m and "action" in m for m in v), v


def test_tc_008_spec_not_a_dict_rejected():
    v = check_prspec.validate(["not", "a", "dict"])
    assert v and "object" in v[0].lower()


# ============================================================
# TC-009 — commit_plan / risks / steps / dependencies / acm types
# ============================================================


def test_tc_009_commit_plan_missing_strategy_rejected():
    spec = _spec()
    spec["commit_plan"] = {"commit_message": "x"}
    v = check_prspec.validate(spec)
    assert any("commit_plan.strategy" in m for m in v), v


def test_tc_009_commit_plan_missing_message_rejected():
    spec = _spec()
    spec["commit_plan"] = {"strategy": "single"}
    v = check_prspec.validate(spec)
    assert any("commit_plan.commit_message" in m for m in v), v


def test_tc_009_commit_plan_not_dict_rejected():
    spec = _spec()
    spec["commit_plan"] = "single commit"
    v = check_prspec.validate(spec)
    assert any("commit_plan" in m and "dict" in m for m in v), v


def test_tc_009_risks_empty_rejected():
    spec = _spec()
    spec["risks"] = []
    v = check_prspec.validate(spec)
    assert any("risks" in m for m in v), v


def test_tc_009_steps_empty_rejected():
    spec = _spec()
    spec["implementation_steps"] = []
    v = check_prspec.validate(spec)
    assert any("implementation_steps" in m for m in v), v


def test_tc_009_dependencies_none_rejected():
    spec = _spec()
    spec["dependencies"] = None
    v = check_prspec.validate(spec)
    assert any("dependencies" in m for m in v), v


def test_tc_009_dependencies_scalar_rejected():
    spec = _spec()
    spec["dependencies"] = 5
    v = check_prspec.validate(spec)
    assert any("dependencies" in m for m in v), v


def test_tc_009_dependencies_list_ok():
    spec = _spec()
    spec["dependencies"] = ["ID-021"]
    assert check_prspec.validate(spec) == []


def test_tc_009_acm_list_rejected():
    spec = _spec()
    spec["acceptance_criteria_mapping"] = ["AC-1"]
    v = check_prspec.validate(spec)
    assert any("acceptance_criteria_mapping" in m for m in v), v


# ============================================================
# TC-010 — CLI --file exit 0 on example
# ============================================================


def test_tc_010_cli_file_exit_0_on_example():
    r = _run_cli(["--file", str(EXAMPLE)])
    assert r.returncode == 0, r.stderr
    assert "valid" in r.stdout.lower()


# ============================================================
# TC-011 — CLI --file exit 1 + stderr content on invalid
# ============================================================


def test_tc_011_cli_file_exit_1_on_invalid():
    r = _run_cli(["--file", str(INVALID)])
    assert r.returncode == 1
    assert "violation" in r.stderr.lower()
    # At least one missing-key name shows up on stderr.
    assert "pr_spec_id" in r.stderr or "title" in r.stderr


# ============================================================
# TC-012 — stdin path (no --file)
# ============================================================


def test_tc_012_cli_stdin_path():
    data = json.dumps(_spec())
    r = _run_cli([], stdin_data=data)
    assert r.returncode == 0, r.stderr
    assert "valid" in r.stdout.lower()


def test_tc_012_cli_stdin_invalid_exits_1():
    r = _run_cli([], stdin_data='{"ticket": "SFP-X"}')
    assert r.returncode == 1
    assert "violation" in r.stderr.lower()


# In-process stdin coverage (so the no-file branch is measured by coverage).
def test_tc_012_main_reads_stdin(monkeypatch):
    spec = _spec()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(spec)))
    assert check_prspec.main([]) == 0


# ============================================================
# TC-013 — --sample exits 0 (in-process + CLI)
# ============================================================


def test_tc_013_sample_in_process(capsys):
    assert check_prspec.main(["--sample"]) == 0
    out = capsys.readouterr().out
    assert "sample" in out.lower()


def test_tc_013_sample_cli():
    r = _run_cli(["--sample"])
    assert r.returncode == 0, r.stderr
    assert "sample" in r.stdout.lower()


# ============================================================
# TC-014 — malformed JSON -> non-zero, NO traceback
# ============================================================


def test_tc_014_malformed_json_in_process(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json,,, }")
    rc = check_prspec.main(["--file", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "malformed json" in err.lower()


def test_tc_014_malformed_json_cli(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ broken")
    r = _run_cli(["--file", str(bad)])
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert "malformed json" in r.stderr.lower()


def test_tc_014_missing_file_in_process(tmp_path, capsys):
    rc = check_prspec.main(["--file", str(tmp_path / "nope.json")])
    assert rc == 1
    assert "Traceback" not in capsys.readouterr().err


# ============================================================
# TC-015 — planner.md references the linter
# ============================================================


def test_tc_015_planner_md_references_linter():
    text = PLANNER_MD.read_text()
    assert "check_prspec.py" in text or "validate(" in text or "prspec_example" in text, (
        "planner.md does not reference the linter"
    )


# ============================================================
# TC-016 — README mentions the linter and --file
# ============================================================


def test_tc_016_readme_mentions_linter():
    text = README_MD.read_text()
    assert "tools/check_prspec.py" in text
    assert "--file" in text


# ============================================================
# TC-017 — coverage gate >= 90% (scoped to tools/check_prspec.py)
# ============================================================
#
# This test re-runs the suite under coverage in a subprocess. To avoid
# infinite recursion (the subprocess would re-import this test, including
# TC-017 itself), we pass SFP_COVERAGE_CHILD=1 into the child and SKIP this
# test when that var is set.


@pytest.mark.skipif(
    os.environ.get("SFP_COVERAGE_CHILD") == "1", reason="inner coverage child run — skip the gate"
)
def test_tc_017_coverage_threshold(tmp_path):
    # Isolated COVERAGE_FILE so we don't clobber the outer run's data.
    cov_file = tmp_path / ".coverage"
    env_cov = {
        "COVERAGE_FILE": str(cov_file),
        "SFP_COVERAGE_CHILD": "1",
        # Ignore the workspace [tool.coverage] config: its `source` list
        # excludes tools/ (see SFP-27), which would yield "No data to report".
        # This test specifies everything via CLI (--include, --fail-under).
        "COVERAGE_RCFILE": "/dev/null",
    }
    # Run the suite under coverage, scoped to the linter.
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
