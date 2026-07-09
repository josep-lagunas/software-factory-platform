#!/usr/bin/env python3
"""
SFP — Deterministic Build-Order Generator (SFP-195)

Parses `docs/SFP_Ticket_Hierarchy.md` REUSING `create_jira_tickets.parse_hierarchy`
(never reimplemented) and computes a wave-based build order via Kahn longest-path:
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

# Reuse the canonical parser — never reimplement. Importing is safe: the
# module top only reads env vars with defaults and never calls jira_api.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import create_jira_tickets as cjt  # noqa: E402

DEFAULT_DOC = "docs/SFP_Ticket_Hierarchy.md"
DEFAULT_OUT_MD = "docs/BUILD_ORDER.md"
DEFAULT_OUT_JSON = "docs/build_order.json"


# ============================================================
# INDEX + VALIDATION
# ============================================================

def build_index(tickets):
    """Build a {num: ticket} index from the parsed ticket list."""
    return {t["num"]: t for t in tickets}


def check_dangling(tickets, by_num):
    """Return the first (offender_id, missing_dep) pair whose dep num is not in
    the index, or None if all deps resolve. Iterates tickets ascending by num."""
    for t in sorted(tickets, key=lambda x: x["num"]):
        for dep in t["deps"]:
            dep_num = int(dep.split("-")[1])
            if dep_num not in by_num:
                return (t["id"], dep)
    return None


def compute_waves(tickets, by_num):
    """Compute {num: wave} via memoized longest-path with DFS recursion-stack
    cycle detection. Exits non-zero naming cycle members if a cycle is found."""
    memo = {}
    stack = []        # current recursion path (nums)
    on_stack = set()  # O(1) membership for the current path

    def wave_of(num):
        if num in memo:
            return memo[num]
        if num in on_stack:
            # Cycle: the slice from the first occurrence to here is the cycle.
            idx = stack.index(num)
            cycle = stack[idx:]
            chain = " -> ".join(f"SFP-{n}" for n in cycle) + f" -> SFP-{num}"
            sys.exit(f"error: cycle detected: {chain}")
        on_stack.add(num)
        stack.append(num)
        dep_nums = [int(d.split("-")[1]) for d in by_num[num]["deps"]]
        w = 0 if not dep_nums else 1 + max(wave_of(d) for d in dep_nums)
        on_stack.discard(num)
        stack.pop()
        memo[num] = w
        return w

    for t in sorted(tickets, key=lambda x: x["num"]):
        wave_of(t["num"])
    return memo


# ============================================================
# READY SET (--done mode)
# ============================================================

def compute_ready(tickets, done_set):
    """Tickets NOT in done whose deps ⊆ done, lowest-num-first.
    `done_set` is a set of ticket-id strings (e.g. {"SFP-5", "SFP-6"})."""
    ready = []
    for t in tickets:
        if t["id"] in done_set:
            continue
        if set(t["deps"]) <= done_set:
            ready.append(t["id"])
    ready.sort(key=lambda tid: int(tid.split("-")[1]))
    return ready


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
        ("Waves computed via longest-path (Kahn): `wave(t)=0` if no deps, "
         "else `1+max(wave(dep))`. Within each wave, tickets are sorted "
         "ascending by number."),
        "",
        ("`*(B→A)*` markers are informational (platform → manual-core) and "
         "are stripped for dependency resolution."),
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
    p.add_argument("--doc", default=DEFAULT_DOC,
                   help=f"hierarchy markdown (default: {DEFAULT_DOC})")
    p.add_argument("--out-md", default=DEFAULT_OUT_MD,
                   help=f"output markdown path (default: {DEFAULT_OUT_MD})")
    p.add_argument("--out-json", default=DEFAULT_OUT_JSON,
                   help=f"output json path (default: {DEFAULT_OUT_JSON})")
    p.add_argument("--done", default=None,
                   help="comma-separated done ticket ids; prints ready set")
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
        sys.exit(f"error: {offender} depends on {missing} "
                 f"which is not in the hierarchy")

    # 2) Cycle detection + waves.
    waves = compute_waves(tickets, by_num)

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
