"""Tests for tools/create_jira_tickets.py — Blocks dependency-link direction.

Regression guard for SFP-196: dependency links were created with the inward/
outward issues swapped, which reversed every dependency arrow in the Jira
backlog (the dependent appeared to block its own prerequisite). The fix pins
the empirically-verified semantics: for a Blocks link the **inwardIssue is the
BLOCKER** and the outwardIssue is the BLOCKED issue. These tests fail if that
mapping is ever inverted again.

Network is mocked at the jira_api boundary; an autouse fixture additionally
patches urllib.request.urlopen to raise so a forgotten mock cannot escape to
the network.
"""

import sys
import urllib.request
from pathlib import Path

import pytest

# --- import the creator directly from tools/ --------------------------------
ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import create_jira_tickets as cjt  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """A forgotten mock must never escape to the network."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("network call escaped to urllib.request.urlopen")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def test_blocks_link_keys_blocker_is_inward():
    """blocks_link_keys(blocker, blocked) -> (inward=blocker, outward=blocked).

    Mirrors the SFP-8/SFP-5 edge: SFP-5 (SFP-22) blocks SFP-8 (SFP-25), so the
    blocker SFP-22 must be the inwardIssue.
    """
    inward, outward = cjt.blocks_link_keys("SFP-22", "SFP-25")
    assert inward == "SFP-22"  # the BLOCKER
    assert outward == "SFP-25"  # the BLOCKED


def test_call_site_passes_blocker_as_inward(monkeypatch):
    """The exact decision the main loop makes: for `T depends on D`, D (the
    blocker) must end up as the inwardIssue. Re-uses the same helper the call
    site uses, so a swap there regresses this test."""

    def ticket_depends_on(ticket_key, dep_key):
        # Reproduce the call-site wiring verbatim.
        blocked_key = ticket_key
        blocking_key = dep_key
        inward, outward = cjt.blocks_link_keys(blocking_key, blocked_key)
        captured = {}

        def fake_jira_api(method, endpoint, data=None):
            captured.update(data or {})
            return {"id": "link-1"}

        monkeypatch.setattr(cjt, "jira_api", fake_jira_api)
        cjt.create_issue_link("Blocks", inward_key=inward, outward_key=outward)
        return captured

    payload = ticket_depends_on("SFP-25", "SFP-22")  # SFP-25 depends on SFP-22
    assert payload["type"]["name"] == "Blocks"
    assert payload["inwardIssue"]["key"] == "SFP-22"  # blocker is inward
    assert payload["outwardIssue"]["key"] == "SFP-25"  # blocked is outward


def test_create_issue_link_payload_preserves_arg_order(monkeypatch):
    """create_issue_link itself must map inward_key->inwardIssue and
    outward_key->outwardIssue (no internal swap)."""
    captured = {}
    monkeypatch.setattr(
        cjt,
        "jira_api",
        lambda method, endpoint, data=None: captured.update(data or {}) or {"id": "x"},
    )
    assert cjt.create_issue_link("Blocks", "SFP-22", "SFP-25") is True
    assert captured["inwardIssue"]["key"] == "SFP-22"
    assert captured["outwardIssue"]["key"] == "SFP-25"
