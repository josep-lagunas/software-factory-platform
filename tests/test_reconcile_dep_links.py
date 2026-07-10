"""Tests for tools/reconcile_dep_links.py — the pure reconciliation logic.

Covers classify_link(), expected_edges(), matches_between(), and
plan_for_matches() — the decision core of the bulk Jira dependency-link
reconciler (SFP-197 + robustness follow-up). Pure functions, no network; the
actual Jira reads/writes live in main() and are exercised manually. An autouse
fixture patches urllib.request.urlopen to raise so nothing can escape.
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
    assert r.classify_link("SFP-22", "SFP-25", blocker="SFP-22", blocked="SFP-25") == "ok"


def test_classify_link_reversed_is_recreate():
    assert r.classify_link("SFP-25", "SFP-22", blocker="SFP-22", blocked="SFP-25") == "recreate"


def test_classify_link_unexpected_shape_is_recreate():
    assert r.classify_link("SFP-99", "SFP-77", blocker="SFP-22", blocked="SFP-25") == "recreate"


# --- expected_edges --------------------------------------------------------


def test_expected_edges_yields_blocker_then_blocked_and_skips_unknown():
    tickets = [
        {"id": "SFP-8", "deps": ["SFP-5"]},
        {"id": "SFP-7", "deps": ["SFP-5", "SFP-999"]},
        {"id": "SFP-6", "deps": []},
    ]
    keymap = {"SFP-5": "SFP-22", "SFP-7": "SFP-24", "SFP-8": "SFP-25"}
    edges = list(r.expected_edges(tickets, keymap))
    assert ("SFP-22", "SFP-25", "SFP-5 blocks SFP-8") in edges
    assert ("SFP-22", "SFP-24", "SFP-5 blocks SFP-7") in edges
    assert len(edges) == 2  # SFP-999 edge skipped


def test_expected_edges_skips_ticket_absent_from_keymap():
    assert list(r.expected_edges([{"id": "SFP-8", "deps": ["SFP-5"]}], {})) == []


# --- matches_between -------------------------------------------------------


def test_matches_between_matches_either_orientation():
    links = [
        {"id": "1", "inward": "SFP-22", "outward": "SFP-25"},
        {"id": "2", "inward": "SFP-30", "outward": "SFP-40"},
    ]
    assert [m["id"] for m in r.matches_between(links, "SFP-22", "SFP-25")] == ["1"]
    assert [m["id"] for m in r.matches_between(links, "SFP-25", "SFP-22")] == ["1"]


def test_matches_between_empty_when_absent():
    links = [{"id": "1", "inward": "SFP-22", "outward": "SFP-25"}]
    assert r.matches_between(links, "SFP-99", "SFP-25") == []


def test_matches_between_handles_none_links():
    """A failed read returns None — must not crash, yields no matches."""
    assert r.matches_between(None, "SFP-22", "SFP-25") == []


# --- plan_for_matches ------------------------------------------------------


def test_plan_single_correct_is_ok():
    matches = [{"id": "L1", "inward": "SFP-22", "outward": "SFP-25"}]
    assert r.plan_for_matches(matches, "SFP-22", "SFP-25") == ("ok", [], False)


def test_plan_single_reversed_is_recreate():
    matches = [{"id": "L1", "inward": "SFP-25", "outward": "SFP-22"}]
    action, delete, create = r.plan_for_matches(matches, "SFP-22", "SFP-25")
    assert action == "recreate"
    assert delete == ["L1"]
    assert create is True


def test_plan_duplicate_correct_links_are_normalized():
    """Two correct links (a timeout-induced duplicate) -> delete both, create one."""
    matches = [
        {"id": "L1", "inward": "SFP-22", "outward": "SFP-25"},
        {"id": "L2", "inward": "SFP-22", "outward": "SFP-25"},
    ]
    action, delete, create = r.plan_for_matches(matches, "SFP-22", "SFP-25")
    assert action == "recreate"
    assert sorted(delete) == ["L1", "L2"]
    assert create is True


def test_plan_correct_plus_reversed_is_normalized():
    matches = [
        {"id": "L1", "inward": "SFP-22", "outward": "SFP-25"},  # correct
        {"id": "L2", "inward": "SFP-25", "outward": "SFP-22"},  # reversed dup
    ]
    action, delete, create = r.plan_for_matches(matches, "SFP-22", "SFP-25")
    assert action == "recreate"
    assert sorted(delete) == ["L1", "L2"]
    assert create is True
