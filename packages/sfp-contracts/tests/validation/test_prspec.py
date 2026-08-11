"""Tests for :mod:`sfp_contracts.validation.prspec` — the PRSpec collect-all
structural linter (ID-021 / SFP-14 / SFP-193), promoted from
``tools/check_prspec.py`` by SFP-236.

Covers TC-001..TC-009 and TC-018 (the deferred-FK shape). The function under
test is :func:`sfp_contracts.validation.validate_prspec`; every case asserts the
collect-all, no-short-circuit behaviour that pydantic ``model_validate`` does
NOT provide and that must be preserved. The thin CLI wrapper is smoke-tested
separately at ``tests/test_check_prspec.py``.

The valid/invalid fixtures live next to the original CLI at
``tools/prspec_example.json`` / ``tools/prspec_invalid.json``; they are the
canonical linter fixtures and are referenced by both the package tests (here)
and the CLI smoke tests.
"""

import copy
import json
from pathlib import Path

import pytest
from sfp_contracts.validation.prspec import validate_prspec

# The linter fixtures ship next to the CLI tool (tools/). The package tests
# resolve them relative to the repo root.
ROOT = Path(__file__).resolve().parents[4]
TOOLS = ROOT / "tools"
EXAMPLE = TOOLS / "prspec_example.json"
INVALID = TOOLS / "prspec_invalid.json"

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

DEFERRED_KEYS = ["column", "target_aggregate", "blocked_on", "follow_up"]


# --- helpers ---------------------------------------------------------------


def _spec():
    """Fresh deep copy of the valid example."""
    return copy.deepcopy(json.loads(EXAMPLE.read_text()))


def _violations_mentioning(violations, needle):
    return [v for v in violations if needle in v]


def _valid_deferral():
    return {
        "column": "project_id",
        "target_aggregate": "business.projects(project_id)",
        "blocked_on": "SFP-100",
        "follow_up": "Add FK business.tickets.project_id -> business.projects(project_id).",
    }


# ============================================================
# TC-001 — the bundled example is valid
# ============================================================


def test_tc_001_example_is_valid():
    assert validate_prspec(json.loads(EXAMPLE.read_text())) == []


# ============================================================
# TC-002 — removing each required key yields a violation that NAMES the key
# ============================================================


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_tc_002_each_missing_required_key_named(key):
    spec = _spec()
    del spec[key]
    violations = validate_prspec(spec)
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
    violations = validate_prspec(spec)
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
    v = validate_prspec(spec)
    assert any("anchor" in m and "modify" in m for m in v), v


def test_tc_004_anchor_before_only_ok():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"before": "literal text"}}]
    assert validate_prspec(spec) == []


def test_tc_004_anchor_line_range_only_ok():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [3, 8]}}]
    assert validate_prspec(spec) == []


def test_tc_004_anchor_both_rejected():
    spec = _spec()
    spec["files"] = [
        {"path": "x.py", "action": "modify", "anchor": {"before": "x", "line_range": [1, 2]}}
    ]
    v = validate_prspec(spec)
    assert any("exactly one" in m.lower() and "both" in m.lower() for m in v), v


def test_tc_004_anchor_neither_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {}}]
    v = validate_prspec(spec)
    assert any("exactly one" in m.lower() and "neither" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_start_below_one_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [0, 5]}}]
    v = validate_prspec(spec)
    assert any("start" in m.lower() and "1" in m for m in v), v


def test_tc_004_anchor_line_range_end_below_start_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [5, 3]}}]
    v = validate_prspec(spec)
    assert any("end" in m.lower() and "start" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_non_int_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [1, "2"]}}]
    v = validate_prspec(spec)
    assert any("int" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_three_elements_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [1, 2, 3]}}]
    v = validate_prspec(spec)
    assert any("2-element" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_one_element_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [1]}}]
    v = validate_prspec(spec)
    assert any("2-element" in m.lower() for m in v), v


def test_tc_004_anchor_line_range_bools_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"line_range": [True, False]}}]
    v = validate_prspec(spec)
    assert any("int" in m.lower() and "bool" in m.lower() for m in v), v


def test_tc_004_anchor_before_empty_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": {"before": "   "}}]
    v = validate_prspec(spec)
    assert any("before" in m.lower() and "non-empty" in m.lower() for m in v), v


def test_tc_004_anchor_not_a_dict_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "modify", "anchor": "literal"}]
    v = validate_prspec(spec)
    assert any("anchor" in m.lower() and "dict" in m.lower() for m in v), v


def test_tc_004_create_with_anchor_ok():
    # create/delete with an anchor present is tolerated (ignored, not rejected).
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "create", "anchor": {"before": "ignored"}}]
    assert validate_prspec(spec) == []


# ============================================================
# TC-005 — verification shape
# ============================================================


def test_tc_005_verification_missing_body_rejected():
    spec = _spec()
    spec["verification"] = {"type": "script"}
    v = validate_prspec(spec)
    assert any("verification.body" in m for m in v), v


def test_tc_005_verification_empty_body_rejected():
    spec = _spec()
    spec["verification"] = {"type": "script", "body": ""}
    v = validate_prspec(spec)
    assert any("verification.body" in m for m in v), v


def test_tc_005_verification_bad_type_rejected():
    spec = _spec()
    spec["verification"] = {"type": "magic", "body": "do thing"}
    v = validate_prspec(spec)
    assert any("verification.type" in m for m in v), v


def test_tc_005_verification_missing_type_rejected():
    spec = _spec()
    spec["verification"] = {"body": "do thing"}
    v = validate_prspec(spec)
    assert any("verification.type" in m for m in v), v


def test_tc_005_verification_command_ok():
    spec = _spec()
    spec["verification"] = {"type": "command", "body": "make check"}
    assert validate_prspec(spec) == []


# ============================================================
# TC-006 — read_allowlist presence / non-empty
# ============================================================


def test_tc_006_read_allowlist_empty_rejected():
    spec = _spec()
    spec["read_allowlist"] = []
    v = validate_prspec(spec)
    assert any("read_allowlist" in m for m in v), v


def test_tc_006_read_allowlist_not_list_rejected():
    spec = _spec()
    spec["read_allowlist"] = "src/"
    v = validate_prspec(spec)
    assert any("read_allowlist" in m for m in v), v


# ============================================================
# TC-007 — rig_reference presence / non-empty
# ============================================================


def test_tc_007_rig_reference_empty_rejected():
    spec = _spec()
    spec["rig_reference"] = ""
    v = validate_prspec(spec)
    assert any("rig_reference" in m for m in v), v


def test_tc_007_rig_reference_not_str_rejected():
    spec = _spec()
    spec["rig_reference"] = None
    v = validate_prspec(spec)
    assert any("rig_reference" in m for m in v), v


# ============================================================
# TC-008 — files[] shape
# ============================================================


def test_tc_008_files_not_a_list_rejected():
    spec = _spec()
    spec["files"] = {"path": "x.py"}
    v = validate_prspec(spec)
    assert any("files" in m and "list" in m for m in v), v


def test_tc_008_files_entry_not_dict_rejected():
    spec = _spec()
    spec["files"] = ["x.py"]
    v = validate_prspec(spec)
    assert any("files[0]" in m and "dict" in m for m in v), v


def test_tc_008_files_entry_missing_path_rejected():
    spec = _spec()
    spec["files"] = [{"action": "create"}]
    v = validate_prspec(spec)
    assert any("files[0]" in m and "path" in m for m in v), v


def test_tc_008_files_entry_bad_action_rejected():
    spec = _spec()
    spec["files"] = [{"path": "x.py", "action": "rename"}]
    v = validate_prspec(spec)
    assert any("files[0]" in m and "action" in m for m in v), v


def test_tc_008_spec_not_a_dict_rejected():
    v = validate_prspec(["not", "a", "dict"])
    assert v and "object" in v[0].lower()


# ============================================================
# TC-009 — commit_plan / risks / steps / dependencies / acm types
# ============================================================


def test_tc_009_commit_plan_missing_strategy_rejected():
    spec = _spec()
    spec["commit_plan"] = {"commit_message": "x"}
    v = validate_prspec(spec)
    assert any("commit_plan.strategy" in m for m in v), v


def test_tc_009_commit_plan_missing_message_rejected():
    spec = _spec()
    spec["commit_plan"] = {"strategy": "single"}
    v = validate_prspec(spec)
    assert any("commit_plan.commit_message" in m for m in v), v


def test_tc_009_commit_plan_not_dict_rejected():
    spec = _spec()
    spec["commit_plan"] = "single commit"
    v = validate_prspec(spec)
    assert any("commit_plan" in m and "dict" in m for m in v), v


def test_tc_009_risks_empty_rejected():
    spec = _spec()
    spec["risks"] = []
    v = validate_prspec(spec)
    assert any("risks" in m for m in v), v


def test_tc_009_steps_empty_rejected():
    spec = _spec()
    spec["implementation_steps"] = []
    v = validate_prspec(spec)
    assert any("implementation_steps" in m for m in v), v


def test_tc_009_dependencies_none_rejected():
    spec = _spec()
    spec["dependencies"] = None
    v = validate_prspec(spec)
    assert any("dependencies" in m for m in v), v


def test_tc_009_dependencies_scalar_rejected():
    spec = _spec()
    spec["dependencies"] = 5
    v = validate_prspec(spec)
    assert any("dependencies" in m for m in v), v


def test_tc_009_dependencies_list_ok():
    spec = _spec()
    spec["dependencies"] = ["ID-021"]
    assert validate_prspec(spec) == []


def test_tc_009_acm_list_rejected():
    spec = _spec()
    spec["acceptance_criteria_mapping"] = ["AC-1"]
    v = validate_prspec(spec)
    assert any("acceptance_criteria_mapping" in m for m in v), v


# ============================================================
# TC-018 — deferred_fk_obligations shape (ID-058 deferral protocol)
# ============================================================
#
# Optional field (absent is fine). When present, each entry must carry non-empty
# column/target_aggregate/blocked_on/follow_up strings. Collects violations; no
# short-circuit. Shape only — does NOT inspect migrations (SFP-235's concern).


def test_tc_018_absence_ok():
    # Absent is fine — the field is optional (no deferrals declared).
    spec = _spec()
    assert "deferred_fk_obligations" not in spec
    assert validate_prspec(spec) == []


def test_tc_018_empty_list_ok():
    # An explicit empty list is fine (no deferrals declared).
    spec = _spec()
    spec["deferred_fk_obligations"] = []
    assert validate_prspec(spec) == []


def test_tc_018_valid_entry_passes():
    spec = _spec()
    spec["deferred_fk_obligations"] = [_valid_deferral()]
    assert validate_prspec(spec) == []


def test_tc_018_not_a_list_rejected():
    spec = _spec()
    spec["deferred_fk_obligations"] = {"column": "project_id"}
    v = validate_prspec(spec)
    assert any("deferred_fk_obligations" in m and "list" in m for m in v), v


def test_tc_018_entry_not_a_dict_rejected():
    spec = _spec()
    spec["deferred_fk_obligations"] = ["project_id"]
    v = validate_prspec(spec)
    assert any("deferred_fk_obligations[0]" in m and "dict" in m for m in v), v


@pytest.mark.parametrize("missing_key", DEFERRED_KEYS)
def test_tc_018_entry_missing_each_key_named(missing_key):
    spec = _spec()
    entry = _valid_deferral()
    del entry[missing_key]
    spec["deferred_fk_obligations"] = [entry]
    v = validate_prspec(spec)
    # The missing key NAME must appear in at least one violation.
    assert _violations_mentioning(v, missing_key), (
        f"no violation names the missing key {missing_key!r}: {v}"
    )


@pytest.mark.parametrize("blank_key", DEFERRED_KEYS)
def test_tc_018_entry_blank_each_key_named(blank_key):
    spec = _spec()
    entry = _valid_deferral()
    entry[blank_key] = "   "
    spec["deferred_fk_obligations"] = [entry]
    v = validate_prspec(spec)
    assert _violations_mentioning(v, blank_key), (
        f"no violation names the blanked key {blank_key!r}: {v}"
    )


def test_tc_018_no_short_circuit():
    # One entry missing ALL required keys -> >= 4 violations (one per key).
    spec = _spec()
    spec["deferred_fk_obligations"] = [{}]
    v = validate_prspec(spec)
    assert len(v) >= 4, v
    for key in DEFERRED_KEYS:
        assert _violations_mentioning(v, key), f"key {key!r} not named: {v}"


def test_tc_018_multiple_entries_indexed():
    # Violations are indexed by entry position (deferred_fk_obligations[i]).
    spec = _spec()
    bad = {"column": "project_id"}  # missing 3 keys
    spec["deferred_fk_obligations"] = [bad, bad]
    v = validate_prspec(spec)
    assert any("deferred_fk_obligations[0]" in m for m in v), v
    assert any("deferred_fk_obligations[1]" in m for m in v), v
