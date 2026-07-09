#!/usr/bin/env python3
"""
SFP — JIRA Ticket Creator

Reads SFP_Ticket_Hierarchy.md and creates all tickets (Epics + Tasks) in JIRA
via the REST API. Also creates epic links and Blocks dependency links.

This is the SFP adaptation of the ARCONTA creator. Differences from the ARCONTA
script:
  - Parses the SFP ticket format: `### SFP-N [AREA] 🤖/👤 — Title`
    with a `**Labels:** ... | **Deps:** ... | **Context ...:** ...` metadata line.
  - Epics are derived from `## NAME Epic` section headers (under `# MANUAL CORE`
    or `# PLATFORM`), not from numbered tickets.
  - Phase/executor/area are carried as Jira labels (`manual-core`/`platform`,
    `ai-agent`/`manual`, area) so the backlog is filterable.
  - Credentials are read from ENVIRONMENT VARIABLES (never hardcoded).

Usage:
    python3 create_jira_tickets.py --dry-run       # Preview without creating
    python3 create_jira_tickets.py                  # Create all tickets
    python3 create_jira_tickets.py --resume         # Resume from saved state
    python3 create_jira_tickets.py --update-descriptions   # Re-push descriptions to existing issues

Requirements: Python 3.10+, no external dependencies (stdlib urllib).

Configuration (env vars — set before running, do NOT hardcode the token):
    JIRA_SITE      - e.g. https://yourorg.atlassian.net
    JIRA_EMAIL     - JIRA account email
    JIRA_API_TOKEN - JIRA API token
    JIRA_PROJECT   - Project key (default SFP)
"""

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ============================================================
# CONFIGURATION — read from env (no hardcoded secrets)
# ============================================================
JIRA_SITE = os.environ.get("JIRA_SITE", "").rstrip("/")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
JIRA_PROJECT = os.environ.get("JIRA_PROJECT", "SFP")
# How tasks link to their epic. Default: the epic is the parent in the Jira issue
# hierarchy (`parent` field). If your project is company-managed and epic linking
# does not populate via `parent`, set EPIC_LINK_FIELD to the Epic Link custom field
# id (commonly customfield_10014) and the script will use that field instead.
EPIC_LINK_FIELD = os.environ.get("EPIC_LINK_FIELD", "")

HERE = Path(__file__).resolve().parent  # tools/
ROOT = HERE.parent  # repo root
HIERARCHY_FILE = ROOT / "docs" / "SFP_Ticket_Hierarchy.md"
STATE_FILE = ROOT / ".jira_creation_state.json"

# Rate limiting (ms between API calls)
API_DELAY_MS = 300

# ============================================================
# JIRA API HELPERS
# ============================================================


def get_auth_header():
    """Build Basic auth header from email:api_token."""
    credentials = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def jira_api(method, endpoint, data=None):
    """Make a JIRA REST API call."""
    url = f"{JIRA_SITE}/rest/api/3{endpoint}"
    headers = {
        "Authorization": get_auth_header(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            if not raw.strip():
                return {"ok": True, "status": resp.status}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"  ❌ API error {e.code} on {method} {endpoint}")
        print(f"     {error_body[:500]}")
        return None
    except Exception as e:
        print(f"  ❌ Request error: {e}")
        return None


def create_issue(fields):
    """Create a JIRA issue and return its key."""
    result = jira_api("POST", "/issue", {"fields": fields})
    if result:
        return result.get("key")
    return None


def create_issue_link(link_type, inward_key, outward_key):
    """Create a dependency link between two issues."""
    data = {
        "type": {"name": link_type},
        "inwardIssue": {"key": inward_key},
        "outwardIssue": {"key": outward_key},
    }
    return jira_api("POST", "/issueLink", data) is not None


def get_issue_link_types():
    result = jira_api("GET", "/issueLinkType")
    if result:
        return {lt["name"]: lt for lt in result.get("issueLinkTypes", [])}
    return {}


def get_project_issue_types():
    result = jira_api("GET", f"/project/{JIRA_PROJECT}")
    if result:
        return {it["name"]: it["id"] for it in result.get("issueTypes", [])}
    return {}


# ============================================================
# MARKDOWN PARSER (SFP format)
# ============================================================

SECTION_RE = re.compile(r"^# (MANUAL CORE|PLATFORM)")
EPIC_RE = re.compile(r"^## (.+?)\s+Epic\b")
TICKET_RE = re.compile(r"^### (SFP-\d+) \[([^\]]+)\] (🤖|👤) — (.+)$")
LABELS_RE = re.compile(r"^\*\*Labels:\*\*\s*(.+)$")


def parse_hierarchy(filepath):
    """
    Parse SFP_Ticket_Hierarchy.md into epics and tickets.

    - Epics come from `## NAME Epic` headers (under a `# MANUAL CORE` / `# PLATFORM`
      section, which disambiguates the two PREREQ epics).
    - Tickets come from `### SFP-N [AREA] 🤖/👤 — Title`, followed by a
      `**Labels:** ... | **Deps:** ... | **Context ...:** ...` metadata line.
    """
    text = Path(filepath).read_text()

    epics = []  # [{epic_id, name, phase}]
    tickets = []  # parsed tickets
    cur_epic = None
    cur_phase = None
    epic_counter = 0

    cur = None
    body_lines = []

    def flush():
        nonlocal cur, body_lines
        if cur:
            cur["description"] = "\n".join(body_lines).strip()
            tickets.append(cur)
        cur = None
        body_lines = []

    for line in text.splitlines():
        # Section banner -> phase
        sec = SECTION_RE.match(line)
        if sec:
            flush()
            cur_phase = "Manual Core" if sec.group(1) == "MANUAL CORE" else "Platform"
            continue

        # Epic header
        em = EPIC_RE.match(line)
        if em:
            flush()
            epic_counter += 1
            name = em.group(1).strip()
            cur_epic = {"epic_id": f"EPIC-{epic_counter}", "name": name, "phase": cur_phase}
            epics.append(cur_epic)
            continue

        # Ticket header
        tk = TICKET_RE.match(line)
        if tk:
            flush()
            tid, area, emoji, title = tk.groups()
            cur = {
                "id": tid,
                "num": int(tid.split("-")[1]),
                "area": area.strip(),
                "emoji": emoji,
                "executor": "manual" if emoji == "👤" else "ai-agent",
                "title": title.strip(),
                "epic_id": cur_epic["epic_id"] if cur_epic else None,
                "phase": cur_phase,
                "labels": [],
                "deps": [],
                "description": "",
            }
            continue

        if cur:
            # Metadata line (Labels | Deps | Context)
            ml = LABELS_RE.match(line)
            if ml and not cur["labels"]:
                parts = line.split("|")
                labels_part = parts[0].replace("**Labels:**", "").strip()
                cur["labels"] = [label.strip() for label in labels_part.split(",") if label.strip()]
                for p in parts:
                    if "**Deps:**" in p:
                        d = p.split("**Deps:**", 1)[1]
                        d = re.sub(r"\*\(B→A\)\*", "", d)  # strip the B→A marker
                        cur["deps"] = re.findall(r"SFP-\d+", d)
                continue
            # Horizontal rule separates tickets; skip it from the body
            if line.strip() == "---":
                continue
            body_lines.append(line)

    flush()
    tickets.sort(key=lambda t: t["num"])
    return epics, tickets


# ============================================================
# DESCRIPTION / LABEL HELPERS
# ============================================================


def inline_nodes(s):
    """Split a line into ADF text nodes, handling `code` and **bold**."""
    nodes = []
    for tok in re.split(r"(`[^`]+`|\*\*[^*]+\*\*)", s):
        if not tok:
            continue
        if tok.startswith("`") and tok.endswith("`"):
            nodes.append({"type": "text", "text": tok[1:-1], "marks": [{"type": "code"}]})
        elif tok.startswith("**") and tok.endswith("**"):
            nodes.append({"type": "text", "text": tok[2:-2], "marks": [{"type": "strong"}]})
        else:
            nodes.append({"type": "text", "text": tok})
    return nodes


def markdown_to_adf(text):
    """Convert a ticket body (markdown) to a full ADF document.

    Produces real bullet lists, code spans, bold, and h3 headings for `**Section:**`.
    """
    content = []
    bullet_buf = []

    def flush_bullets():
        if not bullet_buf:
            return
        items = []
        for raw in bullet_buf:
            # keep "[ ]" checkboxes literal (ARCONTA-consistent)
            item = re.sub(r"^- ", "", raw.strip())
            items.append(
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": inline_nodes(item)}],
                }
            )
        content.append({"type": "bulletList", "content": items})
        bullet_buf.clear()

    for line in text.split("\n"):
        s = line.strip()
        if not s:
            flush_bullets()
            continue
        m = re.match(r"\*\*([A-Z][^*]*):\*\*(.*)$", s)  # **Section:** [trailing content]
        if m:
            flush_bullets()
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": m.group(1).strip()}],
                }
            )
            trailing = m.group(2).strip()
            if trailing:  # inline content -> separate paragraph (with code/bold marks)
                content.append({"type": "paragraph", "content": inline_nodes(trailing)})
            continue
        if s.startswith("- "):  # bullet
            bullet_buf.append(s)
            continue
        flush_bullets()
        content.append({"type": "paragraph", "content": inline_nodes(s)})
    flush_bullets()
    return {"type": "doc", "version": 1, "content": content}


def build_issue_summary(ticket):
    """[AREA] 🤖/👤 Title — clean, scannable in the Jira list."""
    return f"[{ticket['area']}] {ticket['emoji']} {ticket['title']}"


def build_epic_summary(epic):
    """Epic name + phase, unique across the two PREREQ epics."""
    phase = epic.get("phase") or ""
    return f"{epic['name']} ({phase})" if phase else epic["name"]


def sanitize_label(label):
    """JIRA labels cannot contain spaces."""
    return label.replace(" ", "-")


def build_issue_labels(ticket):
    """Phase + executor + area, sanitized and deduped.

    The phase label is already present in the ticket's labels (manual-core/platform),
    so we only sanitize and dedupe here.
    """
    labels = [sanitize_label(label) for label in ticket["labels"]]
    return list(dict.fromkeys(labels))  # dedupe, preserve order


# ============================================================
# STATE MANAGEMENT (resume support)
# ============================================================


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"created": {}, "epics_created": {}, "links_created": False}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ============================================================
# MAIN
# ============================================================


def update_descriptions(tickets):
    """Re-push the (now complete) description to each existing Jira issue,
    using the doc-id → Jira-key map saved in the state file."""
    state = load_state()
    created = state.get("created", {})
    updated = failed = skipped = 0
    total = len(tickets)
    for i, t in enumerate(tickets, 1):
        key = created.get(t["id"])
        if not key:
            skipped += 1
            print(f"  ⏭️  {t['id']} not in state file — skipping")
            continue
        adf = markdown_to_adf(t["description"])
        res = jira_api("PUT", f"/issue/{key}", {"fields": {"description": adf}})
        if res:
            updated += 1
            print(f"  [{i}/{total}] ✅ {t['id']} → {key}")
        else:
            failed += 1
            print(f"  [{i}/{total}] ❌ {t['id']} → {key}")
        time.sleep(API_DELAY_MS / 1000)
    print(f"\n{'=' * 60}")
    print(f"✅ Updated: {updated} | Failed: {failed} | Skipped: {skipped}")
    if failed:
        print("   Re-run with --update-descriptions to retry failures.")


def main():
    dry_run = "--dry-run" in sys.argv
    update_desc = "--update-descriptions" in sys.argv

    # Validate config (unless dry-run)
    if not dry_run:
        missing = [k for k in ("JIRA_SITE", "JIRA_EMAIL", "JIRA_API_TOKEN") if not globals()[k]]
        if missing:
            print(f"❌ Missing env vars: {', '.join(missing)}")
            print("   Export JIRA_SITE, JIRA_EMAIL, JIRA_API_TOKEN (and optionally JIRA_PROJECT).")
            sys.exit(1)

    if not HIERARCHY_FILE.exists():
        print(f"❌ Hierarchy file not found: {HIERARCHY_FILE}")
        sys.exit(1)

    epics, tickets = parse_hierarchy(HIERARCHY_FILE)
    print(f"📋 Parsed {len(tickets)} tickets across {len(epics)} epics from {HIERARCHY_FILE.name}")
    print(
        f"   Manual Core: {sum(1 for t in tickets if t['phase'] == 'Manual Core')} | "
        f"Platform: {sum(1 for t in tickets if t['phase'] == 'Platform')}"
    )
    print(
        f"   Manual (👤): {sum(1 for t in tickets if t['executor'] == 'manual')} | "
        f"AI (🤖): {sum(1 for t in tickets if t['executor'] == 'ai-agent')}"
    )

    if update_desc:
        print(f"\n🔄 Updating descriptions for {len(tickets)} existing issues...")
        update_descriptions(tickets)
        return

    if dry_run:
        print("\n🔍 DRY RUN — preview:\n")
        for e in epics:
            print(f"  📦 EPIC: {build_epic_summary(e)}")
        print()
        for t in tickets:
            deps = ", ".join(t["deps"]) if t["deps"] else "—"
            epic = next((build_epic_summary(e) for e in epics if e["epic_id"] == t["epic_id"]), "—")
            print(f"  {t['id']} → {build_issue_summary(t)}")
            print(f"     Labels: {', '.join(build_issue_labels(t))} | Epic: {epic} | Deps: {deps}")
        print(f"\n  Total: {len(epics)} epics + {len(tickets)} issues + dependency links")
        return

    state = load_state()
    created = state.get("created", {})
    epics_created = state.get("epics_created", {})

    # ---- Verify project + issue types ----
    print("\n🔍 Verifying JIRA project and issue types...")
    issue_types = get_project_issue_types()
    if not issue_types:
        print(f"❌ Could not find project '{JIRA_PROJECT}' or no issue types.")
        sys.exit(1)
    print(f"   Project: {JIRA_PROJECT}")
    print(f"   Issue types: {issue_types}")

    type_mapping = {}
    for jira_type in ["Epic", "Task", "Story", "Bug", "Sub-task"]:
        if jira_type in issue_types:
            type_mapping[jira_type] = issue_types[jira_type]

    print("\n🔍 Verifying issue link types...")
    link_types = get_issue_link_types()
    print(f"   Available link types: {list(link_types.keys())}")

    # ---- STEP 1: Create epics ----
    print(f"\n📦 Step 1: Creating {len(epics)} epics...")
    for e in epics:
        if e["epic_id"] in epics_created:
            continue
        name = build_epic_summary(e)
        fields = {
            "project": {"key": JIRA_PROJECT},
            "summary": name,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": (f"Epic: {e['name']} — phase: {e.get('phase', '')}"),
                            }
                        ],
                    }
                ],
            },
            "issuetype": {"id": type_mapping.get("Epic", type_mapping.get("Task"))},
            "labels": [sanitize_label(e.get("phase") or "platform"), "epic"],
        }
        # Epic Name custom field (required on many Jira instances)
        fields["customfield_10011"] = name
        print(f"  📦 Creating epic {e['epic_id']}: {name}")
        key = create_issue(fields)
        if not key:
            fields.pop("customfield_10011", None)
            print("     🔄 Retrying without Epic Name custom field...")
            key = create_issue(fields)
        if key:
            epics_created[e["epic_id"]] = key
            state["epics_created"] = epics_created
            save_state(state)
            print(f"     ✅ {key}")
        time.sleep(API_DELAY_MS / 1000)

    # ---- STEP 2: Create tickets (in SFP order) ----
    print(f"\n📝 Step 2: Creating {len(tickets)} issues...")
    for t in tickets:
        if t["id"] in created:
            print(f"  ⏭️  {t['id']} already created as {created[t['id']]}")
            continue
        summary = build_issue_summary(t)
        fields = {
            "project": {"key": JIRA_PROJECT},
            "summary": summary,
            "description": markdown_to_adf(t["description"]),
            "issuetype": {"id": type_mapping.get("Task", type_mapping.get("Story"))},
            "labels": build_issue_labels(t),
        }
        # Every task MUST have its epic as parent (the epic is the parent in the
        # Jira issue hierarchy). We never strip this on retry — an orphan task
        # would violate the epic-parent requirement. If `parent` does not link on
        # your project, set EPIC_LINK_FIELD (e.g. customfield_10014) and re-run.
        epic_key = epics_created.get(t["epic_id"]) if t["epic_id"] else None
        if epic_key:
            if EPIC_LINK_FIELD:
                fields[EPIC_LINK_FIELD] = epic_key
            else:
                fields["parent"] = {"key": epic_key}

        print(f"  {t['emoji']} Creating {t['id']}: {summary[:80]}")
        key = create_issue(fields)
        if not key:
            print(
                f"     ⚠️ No parent link type set? "
                f"{'using ' + EPIC_LINK_FIELD if EPIC_LINK_FIELD else 'using parent field'}"
            )
        if key:
            created[t["id"]] = key
            state["created"] = created
            save_state(state)
            print(f"     ✅ {key}")
        else:
            print(f"     ❌ Failed to create {t['id']}")
        time.sleep(API_DELAY_MS / 1000)

    # ---- STEP 3: Dependency links (Blocks) ----
    print("\n🔗 Step 3: Creating dependency links...")
    links_created = links_failed = 0
    for t in tickets:
        if t["id"] not in created or not t["deps"]:
            continue
        blocked_key = created[t["id"]]
        for dep_id in t["deps"]:
            if dep_id not in created:
                print(f"  ⚠️ {t['id']} depends on {dep_id} (not created) — skipping link")
                continue
            blocking_key = created[dep_id]
            # Blocks: outward = blocks, inward = is blocked
            if create_issue_link("Blocks", inward_key=blocked_key, outward_key=blocking_key):
                links_created += 1
                print(f"  🔗 {dep_id} ({blocking_key}) blocks {t['id']} ({blocked_key})")
            else:
                links_failed += 1
            time.sleep(API_DELAY_MS / 1000)

    state["links_created"] = True
    save_state(state)

    # ---- SUMMARY ----
    print(f"\n{'=' * 60}")
    print("✅ CREATION COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Epics created: {len(epics_created)}/{len(epics)}")
    print(f"  Issues created: {len(created)}/{len(tickets)}")
    print(
        f"  Dependency links created: {links_created}"
        + (f"  (failed: {links_failed})" if links_failed else "")
    )
    if len(created) < len(tickets):
        missing = [t["id"] for t in tickets if t["id"] not in created]
        print(f"\n⚠️ Missing: {', '.join(missing)}")
        print("   Re-run with --resume to retry.")


if __name__ == "__main__":
    main()
