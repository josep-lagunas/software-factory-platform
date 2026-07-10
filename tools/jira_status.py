#!/usr/bin/env python3
"""SFP — set or reconcile a Jira ticket's status against its real pipeline state.

Two modes:

  set <KEY> <TRANSITION_ID>
      Transition one ticket. IDs: 11=To Do, 21=Blocked, 31=In Progress,
      41=In Review, 51=Done.

  reconcile <KEY>=<PR#> [<KEY>=<PR#> ...]
      For each ticket, derive the correct status from its GitHub PR's real state
      and fix drift:
        PR merged                -> Done (51)
        PR open / closed-unmerged -> In Review (41)
        (no PR / not found)      -> In Progress (31)
      Idempotent: transitions only when the current status differs. This is the
      self-healing guard against forgotten manual transitions — run it after every
      merge or whenever status is uncertain.

Owned by the Coder (MAS §9.6 / ID-072): the Coder transitions its own ticket
(In Progress on start, In Review on PR open, Done on merge execution). The
Orchestrator may run `reconcile` to correct drift. Phase A stand-in for Phase B's
Orchestrator-owned programmatic transitions (ID-072).

Env (required): JIRA_SITE, JIRA_EMAIL, JIRA_API_TOKEN; for `reconcile` also
GITHUB_REPO (owner/name) and GITHUB_TOKEN_CODER. Load via source-env.sh / .env.

Examples:
    python3 tools/jira_status.py set SFP-199 41
    python3 tools/jira_status.py reconcile SFP-27=21 SFP-28=20 SFP-30=22
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

TRANSITIONS = {"11": "To Do", "21": "Blocked", "31": "In Progress", "41": "In Review", "51": "Done"}
NAME_TO_ID = {v: k for k, v in TRANSITIONS.items()}


def desired_for_pr(merged: bool | None, state: str | None) -> str:
    """Return the transition ID matching a GitHub PR's real state.

    merged=True             -> Done (51)
    PR exists (open/closed) -> In Review (41)  [closed-unmerged still means a PR
                                                  existed and is under review]
    no PR / not found        -> In Progress (31)
    """
    if merged:
        return "51"
    if state in ("open", "closed"):
        return "41"
    return "31"


def _request(
    url: str,
    cred_user: str,
    cred_pass: str,
    data: dict[str, Any] | None = None,
    method: str = "GET",
) -> dict[str, Any]:
    token = base64.b64encode(f"{cred_user}:{cred_pass}".encode()).decode()
    headers = {"Authorization": "Basic " + token}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return {} if resp.status == 204 or not raw else json.loads(raw)
    except urllib.error.HTTPError as exc:  # surfaced for diagnosis, not retried here
        return {"_http_error": exc.code, "_body": exc.read().decode(errors="replace")}


def jira(
    site: str,
    email: str,
    token: str,
    method: str,
    path: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _request(f"{site}/rest/api/3/{path.lstrip('/')}", email, token, data=data, method=method)


def gh_pr_state(repo: str, token: str, pr: str) -> tuple[str | None, bool | None]:
    """Return (state, merged) for a GitHub PR number."""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr}"
    d = _request(url, "x-access-token", token)
    return d.get("state"), d.get("merged")


def current_status(site: str, email: str, token: str, key: str) -> str | None:
    d = jira(site, email, token, "GET", f"issue/{key}?fields=status")
    name = d.get("fields", {}).get("status", {}).get("name")
    return name if isinstance(name, str) else None


def transition(site: str, email: str, token: str, key: str, tid: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"transition": {"id": tid}}
    return jira(site, email, token, "POST", f"issue/{key}/transitions", payload)


def _run_set(site: str, email: str, token: str, key: str, tid: str) -> int:
    transition(site, email, token, key, tid)
    now = current_status(site, email, token, key)
    print(f"{key} -> {TRANSITIONS.get(tid, tid)} | now: {now}")
    return 0


def _run_reconcile(
    site: str,
    email: str,
    token: str,
    repo: str,
    ghtok: str,
    specs: list[str],
) -> int:
    for spec in specs:
        key, pr = spec.split("=", 1)
        state, merged = gh_pr_state(repo, ghtok, pr)
        want = desired_for_pr(merged, state)
        cur = current_status(site, email, token, key)
        want_name = TRANSITIONS[want]
        if cur == want_name:
            print(f"  {key}: {cur} (PR #{pr} {state},merged={merged}) - OK")
        else:
            transition(site, email, token, key, want)
            print(f"  {key}: {cur} -> {want_name} (PR #{pr} {state},merged={merged}) - FIXED")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.exit(__doc__)
    mode = argv[0]
    site = os.environ["JIRA_SITE"]
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]

    if mode == "set":
        if len(argv) != 3:
            sys.exit("usage: set <KEY> <TRANSITION_ID>")
        return _run_set(site, email, token, argv[1], argv[2])

    if mode == "reconcile":
        if len(argv) < 2:
            sys.exit("usage: reconcile <KEY>=<PR#> [<KEY>=<PR#> ...]")
        repo = os.environ["GITHUB_REPO"]
        ghtok = os.environ["GITHUB_TOKEN_CODER"]
        return _run_reconcile(site, email, token, repo, ghtok, argv[1:])

    sys.exit(f"unknown mode: {mode}")


if __name__ == "__main__":
    sys.exit(main())
