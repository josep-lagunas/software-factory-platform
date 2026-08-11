#!/usr/bin/env python3
"""
SFP — Deterministic Build-Order Generator (SFP-195)

Thin CLI wrapper over the build-order DAG logic in
:mod:`sfp_contracts.planning.build_order` (promoted there — promo 2 of 3 of the
``tools/`` -> ``sfp_contracts`` effort, PR #122 being promo 1). This wrapper
owns only: parsing (reusing ``create_jira_tickets.parse_hierarchy`` — never
reimplemented), the ``--done`` / ``--doc`` / ``--out-md`` / ``--out-json``
interface, output emission, and the control-flow glue that turns the library's
typed exceptions into ``sys.exit`` messages.

Parses `docs/SFP_Ticket_Hierarchy.md` and computes a wave-based build order via
Kahn longest-path:
    wave(t) = 0               if t has no deps
    wave(t) = 1 + max(wave(d)) otherwise
Detects dangling deps (dep num not in hierarchy) and cycles (DFS recursion-stack),
exiting non-zero with a named offender in either case. Emits two deterministic
artifacts (no timestamps; byte-identical for the same input):

  - docs/BUILD_ORDER.md   — one `## Wave N` section per wave (asc), each a table.
  - docs/build_order.json — {flat_order, tickets} ordered by (wave asc, num asc).

`--done SFP-5,SFP-6` prints the ready set (tickets not done whose deps ⊆ done),
lowest-num-first, one per line, and emits no docs.

stdlib only. No external dependencies.

Usage:
    python3 tools/build_order.py                          # emit docs
    python3 tools/build_order.py --done SFP-5,SFP-6       # ready set
    python3 tools/build_order.py --doc X --out-md Y --out-json Z
"""

import argparse
import json
import sys
from pathlib import Path

from sfp_contracts.planning.build_order import (
    BuildOrderCycleError,
    build_index,
    check_dangling,
    compute_ready,
    compute_waves,
)

# Reuse the canonical parser — never reimplement. Importing is safe: the
# module top only reads env vars with defaults and never calls jira_api.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import create_jira_tickets as cjt  # noqa: E402

DEFAULT_DOC = "docs/SFP_Ticket_Hierarchy.md"
DEFAULT_OUT_MD = "docs/BUILD_ORDER.md"
DEFAULT_OUT_JSON = "docs/build_order.json"


# ============================================================
# EMIT
# ============================================================


def _group_by_wave(tickets, waves):
    """Return {wave: [tickets]} with each group sorted ascending by num."""
    groups = {}
    for t in tickets:
        groups.setdefault(waves[t["num"]], []).append(t)
    for w in groups:
        groups[w].sort(key=lambda x: x["num"])
    return groups


def emit_md(tickets, waves, doc_path, out_path):
    """Write the BUILD_ORDER.md document. Deterministic (no timestamps)."""
    groups = _group_by_wave(tickets, waves)
    max_wave = max(waves.values()) if waves else -1
    lines = [
        "# SFP — Build Order",
        "",
        f"Source: `{doc_path}`",
        "",
        (
            "Waves computed via longest-path (Kahn): `wave(t)=0` if no deps, "
            "else `1+max(wave(dep))`. Within each wave, tickets are sorted "
            "ascending by number."
        ),
        "",
        (
            "`*(B→A)*` markers are informational (platform → manual-core) and "
            "are stripped for dependency resolution."
        ),
        "",
    ]
    for w in range(max_wave + 1):
        group = groups.get(w, [])
        lines.append(f"## Wave {w}")
        lines.append("")
        lines.append("| Ticket | Title | Area | Executor | Phase | Deps |")
        lines.append("|---|---|---|---|---|---|")
        for t in group:
            deps = ", ".join(t["deps"]) if t["deps"] else "—"
            lines.append(
                f"| {t['id']} | {t['title']} | {t['area']} | "
                f"{t['executor']} | {t['phase']} | {deps} |"
            )
        lines.append("")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(lines) + "\n")


def emit_json(tickets, waves, out_path):
    """Write build_order.json: {flat_order, tickets} ordered by (wave, num).
    Deterministic: indent=2, ensure_ascii=False, trailing newline."""
    ordered = sorted(tickets, key=lambda t: (waves[t["num"]], t["num"]))
    out = {
        "flat_order": [t["id"] for t in ordered],
        "tickets": [
            {
                "ticket": t["id"],
                "wave": waves[t["num"]],
                "deps": list(t["deps"]),
                "area": t["area"],
                "executor": t["executor"],
                "phase": t["phase"],
                "title": t["title"],
            }
            for t in ordered
        ],
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ============================================================
# CLI
# ============================================================


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="build_order.py",
        description="Deterministic build-order generator (SFP-195).",
    )
    p.add_argument(
        "--doc", default=DEFAULT_DOC, help=f"hierarchy markdown (default: {DEFAULT_DOC})"
    )
    p.add_argument(
        "--out-md", default=DEFAULT_OUT_MD, help=f"output markdown path (default: {DEFAULT_OUT_MD})"
    )
    p.add_argument(
        "--out-json",
        default=DEFAULT_OUT_JSON,
        help=f"output json path (default: {DEFAULT_OUT_JSON})",
    )
    p.add_argument("--done", default=None, help="comma-separated done ticket ids; prints ready set")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if not Path(args.doc).exists():
        sys.exit(f"error: hierarchy file not found: {args.doc}")

    _, tickets = cjt.parse_hierarchy(args.doc)
    by_num = build_index(tickets)

    # 1) Dangling check (before cycle).
    dangling = check_dangling(tickets, by_num)
    if dangling:
        offender, missing = dangling
        sys.exit(f"error: {offender} depends on {missing} which is not in the hierarchy")

    # 2) Cycle detection + waves. The library raises BuildOrderCycleError; the
    #    CLI reproduces the byte-identical exit message.
    try:
        waves = compute_waves(tickets, by_num)
    except BuildOrderCycleError as e:
        sys.exit(f"error: cycle detected: {e}")

    # 3) --done mode: print ready set, emit nothing.
    if args.done is not None:
        done_set = {s.strip() for s in args.done.split(",") if s.strip()}
        for tid in compute_ready(tickets, done_set):
            print(tid)
        return 0

    # 4) Default: emit docs.
    emit_md(tickets, waves, args.doc, args.out_md)
    emit_json(tickets, waves, args.out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
