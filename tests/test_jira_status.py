"""Tests for tools/jira_status.py — the pure PR→status decision logic.

Covers desired_for_pr() (the decision core of the reconciler). Pure function,
no network; the Jira/GitHub reads/writes live in _run_set/_run_reconcile and are
exercised manually. An autouse fixture patches urllib.request.urlopen to raise so
nothing can escape.
"""

import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import jira_status as js  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("network call escaped to urllib.request.urlopen")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def test_merged_pr_is_done():
    assert js.desired_for_pr(merged=True, state="closed") == "51"


def test_open_pr_is_in_review():
    assert js.desired_for_pr(merged=False, state="open") == "41"


def test_closed_unmerged_pr_is_in_review():
    """A PR that exists (even if closed without merging) means review happened."""
    assert js.desired_for_pr(merged=False, state="closed") == "41"


def test_no_pr_is_in_progress():
    assert js.desired_for_pr(merged=None, state=None) == "31"


def test_merged_takes_precedence_over_state():
    """Even if state is somehow odd, merged=True => Done."""
    assert js.desired_for_pr(merged=True, state=None) == "51"


def test_transition_id_table_is_consistent():
    """The ID <-> name maps are inverse."""
    assert js.NAME_TO_ID["Done"] == "51"
    assert js.NAME_TO_ID["In Progress"] == "31"
    assert js.NAME_TO_ID["In Review"] == "41"
    assert all(v in js.TRANSITIONS for v in js.NAME_TO_ID.values())
