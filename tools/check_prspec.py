#!/usr/bin/env python3
"""
SFP — PRSpec structural linter CLI (SFP-193 / ID-021).

Thin CLI wrapper over :func:`sfp_contracts.validation.validate_prspec`. The
load-bearing validation logic lives in the typed, CI-mypy-checked
``sfp_contracts`` package (promoted from this file by SFP-236 — the ID-021
intent). This wrapper preserves the original ``--file`` / stdin / ``--sample``
interface byte-for-byte so existing callers (CI, the readiness flow, humans)
keep working.

Validates that a Planner-emitted PRSpec (SFP-14) carries every required
top-level key and that each ``modify`` file entry is *execution-pinned* — i.e.
it carries exactly one anchor (``before`` literal text OR ``line_range``). This
front-loads determinism: a spec that fails here never reaches the Coder. It also
shape-validates the optional ``deferred_fk_obligations`` field (ID-058 deferral
protocol). Unknown/extra top-level keys and duplicate file paths are NOT
rejected (presence + shape only).

Usage:
    python3 tools/check_prspec.py --file <spec.json>   # validate a file
    cat spec.json | python3 tools/check_prspec.py       # validate via stdin
    python3 tools/check_prspec.py --sample              # self-test on bundled fixtures

Exit status: 0 iff zero violations, else 1. Malformed JSON exits 1 with a
one-line error (no traceback).
"""

import argparse
import json
import sys
from pathlib import Path

from sfp_contracts.validation.prspec import validate

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
