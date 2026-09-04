"""Tests for the TicketSummary/Project read-model queries (MAS §5.12, SFP-159).

Covers every SFP-159 acceptance criterion, deterministically (AP-011 — no
clock, no network, no ordering beyond the decision log's append order):

- single summary — projects ``current_state`` / ``decision_count`` /
  ``last_reason`` / ``last_transition_at_sequence`` from a fixture decision
  log (SFP-148 shape, built through the real SFP-137 ``transition`` core);
- empty log — the explicit unknown shape (``state_known=False``, no fake
  state string, no fake position);
- fan-out — known tickets summarize; an unknown member yields
  ``state_known=False`` and the projection never raises mid-way;
- histogram — known tickets count under their state's name, unknown members
  under :data:`UNKNOWN_TICKET_STATE_KEY`;
- empty project — a valid empty :class:`ProjectView`, not an error;
- read-only surface — neither query exposes a mutating method, and retrieve
  never writes through the reader (exactly one read per ticket);
- determinism/purity — the same log yields equal views.

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
    UNKNOWN_TICKET_STATE_KEY,
    ProjectQuery,
    ProjectView,
    TicketSummaryQuery,
    TicketSummaryView,
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


# --- structural conformance: mypy proves the assignability at type-check
# time; the dir()/surface tests below are the runtime counterpart. ----------
_structural_reader: WorkflowDecisionReader = FakeReader()


# --- TicketSummaryView: single projection against a fixture log -----------
def test_summary_projects_every_field_from_the_fixture_log() -> None:
    first = make_decision("spec landed")
    second = make_decision("coding underway", final=True)
    assert first.resulting_state is not second.resulting_state  # test's premise
    reader = FakeReader({"SFP-2": (first, second)})

    view = TicketSummaryQuery(reader).retrieve("SFP-2")

    assert isinstance(view, TicketSummaryView)
    assert view.ticket_id == "SFP-2"
    assert view.state_known is True
    assert view.current_state is second.resulting_state  # the LAST decision
    assert view.current_state is not first.resulting_state
    assert view.decision_count == 2
    assert view.last_reason == "coding underway"  # the last decision's reason
    assert view.last_transition_at_sequence == 1  # 0-based position of `second`


def test_summary_single_decision_is_the_minimum_populated_shape() -> None:
    decision = make_decision("only move")
    reader = FakeReader({"SFP-3": (decision,)})

    view = TicketSummaryQuery(reader).retrieve("SFP-3")

    assert view.state_known is True
    assert view.current_state is decision.resulting_state
    assert view.decision_count == 1
    assert view.last_reason == "only move"
    assert view.last_transition_at_sequence == 0


def test_summary_empty_log_returns_explicit_unknown_shape() -> None:
    view = TicketSummaryQuery(FakeReader()).retrieve("SFP-1")

    assert view.ticket_id == "SFP-1"
    assert view.state_known is False
    # The emptiness signal is the boolean, not a sentinel state name or a
    # fake position: the fields are genuinely unset.
    assert view.current_state is None
    assert view.decision_count == 0
    assert view.last_reason == ""
    assert view.last_transition_at_sequence == -1


def test_summary_unknown_ticket_among_known_ones_is_unknown() -> None:
    reader = FakeReader({"known": (make_decision("d"),)})
    query = TicketSummaryQuery(reader)

    assert query.retrieve("known").state_known is True
    assert query.retrieve("never-recorded").state_known is False


def test_summary_rejects_non_positive_limit() -> None:
    for bad in (0, -1):
        with pytest.raises(ValueError, match="limit must be > 0"):
            TicketSummaryQuery(FakeReader(), limit=bad)


def test_summary_repeated_calls_are_stable() -> None:
    reader = FakeReader({"SFP-4": tuple(make_decision(f"m-{i}") for i in range(5))})
    query = TicketSummaryQuery(reader)

    views = [query.retrieve("SFP-4") for _ in range(3)]

    assert views[0] == views[1] == views[2]


# --- ProjectQuery: fan-out over caller-supplied membership ----------------
def test_project_fan_out_summarizes_known_tickets_in_caller_order() -> None:
    spec = make_decision("spec landed")
    coding = make_decision("coding underway", final=True)
    reader = FakeReader(
        {
            "SFP-A": (spec,),
            "SFP-B": (spec, coding),
            "SFP-C": (spec, spec),
        }
    )

    view = ProjectQuery(reader).retrieve("PROJ-1", ["SFP-B", "SFP-A"])

    assert isinstance(view, ProjectView)
    assert view.project_id == "PROJ-1"
    assert [s.ticket_id for s in view.ticket_summaries] == ["SFP-B", "SFP-A"]
    b, a = view.ticket_summaries
    assert b.state_known is True and a.state_known is True
    assert b.current_state is WorkflowState.CODING_IN_PROGRESS
    assert b.decision_count == 2
    assert b.last_transition_at_sequence == 1
    assert a.current_state is WorkflowState.READY_FOR_CODING
    assert a.decision_count == 1
    assert a.last_reason == "spec landed"
    # Histogram: one ticket per resulting state, keyed by the state's name.
    assert view.tickets_by_state == {
        WorkflowState.CODING_IN_PROGRESS.name: 1,
        WorkflowState.READY_FOR_CODING.name: 1,
    }


def test_project_unknown_ticket_projects_unknown_and_never_raises() -> None:
    reader = FakeReader({"SFP-A": (make_decision("known move"),)})

    view = ProjectQuery(reader).retrieve("PROJ-2", ["SFP-A", "GHOST-1"])

    assert len(view.ticket_summaries) == 2  # the unknown member is present
    known, ghost = view.ticket_summaries
    assert known.ticket_id == "SFP-A" and known.state_known is True
    assert ghost.ticket_id == "GHOST-1"
    assert ghost.state_known is False
    assert ghost.current_state is None
    assert ghost.decision_count == 0
    assert ghost.last_reason == ""
    assert ghost.last_transition_at_sequence == -1
    # The unknown member counts under the dedicated bucket, never as a
    # real state name.
    assert view.tickets_by_state == {
        WorkflowState.READY_FOR_CODING.name: 1,
        UNKNOWN_TICKET_STATE_KEY: 1,
    }
    assert UNKNOWN_TICKET_STATE_KEY not in {state.name for state in WorkflowState}


def test_project_all_unknown_members_is_a_valid_view() -> None:
    view = ProjectQuery(FakeReader()).retrieve("PROJ-3", ["GHOST-A", "GHOST-B"])

    assert len(view.ticket_summaries) == 2
    assert all(s.state_known is False for s in view.ticket_summaries)
    assert view.tickets_by_state == {UNKNOWN_TICKET_STATE_KEY: 2}


def test_project_histogram_counts_same_state_multiplicity() -> None:
    move = make_decision("same move")
    reader = FakeReader({"A": (move,), "B": (move,), "C": (move,)})

    view = ProjectQuery(reader).retrieve("P", ["A", "B", "C"])

    assert view.tickets_by_state == {WorkflowState.READY_FOR_CODING.name: 3}


def test_project_empty_ticket_ids_is_a_valid_empty_view() -> None:
    reader = FakeReader({"SFP-A": (make_decision("d"),)})
    query = ProjectQuery(reader)

    view = query.retrieve("PROJ-EMPTY", [])

    assert view.project_id == "PROJ-EMPTY"
    assert view.ticket_summaries == ()
    assert view.tickets_by_state == {}
    # And the reader was never consulted for a membership of zero.
    assert reader.reads == []


def test_project_fan_out_reads_each_ticket_exactly_once() -> None:
    reader = FakeReader(
        {
            "SFP-A": (make_decision("a"),),
            "SFP-B": (make_decision("b"),),
            "SFP-C": (make_decision("c"),),
        }
    )

    ProjectQuery(reader).retrieve("P", ["SFP-A", "GHOST", "SFP-B", "SFP-C"])

    assert reader.reads == ["SFP-A", "GHOST", "SFP-B", "SFP-C"]  # one each, in order


def test_project_non_positive_limit_is_rejected_at_construction() -> None:
    for bad in (0, -1):
        with pytest.raises(ValueError, match="limit must be > 0"):
            ProjectQuery(FakeReader(), limit=bad)


def test_project_query_forwards_its_limit() -> None:
    assert ProjectQuery(FakeReader()).limit == 10
    assert ProjectQuery(FakeReader(), limit=4).limit == 4


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


def test_summary_query_exposes_no_mutating_methods() -> None:
    surface = _public_names(TicketSummaryQuery(FakeReader()))

    assert surface == {"limit", "retrieve"}
    for name in surface:
        assert not any(fragment in name.lower() for fragment in MUTATING_SUBSTRINGS), (
            f"public method {name!r} looks mutating"
        )


def test_project_query_exposes_no_mutating_methods() -> None:
    surface = _public_names(ProjectQuery(FakeReader()))

    assert surface == {"limit", "retrieve"}
    for name in surface:
        assert not any(fragment in name.lower() for fragment in MUTATING_SUBSTRINGS), (
            f"public method {name!r} looks mutating"
        )


def test_summary_view_is_frozen() -> None:
    view = TicketSummaryQuery(FakeReader({"t": (make_decision("d"),)})).retrieve("t")

    with pytest.raises(Exception, match="frozen|immutable"):
        view.current_state = WorkflowState.FAILED


def test_project_view_is_frozen() -> None:
    view = ProjectQuery(FakeReader({"t": (make_decision("d"),)})).retrieve("P", ["t"])

    with pytest.raises(Exception, match="frozen|immutable"):
        view.project_id = "OTHER"


def test_summary_view_rejects_unknown_fields() -> None:
    decision = make_decision("d")
    payload: dict[str, Any] = {
        "ticket_id": "t",
        "current_state": decision.resulting_state,
        "state_known": True,
        "decision_count": 1,
        "last_reason": decision.reason,
        "last_transition_at_sequence": 0,
        "sneaky": True,
    }
    with pytest.raises(Exception, match="extra"):
        TicketSummaryView(**payload)


def test_project_view_rejects_unknown_fields() -> None:
    summary = TicketSummaryView(
        ticket_id="t",
        current_state=None,
        state_known=False,
        decision_count=0,
        last_reason="",
        last_transition_at_sequence=-1,
    )
    with pytest.raises(Exception, match="extra"):
        ProjectView(project_id="P", ticket_summaries=(summary,), bogus=True)


def test_summary_view_rejects_out_of_range_counts_and_positions() -> None:
    decision = make_decision("d")
    for bad_count, bad_position in ((-1, 0), (0, -2)):
        with pytest.raises(Exception, match="greater than or equal"):
            TicketSummaryView(
                ticket_id="t",
                current_state=decision.resulting_state,
                state_known=True,
                decision_count=bad_count,
                last_reason=decision.reason,
                last_transition_at_sequence=bad_position,
            )


# --- determinism / purity --------------------------------------------------
def test_same_log_yields_equal_summaries_and_projects() -> None:
    log = {
        "SFP-A": (make_decision("a1"), make_decision("a2", final=True)),
        "SFP-B": (make_decision("b1"),),
    }
    ids = ["SFP-A", "GHOST", "SFP-B"]

    summary_a = TicketSummaryQuery(FakeReader(log)).retrieve("SFP-A")
    summary_b = TicketSummaryQuery(FakeReader(log)).retrieve("SFP-A")
    project_a = ProjectQuery(FakeReader(log)).retrieve("PROJ-D", ids)
    project_b = ProjectQuery(FakeReader(log)).retrieve("PROJ-D", ids)

    assert summary_a == summary_b
    assert summary_a is not summary_b  # fresh object per call, equal by value
    assert project_a == project_b
    assert project_a is not project_b


def test_summary_agrees_with_workflow_context_on_state_and_count() -> None:
    # The two views are altitudes over the SAME log: the compact row must
    # never disagree with the full view on state or count.
    from orchestrator.interfaces import WorkflowContextQuery

    decisions = (make_decision("m-0"), make_decision("m-1", final=True))
    reader = FakeReader({"SFP-X": decisions})

    summary = TicketSummaryQuery(reader).retrieve("SFP-X")
    context = WorkflowContextQuery(FakeReader({"SFP-X": decisions})).retrieve("SFP-X")

    assert summary.current_state is context.current_state
    assert summary.decision_count == context.decision_count
    assert summary.state_known is context.state_known
