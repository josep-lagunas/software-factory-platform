#!/usr/bin/env python3
"""notify-run — Slack reporting glue for run-ticket.sh (on-the-fly, no ticket).

Wraps the ticket pipeline and reports to the Slack channel using the landed
communication.interfaces.slack_outbound (SFP-133/doc-116). This is OPERATIONAL
GLUE, not platform code: it exists so the factory narrates its runs while the
formal wiring (doc-119 NotifyUser handlers) arrives.

Usage:
    ./scripts/notify-run.sh SFP-137 --slug whatever [--resume]   # same args as run-ticket.sh

Posts:
  - a "started" message when the run begins;
  - the outcome (slice ok / slice aborted + the SFP-236 detail line) as a
    REPLY IN THE THREAD of the started message (the UserInteraction pattern).
Never fails the run if Slack is down (best-effort; stderr note only).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "communication" / "src"))

from communication.interfaces.slack_outbound import SlackOutboundClient  # noqa: E402
from sfp_config import LocalSecretProvider  # noqa: E402

_OUTCOME_RE = re.compile(r"(slice (?:ok|aborted): .*)")


def _post(client: SlackOutboundClient, text: str, thread: str | None = None) -> str | None:
    """Best-effort post; returns the thread_ts of the message (for replies)."""
    try:
        receipt = client.send_message(text)
        if receipt.ok:
            return receipt.thread_ref
        print(f"[notify-run] slack refused: {receipt.error}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — glue must never break the run
        print(f"[notify-run] slack unreachable: {type(exc).__name__}", file=sys.stderr)
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: notify-run.sh <SFP-XXX> [run-ticket args...]", file=sys.stderr)
        return 2
    ticket = sys.argv[1]
    args = sys.argv[1:]

    client = SlackOutboundClient(LocalSecretProvider())
    thread = _post(client, f"🚀 `{ticket}` — pipeline started")

    proc = subprocess.run(
        [str(REPO / "run-ticket.sh"), *args], cwd=REPO, capture_output=True, text=True
    )
    if proc.stdout:
        print(proc.stdout, end="")

    outcome = None
    m = _OUTCOME_RE.search(proc.stdout or "")
    if m:
        outcome = m.group(1)
    if outcome:
        text = outcome
    elif proc.returncode == 0:
        text = "✅ exit 0"
    else:
        text = f"❌ exit {proc.returncode} (no outcome line)"
    emoji = "🎉" if outcome and outcome.startswith("slice ok") else ("⚠️" if outcome else "❓")
    _post(client, f"{emoji} `{ticket}` — {text}", thread=thread)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
