"""Tests for the WorkflowContextView read-model query (MAS §5.12, SFP-158).

Covers every SFP-158 acceptance criterion, deterministically (AP-011 — no
clock, no network, no ordering beyond the decision log's append order):

- empty log — explicit shape (``state_known=False``, no fake state string);
- populated log — ``current_state`` is the LAST decision's
  ``resulting_state`` and the count is the full log;
- single decision — the minimum populated shape;
- bound — 15 decisions with the default limit 10 return exactly the last 10;
  a custom smaller bound also returns the tail, in order;
- read-only surface — the query and its Protocol expose no mutating method
  (dir()/structural assertions), and retrieve never writes through the
  reader;
- determinism/purity — the same log yields equal views, and the source
  sequence is untouched.

The reader is a local fake (the Protocol's structural shape); no SFP-148
import is needed at runtime beyond the shared domain ``WorkflowDecision``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from orchestrator.domain.workflow.state_machine import (
    WorkflowDecision,
    transition,
)
from orchestrator.domain.workflow.states import WorkflowState
from orchestrator.interfaces import (
    DEFAULT_RECENT_DECISIONS_LIMIT,
    WorkflowContextQuery,
    WorkflowContextView,
    WorkflowDecisionReader,
)


def make_decision(reason: str, *, final: bool = False) -> WorkflowDecision:
    """A canonical engine-produced decision (via the real SFP-137 core).

    Two legal moves with *different* resulting states so a "latest wins"
    test can tell consecutive decisions apart.
    """
    if final:
        _state, decision = transition(
            WorkflowState.READY_FOR_CODING,
            WorkflowState.CODING_IN_PROGRESS,
            reason=reason,
            applied_policy="coding-start",
            business_facts_considered=("fact:1",),
            aggregate_changes=("tickets.workflow_status",),
        )
        return decision
    _state, decision = transition(
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        WorkflowState.READY_FOR_CODING,
        reason=reason,
        applied_policy="planner-done",
        business_facts_considered=("fact:1",),
        aggregate_changes=("tickets.workflow_status",),
    )
    return decision


class FakeReader:
    """The Protocol's shape: one read method, append order, ``()`` on miss.

    Spy counters assert the read-only surface: retrieve() must never mutate
    the store, and this class offers no method that could.
    """

    def __init__(self, log: dict[str, tuple[WorkflowDecision, ...]] | None = None) -> None:
        self._log = log or {}
        self.reads: list[str] = []

    def decisions_for(self, ticket_id: str) -> Sequence[WorkflowDecision]:
        self.reads.append(ticket_id)
        return self._log.get(ticket_id, ())


class CountingReader:
    """A reader that hands back a mutable ``list`` — retrieve must not touch it."""

    def __init__(self, decisions: list[WorkflowDecision]) -> None:
        self._decisions = decisions

    def decisions_for(self, ticket_id: str) -> list[WorkflowDecision]:
        return self._decisions


# --- structural conformance: mypy proves the assignability at type-check
# time (FakeReader structurally satisfies the read-only Protocol); the
# dir()/surface tests below are the runtime counterpart. --------------------
_structural_reader: WorkflowDecisionReader = FakeReader()


# --- empty log: explicit shape, never a fake state string ------------------
def test_empty_log_returns_explicit_unknown_shape() -> None:
    view = WorkflowContextQuery(FakeReader()).retrieve("SFP-1")

    assert isinstance(view, WorkflowContextView)
    assert view.ticket_id == "SFP-1"
    assert view.state_known is False
    assert view.current_state is None
    assert view.last_decision is None
    assert view.decision_count == 0
    assert view.recent_decisions == ()


def test_empty_log_current_state_is_none_not_a_placeholder_string() -> None:
    view = WorkflowContextQuery(FakeReader({})).retrieve("unknown")

    # The emptiness signal is the boolean, not a sentinel state name: the
    # field is genuinely unset (None), never "" / "UNKNOWN" / an enum member.
    assert view.current_state is None
    assert view.state_known is False


def test_unknown_ticket_returns_empty_not_none() -> None:
    reader = FakeReader({"known": (make_decision("d"),)})
    view = WorkflowContextQuery(reader).retrieve("never-recorded")

    assert view.decision_count == 0
    assert view.state_known is False


# --- populated log ---------------------------------------------------------
def test_populated_log_state_is_last_decisions_resulting_state() -> None:
    first = make_decision("spec landed")
    second = make_decision("coding underway", final=True)
    assert first.resulting_state is not second.resulting_state  # test's premise
    reader = FakeReader({"SFP-2": (first, second)})

    view = WorkflowContextQuery(reader).retrieve("SFP-2")

    assert view.state_known is True
    assert view.current_state is second.resulting_state  # the LAST decision
    assert view.current_state is not first.resulting_state
    assert view.last_decision == second
    assert view.decision_count == 2


def test_single_decision_is_the_minimum_populated_shape() -> None:
    decision = make_decision("only move")
    reader = FakeReader({"SFP-3": (decision,)})

    view = WorkflowContextQuery(reader).retrieve("SFP-3")

    assert view.state_known is True
    assert view.current_state is decision.resulting_state
    assert view.last_decision == decision
    assert view.decision_count == 1
    assert view.recent_decisions == (decision,)


# --- bounded recent tail ---------------------------------------------------
def test_fifteen_decisions_default_limit_returns_exactly_last_ten() -> None:
    decisions = tuple(make_decision(f"move-{i}") for i in range(15))
    reader = FakeReader({"SFP-4": decisions})

    view = WorkflowContextQuery(reader).retrieve("SFP-4")

    assert DEFAULT_RECENT_DECISIONS_LIMIT == 10
    assert len(view.recent_decisions) == 10
    assert view.recent_decisions == decisions[-10:]
    assert view.recent_decisions[0] == decisions[5]
    assert view.recent_decisions[-1] == decisions[14]
    # The count is the FULL log, independent of the tail bound.
    assert view.decision_count == 15


def test_custom_limit_bounds_the_tail_in_order() -> None:
    decisions = tuple(make_decision(f"move-{i}") for i in range(15))
    reader = FakeReader({"SFP-5": decisions})

    view = WorkflowContextQuery(reader, limit=3).retrieve("SFP-5")

    assert view.recent_decisions == decisions[-3:]
    assert view.decision_count == 15
    assert view.current_state is decisions[-1].resulting_state


def test_limit_larger_than_log_returns_whole_log() -> None:
    decisions = (make_decision("a"), make_decision("b"))
    reader = FakeReader({"SFP-6": decisions})

    view = WorkflowContextQuery(reader, limit=50).retrieve("SFP-6")

    assert view.recent_decisions == decisions
    assert view.decision_count == 2


def test_non_positive_limit_is_rejected() -> None:
    for bad in (0, -1):
        with pytest.raises(ValueError, match="limit must be > 0"):
            WorkflowContextQuery(FakeReader(), limit=bad)


def test_default_limit_is_ten() -> None:
    assert WorkflowContextQuery(FakeReader()).limit == 10


# --- read-only surface -----------------------------------------------------
MUTATING_SUBSTRINGS = (
    "record",
    "save",
    "write",
    "append",
    "mutate",
    "update",
    "delete",
    "remove",
    "clear",
    "pop",
    "insert",
    "extend",
    "add",
    "set_",
    "commit",
    "reset",
)


def _public_names(obj: Any) -> set[str]:
    return {name for name in dir(obj) if not name.startswith("_")}


def test_query_exposes_no_mutating_methods() -> None:
    surface = _public_names(WorkflowContextQuery(FakeReader()))

    assert surface == {"limit", "retrieve"}
    for name in surface:
        assert not any(fragment in name.lower() for fragment in MUTATING_SUBSTRINGS), (
            f"public method {name!r} looks mutating"
        )


def test_reader_protocol_declares_exactly_one_read_method() -> None:
    # The Protocol's declared surface is the single read method — nothing
    # that could mutate the decision store.
    assert _public_names(WorkflowDecisionReader) == {"decisions_for"}


def test_concrete_sfp148_shaped_reader_satisfies_the_protocol() -> None:
    # Structural check: an object with exactly decisions_for conforms.
    reader: WorkflowDecisionReader = FakeReader()
    query = WorkflowContextQuery(reader)
    assert query.retrieve("t").decision_count == 0


def test_retrieve_reads_once_and_never_mutates_the_store() -> None:
    decisions = (make_decision("a"), make_decision("b"))
    reader = FakeReader({"SFP-7": decisions})

    WorkflowContextQuery(reader).retrieve("SFP-7")

    assert reader.reads == ["SFP-7"]  # exactly one read


def test_retrieve_leaves_a_mutable_source_sequence_untouched() -> None:
    decisions = [make_decision(f"m-{i}") for i in range(12)]
    snapshot = list(decisions)
    query = WorkflowContextQuery(CountingReader(decisions))

    view = query.retrieve("SFP-8")

    assert decisions == snapshot  # no append/pop/reorder of the source
    assert len(view.recent_decisions) == 10


def test_view_is_frozen() -> None:
    view = WorkflowContextQuery(FakeReader({"t": (make_decision("d"),)})).retrieve("t")

    with pytest.raises(Exception, match="frozen|immutable"):
        view.current_state = WorkflowState.FAILED


def test_view_rejects_unknown_fields() -> None:
    decision = make_decision("d")
    payload: dict[str, Any] = {
        "ticket_id": "t",
        "current_state": decision.resulting_state,
        "state_known": True,
        "last_decision": decision,
        "decision_count": 1,
        "recent_decisions": (decision,),
        "sneaky": True,
    }
    with pytest.raises(Exception, match="extra"):
        WorkflowContextView(**payload)


def test_view_rejects_negative_count() -> None:
    decision = make_decision("d")
    with pytest.raises(Exception, match="greater than or equal"):
        WorkflowContextView(
            ticket_id="t",
            current_state=decision.resulting_state,
            state_known=True,
            last_decision=decision,
            decision_count=-1,
        )


# --- determinism / purity --------------------------------------------------
def test_same_log_yields_equal_views() -> None:
    decisions = tuple(make_decision(f"move-{i}") for i in range(12))
    view_a = WorkflowContextQuery(FakeReader({"SFP-9": decisions})).retrieve("SFP-9")
    view_b = WorkflowContextQuery(FakeReader({"SFP-9": decisions})).retrieve("SFP-9")

    assert view_a == view_b
    assert view_a is not view_b  # fresh object per call, equal by value


def test_repeated_calls_are_stable() -> None:
    reader = FakeReader({"SFP-10": tuple(make_decision(f"m-{i}") for i in range(5))})
    query = WorkflowContextQuery(reader)

    views = [query.retrieve("SFP-10") for _ in range(3)]

    assert views[0] == views[1] == views[2]


def test_tickets_do_not_leak_across_views() -> None:
    mine = make_decision("mine")
    other = make_decision("other")
    reader = FakeReader({"SFP-11": (mine,), "SFP-12": (other, other)})
    query = WorkflowContextQuery(reader)

    a = query.retrieve("SFP-11")
    b = query.retrieve("SFP-12")

    assert a.ticket_id == "SFP-11" and a.decision_count == 1
    assert b.ticket_id == "SFP-12" and b.decision_count == 2
    assert a.last_decision is mine
    assert b.last_decision is other
