#!/usr/bin/env python3
"""
SFP — Reconcile Jira Blocks dependency links to the doc source of truth.

For every `Deps:` edge in docs/SFP_Ticket_Hierarchy.md (ticket T depends on D),
ensure the Jira Blocks link means "D blocks T": inwardIssue = D (the BLOCKER),
outwardIssue = T (the BLOCKED). Correct links are left untouched; reversed or
duplicate links are normalized (delete all matching, create one correct);
missing links are created.

Idempotent: re-running reports zero changes once Jira matches the doc.

Robustness (SFP-197 follow-up): transient network errors are retried; a failed
READ is recorded as an ERROR for that edge and skipped (never mistaken for a
missing link, which would create a duplicate); every edge outcome is written to
a JSONL log so you can see exactly which edges failed. Re-run until the log
shows 0 errors and 0 pending changes.

Driven by the doc (source of truth) + .jira_creation_state.json (doc-number ->
Jira-key map). Reuses tools/create_jira_tickets.py helpers (parse_hierarchy,
jira_api, create_issue_link). Does NOT touch non-Blocks links or tickets absent
from the state map. See SFP-196 for the link-direction semantics.

Usage:
    python3 tools/reconcile_dep_links.py --dry-run               # plan only
    python3 tools/reconcile_dep_links.py                         # apply
    python3 tools/reconcile_dep_links.py --log reconcile.jsonl   # default

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


# ============================================================
# PURE LOGIC (unit-tested, no network)
# ============================================================


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


def plan_for_matches(matches, blocker, blocked):
    """Given all current Blocks links between `blocker` and `blocked`, return
    (action, ids_to_delete, should_create).

    - ("ok", [], False)              -> exactly one correct link; nothing to do.
    - ("recreate", [...ids], True)   -> normalize: delete every listed link and
                                       create exactly one correct one. Covers a
                                       single reversed link AND duplicates.
    """
    correct = [m for m in matches if m["inward"] == blocker and m["outward"] == blocked]
    if len(matches) == 1 and correct:
        return "ok", [], False
    return "recreate", [m["id"] for m in matches], True


# ============================================================
# NETWORK (retried, memoized, error-aware)
# ============================================================


def api_retry(method, endpoint, data=None, attempts=4, base_backoff=1.5):
    """Call cjt.jira_api with exponential backoff on failure (None result).

    cjt.jira_api returns None only on a real HTTP/request error; a legitimate
    empty body (e.g. DELETE 204) comes back as {"ok": True}. So None reliably
    means "retry". Returns the jira_api result (possibly None after all attempts).
    """
    result = None
    for attempt in range(attempts):
        result = cjt.jira_api(method, endpoint, data)
        if result is not None:
            return result
        time.sleep(base_backoff**attempt)
    return result


_LINKS_CACHE: dict[str, list[dict]] = {}
_LINKS_ERRORED: set[str] = set()


def blocks_links_of(issue_key):
    """Full Blocks link records involving `issue_key`: list of {id, inward, outward}.

    Returns None on a hard read failure (after retries) — DISTINCT from [] which
    means the issue legitimately has no Blocks links. Memoized per issue so each
    is read once; a failed read is remembered (not retried per-edge) and recorded
    in _LINKS_ERRORED.
    """
    if issue_key in _LINKS_CACHE:
        return _LINKS_CACHE[issue_key]
    if issue_key in _LINKS_ERRORED:
        return None
    result = api_retry("GET", f"/issue/{issue_key}?fields=issuelinks")
    if result is None:
        _LINKS_ERRORED.add(issue_key)
        _LINKS_CACHE[issue_key] = None  # type: ignore[assignment]
        return None
    out = []
    for link in result.get("fields", {}).get("issuelinks", []):
        if link.get("type", {}).get("name") != "Blocks":
            continue
        full = api_retry("GET", f"/issueLink/{link['id']}")
        if full is None:
            # A single link record unreadable: skip it (will be caught on re-run
            # if it leaves the edge inconsistent). Don't fail the whole issue.
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


def matches_between(links, a, b):
    """All link records whose two issues are exactly {a, b}."""
    return [ln for ln in (links or []) if {ln["inward"], ln["outward"]} == {a, b}]


# ============================================================
# MAIN
# ============================================================


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--dry-run", action="store_true", help="plan only, no writes")
    p.add_argument("--doc", default=str(DOC))
    p.add_argument("--state", default=str(STATE))
    p.add_argument(
        "--log",
        default="reconcile_log.jsonl",
        help="JSONL per-edge outcome log (default: reconcile_log.jsonl)",
    )
    args = p.parse_args(argv)

    for var in ("JIRA_SITE", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        if not os.environ.get(var):
            sys.exit(f"error: {var} not set (run source-env.sh / source .env)")

    if not Path(args.state).exists():
        sys.exit(f"error: state file not found: {args.state}")
    keymap = json.loads(Path(args.state).read_text())["created"]  # doc-id -> jira key

    _, tickets = cjt.parse_hierarchy(args.doc)

    counts = {"ok": 0, "recreate": 0, "create": 0, "error": 0}
    log_lines = []

    def record(edge, action, detail=""):
        counts[action] = counts[action] + 1
        line = {"edge": edge, "action": action, "detail": detail}
        log_lines.append(line)
        tag = {
            "ok": "✓ OK",
            "recreate": "🔄 FIX",
            "create": "➕ CREATE",
            "error": "✖ ERROR",
        }[action]
        print(f"  {tag:<10} {edge}  {detail}")

    for blocker, blocked, edge in expected_edges(tickets, keymap):
        links = blocks_links_of(blocked)
        if links is None:
            record(edge, "error", f"could not read links of {blocked} (network)")
            continue
        matches = matches_between(links, blocker, blocked)
        if not matches:
            record(edge, "create", f"{blocker} blocks {blocked}")
            if not args.dry_run:
                if (
                    api_retry(
                        "POST",
                        "/issueLink",
                        {
                            "type": {"name": "Blocks"},
                            "inwardIssue": {"key": blocker},
                            "outwardIssue": {"key": blocked},
                        },
                    )
                    is None
                ):
                    record(edge, "error", "create failed (network)")
                time.sleep(cjt.API_DELAY_MS / 1000)
            continue

        action, to_delete, should_create = plan_for_matches(matches, blocker, blocked)
        if action == "ok":
            record(edge, "ok")
            continue

        detail = f"normalize {len(matches)} link(s) -> {blocker} blocks {blocked}"
        record(edge, "recreate", detail)
        if args.dry_run:
            continue
        ok = True
        for lid in to_delete:
            if api_retry("DELETE", f"/issueLink/{lid}") is None:
                ok = False
            time.sleep(cjt.API_DELAY_MS / 1000)
        if should_create:
            if (
                api_retry(
                    "POST",
                    "/issueLink",
                    {
                        "type": {"name": "Blocks"},
                        "inwardIssue": {"key": blocker},
                        "outwardIssue": {"key": blocked},
                    },
                )
                is None
            ):
                ok = False
            time.sleep(cjt.API_DELAY_MS / 1000)
        if not ok:
            record(edge, "error", "one or more writes failed (network)")

    # write JSONL log
    log_path = Path(args.log)
    if log_lines:
        with log_path.open("w") as fh:
            for line in log_lines:
                fh.write(json.dumps(line) + "\n")

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"\n{mode}: ok={counts['ok']} recreated={counts['recreate']} "
        f"created={counts['create']} errors={counts['error']}"
    )
    print(f"log: {log_path}")
    pending = counts["recreate"] + counts["create"]
    if counts["error"] == 0 and pending == 0:
        print("Jira Blocks links match the doc — nothing to do.")
    else:
        if counts["error"]:
            print(f"⚠ {counts['error']} edge(s) had network errors — re-run to retry them.")
        if pending and not counts["error"]:
            print("⚠ pending changes remain — re-run.")

    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
