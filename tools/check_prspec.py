#!/usr/bin/env python3
"""
SFP — PRSpec structural linter (SFP-193 / ID-021).

Validates that a Planner-emitted PRSpec (SFP-14) carries every required
top-level key and that each `modify` file entry is *execution-pinned* — i.e.
it carries exactly one anchor (`before` literal text OR `line_range`). This
front-loads determinism: a spec that fails here never reaches the Coder.

stdlib only (argparse, json, sys, pathlib). No external dependencies.

Usage:
    python3 tools/check_prspec.py --file <spec.json>   # validate a file
    cat spec.json | python3 tools/check_prspec.py       # validate via stdin
    python3 tools/check_prspec.py --sample              # self-test on bundled fixtures

Exit status: 0 iff zero violations, else 1. Malformed JSON exits 1 with a
one-line error (no traceback). Unknown/extra top-level keys and duplicate file
paths are NOT rejected (presence + shape only).

TODO: replace this structural check with the canonical `sfp_contracts` JSON
schema once that package lands (see packages/sfp_contracts, ID-021). Until
then this is the authoritative gate.
"""

import argparse
import json
import sys
from pathlib import Path

# ============================================================
# CONTRACT — required top-level keys + controlled vocabularies
# ============================================================

REQUIRED_TOP_LEVEL_KEYS = (
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
)

VALID_ACTIONS = ("create", "modify", "delete")
VALID_VERIFICATION_TYPES = ("script", "command")


# ============================================================
# VALIDATION — collects ALL violations (no short-circuit)
# ============================================================


def validate(spec) -> list:
    """Validate a PRSpec dict. Returns a list of human-readable violation
    strings (empty == valid). Collects every violation; never short-circuits."""
    violations = []

    if not isinstance(spec, dict):
        return [f"spec must be a JSON object (dict); got {type(spec).__name__}"]

    # ---- required top-level keys ----------------------------------------
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in spec:
            violations.append(f"missing required top-level key: '{key}'")

    # ---- files ----------------------------------------------------------
    if "files" in spec:
        violations.extend(_check_files(spec["files"]))

    # ---- verification ---------------------------------------------------
    if "verification" in spec:
        v = spec["verification"]
        if not isinstance(v, dict):
            violations.append(f"'verification' must be a dict (got {type(v).__name__})")
        else:
            vtype = v.get("type")
            if vtype not in VALID_VERIFICATION_TYPES:
                violations.append(
                    f"'verification.type' must be one of {VALID_VERIFICATION_TYPES} (got {vtype!r})"
                )
            body = v.get("body")
            if not isinstance(body, str) or not body.strip():
                violations.append("'verification.body' must be a non-empty string")

    # ---- read_allowlist -------------------------------------------------
    if "read_allowlist" in spec:
        ra = spec["read_allowlist"]
        if not isinstance(ra, list) or len(ra) == 0:
            violations.append("'read_allowlist' must be a non-empty list")

    # ---- rig_reference --------------------------------------------------
    if "rig_reference" in spec:
        rr = spec["rig_reference"]
        if not isinstance(rr, str) or not rr.strip():
            violations.append("'rig_reference' must be a non-empty string")

    # ---- commit_plan ----------------------------------------------------
    if "commit_plan" in spec:
        cp = spec["commit_plan"]
        if not isinstance(cp, dict):
            violations.append("'commit_plan' must be a dict")
        else:
            strategy = cp.get("strategy")
            if not isinstance(strategy, str) or not strategy.strip():
                violations.append("'commit_plan.strategy' must be a non-empty string")
            cm = cp.get("commit_message")
            if not isinstance(cm, str) or not cm.strip():
                violations.append("'commit_plan.commit_message' must be a non-empty string")

    # ---- risks ----------------------------------------------------------
    if "risks" in spec:
        r = spec["risks"]
        if not isinstance(r, list) or len(r) == 0:
            violations.append("'risks' must be a non-empty list")

    # ---- implementation_steps ------------------------------------------
    if "implementation_steps" in spec:
        steps = spec["implementation_steps"]
        if not isinstance(steps, list) or len(steps) == 0:
            violations.append("'implementation_steps' must be a non-empty list")

    # ---- dependencies (dict OR list both OK) ---------------------------
    if "dependencies" in spec:
        d = spec["dependencies"]
        if not isinstance(d, (dict, list)):
            violations.append(f"'dependencies' must be a dict or list (got {type(d).__name__})")

    # ---- acceptance_criteria_mapping (must be dict) --------------------
    if "acceptance_criteria_mapping" in spec:
        acm = spec["acceptance_criteria_mapping"]
        if not isinstance(acm, dict):
            violations.append(
                "'acceptance_criteria_mapping' must be a dict "
                f"(got {type(acm).__name__}; list/scalar rejected)"
            )

    # NOTE: unknown/extra top-level keys are NOT rejected (presence+shape
    # only). Duplicate file paths are NOT rejected either. See PRSpec SFP-193.
    return violations


def _check_files(files) -> list:
    out = []
    if not isinstance(files, list):
        out.append(f"'files' must be a list (got {type(files).__name__})")
        return out
    if len(files) == 0:
        out.append("'files' must be a non-empty list")
        return out
    for i, entry in enumerate(files):
        if not isinstance(entry, dict):
            out.append(f"files[{i}] must be a dict/object (got {type(entry).__name__})")
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            out.append(f"files[{i}] missing or empty 'path' (non-empty string required)")
        action = entry.get("action")
        if action not in VALID_ACTIONS:
            out.append(f"files[{i}] invalid 'action' {action!r} (must be one of {VALID_ACTIONS})")
        # create/delete with an anchor present is OK (ignored, not rejected).
        if action == "modify":
            out.extend(_check_anchor(entry.get("anchor"), i))
    return out


def _check_anchor(anchor, i: int) -> list:
    """Validate a modify entry's anchor: EXACTLY ONE of before/line_range."""
    out = []
    # Missing anchor key (entry.get returns None) OR explicit None.
    if anchor is None:
        out.append(f"files[{i}] action=modify REQUIRES an 'anchor' (missing)")
        return out
    if not isinstance(anchor, dict):
        out.append(f"files[{i}] 'anchor' must be a dict (got {type(anchor).__name__})")
        return out
    has_before = "before" in anchor
    has_range = "line_range" in anchor
    if has_before and has_range:
        out.append(f"files[{i}] 'anchor' must have EXACTLY ONE of before/line_range (both present)")
        return out
    if not has_before and not has_range:
        out.append(
            f"files[{i}] 'anchor' must have EXACTLY ONE of before/line_range (neither present)"
        )
        return out
    if has_before:
        b = anchor["before"]
        if not isinstance(b, str) or not b.strip():
            out.append(f"files[{i}] anchor.before must be a non-empty string")
        return out
    # line_range path
    lr = anchor["line_range"]
    if not isinstance(lr, list) or len(lr) != 2:
        out.append(f"files[{i}] anchor.line_range must be a 2-element list [start, end]")
        return out
    start, end = lr[0], lr[1]
    # Reject bools explicitly: isinstance(True, int) is True in Python, so the
    # int check alone would let True/False through.
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
    ):
        out.append(f"files[{i}] anchor.line_range elements must be ints (bools rejected)")
        return out
    if start < 1:
        out.append(f"files[{i}] anchor.line_range start must be >= 1 (got {start})")
    if end < start:
        out.append(f"files[{i}] anchor.line_range end ({end}) must be >= start ({start})")
    return out


# ============================================================
# CLI
# ============================================================


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="check_prspec.py",
        description="Structurally validate an SFP PRSpec JSON (SFP-14 / ID-021).",
    )
    p.add_argument("--file", help="path to a PRSpec JSON file; if omitted, read stdin")
    p.add_argument(
        "--sample", action="store_true", help="self-test against bundled fixtures (exit 0 if ok)"
    )
    return p.parse_args(argv)


def _run_sample() -> int:
    here = Path(__file__).resolve().parent
    try:
        example = json.loads((here / "prspec_example.json").read_text())
        invalid = json.loads((here / "prspec_invalid.json").read_text())
    except OSError as e:
        print(f"error: sample fixtures missing: {e}", file=sys.stderr)
        return 1
    ex_v = validate(example)
    inv_v = validate(invalid)
    print(
        f"sample: example -> {len(ex_v)} violation(s) (expect 0); "
        f"invalid -> {len(inv_v)} violation(s) (expect >0)"
    )
    return 0 if (ex_v == [] and len(inv_v) > 0) else 1


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.sample:
        return _run_sample()

    if args.file:
        try:
            raw = Path(args.file).read_text()
        except OSError as e:
            print(f"error: cannot read {args.file}: {e}", file=sys.stderr)
            return 1
        source = args.file
    else:
        raw = sys.stdin.read()
        source = "<stdin>"

    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        # Graceful: one-line error, NO traceback.
        print(f"error: malformed JSON in {source}: {e}", file=sys.stderr)
        return 1

    violations = validate(spec)
    if violations:
        print(f"error: {len(violations)} violation(s) in {source}:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    print(f"ok: {source} is a valid PRSpec (0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
