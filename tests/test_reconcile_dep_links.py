"""Tests for tools/reconcile_dep_links.py — the pure reconciliation logic.

Covers classify_link(), find_link(), and expected_edges() — the decision core of
the bulk Jira dependency-link reconciler (SFP-197). These are pure functions
with no network; the actual Jira reads/writes live in main() and are exercised
manually against the live backlog. An autouse fixture patches
urllib.request.urlopen to raise so nothing can escape to the network.
"""

import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import reconcile_dep_links as r  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("network call escaped to urllib.request.urlopen")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# --- classify_link ---------------------------------------------------------


def test_classify_link_correct_is_ok():
    """inward=blocker, outward=blocked -> already correct."""
    assert r.classify_link("SFP-22", "SFP-25", blocker="SFP-22", blocked="SFP-25") == "ok"


def test_classify_link_reversed_is_recreate():
    """The bug shape: inward=dependent(blocked), outward=dep(blocker) -> recreate."""
    assert r.classify_link("SFP-25", "SFP-22", blocker="SFP-22", blocked="SFP-25") == "recreate"


def test_classify_link_unexpected_shape_is_recreate():
    """A record that somehow doesn't match either orientation is treated as
    recreate (safe default) rather than silently ignored."""
    assert r.classify_link("SFP-99", "SFP-77", blocker="SFP-22", blocked="SFP-25") == "recreate"


# --- find_link -------------------------------------------------------------


def test_find_link_matches_either_orientation():
    """find_link must match the pair regardless of which side is inward/outward."""
    links = [
        {"id": "1", "inward": "SFP-22", "outward": "SFP-25"},
        {"id": "2", "inward": "SFP-30", "outward": "SFP-40"},
    ]
    assert r.find_link(links, "SFP-22", "SFP-25")["id"] == "1"
    assert r.find_link(links, "SFP-25", "SFP-22")["id"] == "1"  # reversed args still match


def test_find_link_none_when_absent():
    links = [{"id": "1", "inward": "SFP-22", "outward": "SFP-25"}]
    assert r.find_link(links, "SFP-99", "SFP-25") is None


# --- expected_edges --------------------------------------------------------


def test_expected_edges_yields_blocker_then_blocked_and_skips_unknown():
    """For 'T depends on D' the edge is (blocker=D, blocked=T); edges whose
    endpoints are not in the keymap are skipped."""
    tickets = [
        {"id": "SFP-8", "deps": ["SFP-5"]},  # SFP-5 blocks SFP-8
        {"id": "SFP-7", "deps": ["SFP-5", "SFP-999"]},  # SFP-999 unknown -> skipped
        {"id": "SFP-6", "deps": []},
    ]
    keymap = {"SFP-5": "SFP-22", "SFP-7": "SFP-24", "SFP-8": "SFP-25"}
    edges = list(r.expected_edges(tickets, keymap))
    assert ("SFP-22", "SFP-25", "SFP-5 blocks SFP-8") in edges
    assert ("SFP-22", "SFP-24", "SFP-5 blocks SFP-7") in edges
    # the SFP-999 edge is skipped (only 2 edges, not 3)
    assert len(edges) == 2
    assert not any(blocked == "SFP-999" or blocker == "SFP-999" for blocker, blocked, _ in edges)


def test_expected_edges_skips_ticket_absent_from_keymap():
    tickets = [{"id": "SFP-8", "deps": ["SFP-5"]}]
    assert list(r.expected_edges(tickets, {})) == []  # blocked SFP-8 not in map
