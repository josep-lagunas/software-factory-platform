#!/usr/bin/env python3
"""
SFP — Reconcile Jira Blocks dependency links to the doc source of truth.

For every `Deps:` edge in docs/SFP_Ticket_Hierarchy.md (ticket T depends on D),
ensure the Jira Blocks link means "D blocks T": inwardIssue = D (the BLOCKER),
outwardIssue = T (the BLOCKED). Links already correct are left untouched;
reversed links are deleted and recreated correctly; missing links are created.

Idempotent: re-running reports zero changes once Jira matches the doc.

Driven by the doc (source of truth) + .jira_creation_state.json (doc-number ->
Jira-key map). Reuses tools/create_jira_tickets.py helpers (parse_hierarchy,
jira_api, create_issue_link). Does NOT touch non-Blocks links or tickets absent
from the state map. See SFP-196 for the link-direction semantics.

Usage:
    python3 tools/reconcile_dep_links.py --dry-run     # plan only, no writes
    python3 tools/reconcile_dep_links.py               # apply

Env (required): JIRA_SITE, JIRA_EMAIL, JIRA_API_TOKEN (via source-env.sh / .env).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# import the creator's helpers directly from tools/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import create_jira_tickets as cjt  # noqa: E402

DOC = ROOT / "docs" / "SFP_Ticket_Hierarchy.md"
STATE = ROOT / ".jira_creation_state.json"


def classify_link(inward, outward, blocker, blocked):
    """Decide the action for one Blocks link between `blocker` and `blocked`.

    `inward`/`outward` are the CURRENT issue keys on the link record (both
    required; a Blocks link always has both). Correct state is
    inward=blocker (BLOCKER), outward=blocked (BLOCKED). Returns one of:
    "ok" | "recreate".
    """
    if inward == blocker and outward == blocked:
        return "ok"
    if inward == blocked and outward == blocker:
        return "recreate"
    # The record involves both issues but in an unexpected shape — should not
    # happen for a two-issue Blocks link. Treat as recreate to be safe.
    return "recreate"


def expected_edges(tickets, keymap):
    """Yield (blocker_key, blocked_key, doc_edge) for every Deps edge whose
    both endpoints are in the keymap. `doc_edge` is "docD blocks docT"."""
    for t in tickets:
        blocked_doc = t["id"]
        blocked_key = keymap.get(blocked_doc)
        if not blocked_key:
            continue
        for dep_doc in t.get("deps", []):
            blocker_key = keymap.get(dep_doc)
            if not blocker_key:
                continue
            yield blocker_key, blocked_key, f"{dep_doc} blocks {blocked_doc}"


_LINKS_CACHE: dict[str, list[dict]] = {}


def blocks_links_of(issue_key):
    """Full Blocks link records involving `issue_key`: list of {id, inward, outward}.

    Memoized — each issue is fetched once even when several edges touch it.
    """
    if issue_key in _LINKS_CACHE:
        return _LINKS_CACHE[issue_key]
    result = cjt.jira_api("GET", f"/issue/{issue_key}?fields=issuelinks")
    out = []
    if result:
        for link in result.get("fields", {}).get("issuelinks", []):
            if link.get("type", {}).get("name") != "Blocks":
                continue
            full = cjt.jira_api("GET", f"/issueLink/{link['id']}")
            if not full:
                continue
            out.append(
                {
                    "id": full["id"],
                    "inward": full.get("inwardIssue", {}).get("key"),
                    "outward": full.get("outwardIssue", {}).get("key"),
                }
            )
    _LINKS_CACHE[issue_key] = out
    return out


def find_link(links, a, b):
    """Return the link record whose two issues are exactly {a, b}, or None."""
    for link in links:
        if {link["inward"], link["outward"]} == {a, b}:
            return link
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--dry-run", action="store_true", help="plan only, no writes")
    p.add_argument("--doc", default=str(DOC))
    p.add_argument("--state", default=str(STATE))
    args = p.parse_args(argv)

    for var in ("JIRA_SITE", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        if not os.environ.get(var):
            sys.exit(f"error: {var} not set (run source-env.sh / source .env)")

    if not Path(args.state).exists():
        sys.exit(f"error: state file not found: {args.state}")
    keymap = json.loads(Path(args.state).read_text())["created"]  # doc-id -> jira key

    _, tickets = cjt.parse_hierarchy(args.doc)

    counts = {"ok": 0, "recreate": 0, "create": 0, "skip": 0}
    for blocker, blocked, edge in expected_edges(tickets, keymap):
        links = blocks_links_of(blocked)
        link = find_link(links, blocker, blocked)
        if link is None:
            action = "create"
        else:
            action = classify_link(link["inward"], link["outward"], blocker, blocked)

        counts[action] += 1
        if action == "ok":
            continue
        if action == "create":
            print(f"  ➕ CREATE  {edge}: {blocker} blocks {blocked}")
            if not args.dry_run:
                cjt.create_issue_link("Blocks", inward_key=blocker, outward_key=blocked)
                time.sleep(cjt.API_DELAY_MS / 1000)
        elif action == "recreate":
            print(f"  🔄 FIX     {edge}: link {link['id']} reversed -> {blocker} blocks {blocked}")
            if not args.dry_run:
                cjt.jira_api("DELETE", f"/issueLink/{link['id']}")
                time.sleep(cjt.API_DELAY_MS / 1000)
                cjt.create_issue_link("Blocks", inward_key=blocker, outward_key=blocked)
                time.sleep(cjt.API_DELAY_MS / 1000)

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"\n{mode}: ok={counts['ok']} recreated={counts['recreate']} "
        f"created={counts['create']} (edges skipped for missing keys: {counts['skip']})"
    )
    if counts["recreate"] == 0 and counts["create"] == 0:
        print("Jira Blocks links match the doc — nothing to do.")


if __name__ == "__main__":
    main()
