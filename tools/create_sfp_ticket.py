#!/usr/bin/env python3
"""
SFP — single-ticket creator/normalizer (SFP-194).

A thin stdlib CLI over the helpers in tools/create_jira_tickets.py (cjt). It
creates ONE Jira ticket (Task) in the SFP project, or updates an existing one,
using the canonical SFP summary/label conventions:

    summary = "[AREA] {emoji} Title"        # emoji from executor (bot->🤖, human->👤)
    labels  = [phase, executor_label, area, *extras]   # sanitized + deduped

Reused from cjt (NEVER reimplemented here): markdown_to_adf, build_issue_labels,
sanitize_label, jira_api, create_issue. See ID-070 (ticket template) and
tools/create_jira_tickets.py.

Usage:
    # create (default)
    python3 tools/create_sfp_ticket.py --title "Title" --area SPEC \\
        --executor bot --phase platform --description body.md

    # update
    python3 tools/create_sfp_ticket.py --update --key SFP-XXX \\
        --summary "[AREA] 🤖 Title" --description-source existing \\
        --labels-mode merge --labels "extra1,extra2"

Environment (required before any network call): JIRA_SITE, JIRA_EMAIL,
JIRA_API_TOKEN (optionally JIRA_PROJECT, default SFP).
"""

import argparse
import sys
from pathlib import Path

# tools/ on sys.path so the sibling helper module imports unchanged.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_jira_tickets as cjt  # noqa: E402

# ============================================================
# CONVENTIONS — emoji <-> executor, executor -> label
# ============================================================

# executor (as used on the CLI) -> heading/summary emoji
EMOJI = {"bot": "\U0001f916", "human": "\U0001f464"}  # 🤖 / 👤
# executor -> Jira label
EXECUTOR_LABEL = {"bot": "ai-agent", "human": "manual"}


# ============================================================
# SMALL HELPERS
# ============================================================


def _parse_csv(value):
    """Split a 'a,b,c' arg into a clean list (drop empty/whitespace entries)."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _read_description(desc_arg):
    """Read the description body: '-' means stdin, else a file path."""
    if desc_arg == "-":
        return sys.stdin.read()
    return Path(desc_arg).read_text()


def _require_env():
    """JIRA_SITE/JIRA_EMAIL/JIRA_API_TOKEN must be set before any network call.

    Reuses the cjt module globals (which themselves read os.environ at import);
    tests monkeypatch these attributes directly on cjt.
    """
    missing = [
        name
        for name, val in (
            ("JIRA_SITE", cjt.JIRA_SITE),
            ("JIRA_EMAIL", cjt.JIRA_EMAIL),
            ("JIRA_API_TOKEN", cjt.JIRA_API_TOKEN),
        )
        if not val
    ]
    if missing:
        print("❌ Missing env vars: " + ", ".join(missing))
        sys.exit(1)


# ============================================================
# CREATE
# ============================================================


def run_create(args):
    # Validate required create flags first (fail fast on user input).
    required = {
        "--title": args.title,
        "--area": args.area,
        "--executor": args.executor,
        "--description": args.description,
    }
    missing = [flag for flag, val in required.items() if not val]
    # --phase and --deps are accepted but optional. --deps is parsed by argparse
    # but dependency *linking* is not part of the single-ticket create contract
    # (the batch creator does it via cjt.create_issue_link); it is intentionally
    # not actioned here.
    if missing:
        print("❌ create requires: " + ", ".join(missing))
        sys.exit(1)
    _require_env()

    emoji = EMOJI[args.executor]
    executor_label = EXECUTOR_LABEL[args.executor]
    summary = f"[{args.area}] {emoji} {args.title}"

    extras = _parse_csv(args.labels)
    raw_labels = []
    if args.phase:
        raw_labels.append(args.phase)
    raw_labels.append(executor_label)
    raw_labels.append(args.area)
    raw_labels.extend(extras)
    labels = cjt.build_issue_labels({"labels": raw_labels})

    text = _read_description(args.description)
    description = cjt.markdown_to_adf(text)

    fields = {
        "project": {"key": cjt.JIRA_PROJECT},
        "issuetype": {"name": "Task"},
        "summary": summary,
        "description": description,
        "labels": labels,
    }
    key = cjt.create_issue(fields)
    if key:
        print(key)
    else:
        sys.exit(1)


# ============================================================
# UPDATE
# ============================================================


def _resolve_update_description(existing, args):
    """Resolve the description for an update PUT.

    description-source=existing -> take fields.description with R1 dispatch:
        ADF dict  -> passthrough verbatim (do not re-convert),
        str       -> cjt.markdown_to_adf(str),
        None      -> empty doc (normalize).
    description-source=file -> read --description-file -> cjt.markdown_to_adf.
    """
    fields = (existing or {}).get("fields") or {}
    if args.description_source == "existing":
        desc = fields.get("description")
        if isinstance(desc, dict):  # R1: already ADF -> passthrough verbatim
            return desc
        if desc is None:
            desc = ""
        return cjt.markdown_to_adf(desc)
    # file
    if not args.description_file:
        print("❌ --description-source file requires --description-file")
        sys.exit(1)
    text = Path(args.description_file).read_text()
    return cjt.markdown_to_adf(text)


def _resolve_update_labels(existing, args):
    """Resolve labels for an update PUT.

    merge   -> dedupe(existing ∪ parsed), existing order first (parsed sanitized).
    replace -> only sanitized parsed labels (existing dropped).
    """
    fields = (existing or {}).get("fields") or {}
    existing_labels = fields.get("labels") or []
    parsed = _parse_csv(args.labels)
    if args.labels_mode == "merge":
        merged = list(existing_labels)
        for lbl in parsed:
            san = cjt.sanitize_label(lbl)
            if san not in merged:
                merged.append(san)
        return merged
    # replace
    return [cjt.sanitize_label(lbl) for lbl in parsed]


def run_update(args):
    required = {
        "--key": args.key,
        "--summary": args.summary,
        "--description-source": args.description_source,
    }
    missing = [flag for flag, val in required.items() if not val]
    if missing:
        print("❌ update requires: " + ", ".join(missing))
        sys.exit(1)
    _require_env()

    existing = cjt.jira_api("GET", f"/issue/{args.key}")
    if not existing:
        print("❌ could not fetch " + args.key)
        sys.exit(1)

    description = _resolve_update_description(existing, args)
    labels = _resolve_update_labels(existing, args)

    fields = {
        "summary": args.summary,
        "description": description,
        "labels": labels,
    }
    cjt.jira_api("PUT", f"/issue/{args.key}", {"fields": fields})
    print(args.key)


# ============================================================
# CLI
# ============================================================


def build_parser():
    p = argparse.ArgumentParser(
        prog="create_sfp_ticket.py",
        description="Create or update a single SFP Jira ticket (SFP-194). "
        "Reuses tools/create_jira_tickets.py helpers.",
    )
    p.add_argument(
        "--update", action="store_true", help="update an existing ticket instead of creating one"
    )
    # ---- create-only ----
    p.add_argument("--title", help="ticket title (create)")
    p.add_argument("--area", help="area tag, e.g. SPEC (create)")
    p.add_argument("--executor", choices=list(EMOJI), help="who executes: bot or human (create)")
    p.add_argument(
        "--phase", choices=["manual-core", "platform"], help="phase label (create, optional)"
    )
    p.add_argument(
        "--deps",
        help="comma-separated dependency ids (create, accepted; "
        "linking is out of scope for single-ticket create)",
    )
    p.add_argument("--labels", help="comma-separated extra labels (create + update)")
    p.add_argument("--description", help="path to a markdown body file, or '-' for stdin (create)")
    # ---- update-only ----
    p.add_argument("--key", help="existing issue key, e.g. SFP-194 (update)")
    p.add_argument("--summary", help="new summary (update)")
    p.add_argument(
        "--description-source",
        choices=["existing", "file"],
        help="where the update description comes from (update)",
    )
    p.add_argument(
        "--description-file",
        help="markdown body file (update; required with --description-source file)",
    )
    p.add_argument(
        "--labels-mode",
        choices=["merge", "replace"],
        default="merge",
        help="merge = existing ∪ parsed (default); replace = drop existing (update)",
    )
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.update:
        run_update(args)
    else:
        run_create(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
