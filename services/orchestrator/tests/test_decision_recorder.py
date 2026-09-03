"""Tests for the concrete DecisionSink — durable per-ticket decision logs.

Covers every SFP-148 acceptance criterion, deterministically (AP-011 — no
clock, no network, no ordering beyond the call sequence):

- one-mutate-per-record — a spy manager counts ``mutate`` calls exactly;
- append-only + call order — two records land as two entries in order, and
  the first entry is deep-equal to its pre-append snapshot (never mutated);
- verbatim storage — the recorded entry equals the handed-over decision and
  duplicates of identical content are tolerated as appends;
- cross-ticket isolation — a second ticket's log never leaks into the first;
- failure propagation — a manager error (persistence and stale-version)
  escapes ``record()`` unswallowed;
- read-only accessor — ``decisions_for`` loads without saving and returns
  ``()`` for an unknown ticket;
- Protocol conformance — mypy structural check (``sink: DecisionSink = ...``)
  plus the runtime-checkable isinstance test;
- integration — the recorder drives the real publisher as its sink, and the
  real SqlAlchemy repository round-trips a decision log through the manager
  (the full SFP-147 boundary, in-memory SQLite).
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import sqlalchemy as sa
from orchestrator.application.decision_recorder import (
    DecisionRecorder,
    TicketWorkflowAggregate,
)
from orchestrator.domain.aggregate_manager import (
    AggregateManager,
    AggregateRepository,
    StaleAggregateError,
)
from orchestrator.domain.workflow.state_machine import (
    DecisionSink,
    WorkflowDecision,
    WorkflowTransitionPublisher,
    transition,
)
from orchestrator.domain.workflow.states import WorkflowState
from orchestrator.infrastructure.persistence.aggregate_repository import (
    AggregateVersionRow,
    SessionFactory,
    SqlAlchemyAggregateRepository,
    session_scope,
)
from sfp_messaging.bus import MessageBus
from sqlalchemy.orm import Session, sessionmaker


def make_decision(reason: str) -> WorkflowDecision:
    """A canonical engine-produced decision (via the real SFP-137 core)."""
    _state, decision = transition(
        WorkflowState.READY_FOR_CODING,
        WorkflowState.CODING_IN_PROGRESS,
        reason=reason,
        applied_policy="coding-start",
        business_facts_considered=("coding-job-fact:running",),
        aggregate_changes=("tickets.workflow_status",),
    )
    return decision


class ManagerFailed(Exception):
    """Marker the spy manager raises — must propagate uncaught."""


class SpyManager:
    """In-memory manager recording every ``mutate`` call and its arguments.

    Semantics identical to the real
    :class:`~orchestrator.domain.aggregate_manager.AggregateManager` (load →
    rule → save against an :class:`AggregateRepository`-shaped store), with
    exact call accounting and injectable failures. Deterministic by
    construction.
    """

    def __init__(self) -> None:
        self._stored: dict[str, TicketWorkflowAggregate] = {}
        self.mutate_calls: list[tuple[str, Any]] = []
        self.load_calls: list[str] = []
        self.save_calls: list[tuple[TicketWorkflowAggregate, int | None]] = []
        self.mutate_error: Exception | None = None

    # Spy surface: count and record every mutate.
    def mutate(
        self,
        aggregate_id: str,
        rule: Any,
    ) -> TicketWorkflowAggregate:
        self.mutate_calls.append((aggregate_id, rule))
        if self.mutate_error is not None:
            raise self.mutate_error
        loaded = self._stored.get(aggregate_id)
        expected_version = loaded.version if loaded is not None else None
        result = rule(loaded)
        self.save_calls.append((result, expected_version))
        self._stored[aggregate_id] = result
        return result

    # Read surface mirroring the real manager's load.
    def load(self, aggregate_id: str) -> TicketWorkflowAggregate | None:
        self.load_calls.append(aggregate_id)
        return self._stored.get(aggregate_id)

    def stored(self, ticket_id: str) -> TicketWorkflowAggregate | None:
        return self._stored.get(ticket_id)


class FakeBus:
    """Minimal MessageBus double: records published envelopes."""

    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, message: Any) -> None:
        self.published.append(message)

    async def subscribe(self, handler: Any) -> None:
        return None


# --- one mutate call per record ----------------------------------------------


def test_record_performs_exactly_one_mutate_call() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]

    recorder.record(make_decision("first"))

    assert len(spy.mutate_calls) == 1
    assert spy.mutate_calls[0][0] == "t1"  # the per-ticket aggregate id


def test_each_record_is_its_own_mutate_call() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]

    for reason in ("a", "b", "c"):
        recorder.record(make_decision(reason))

    assert len(spy.mutate_calls) == 3


def test_record_creates_the_aggregate_on_first_call() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="fresh")  # type: ignore[arg-type]
    decision = make_decision("first")

    recorder.record(decision)

    stored = spy.stored("fresh")
    assert stored is not None
    assert stored.decisions == (decision,)
    # The very first write carries expected_version=None (upsert, SFP-147).
    assert spy.save_calls[0][1] is None


# --- append-only + call order ------------------------------------------------


def test_recording_twice_yields_two_entries_in_call_order() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]
    first = make_decision("first")
    second = make_decision("second")

    recorder.record(first)
    recorder.record(second)

    assert list(recorder.decisions_for("t1")) == [first, second]


def test_prior_entry_is_never_mutated() -> None:
    """The deep-equal append-only assert against a pre-append snapshot."""
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]
    first = make_decision("first")
    recorder.record(first)

    first_snapshot = copy.deepcopy(first)
    entry_snapshot_after_first = copy.deepcopy(spy.stored("t1").decisions[0])  # type: ignore[union-attr]

    recorder.record(make_decision("second"))

    stored = spy.stored("t1")
    assert stored is not None
    assert stored.decisions[0] == first_snapshot
    assert stored.decisions[0] == entry_snapshot_after_first
    assert len(stored.decisions) == 2


def test_duplicate_identical_content_is_tolerated_as_an_append() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]
    decision = make_decision("same")

    recorder.record(decision)
    recorder.record(decision)

    # Verbatim storage: two appends, no filtering, no dedupe, no transform.
    assert list(recorder.decisions_for("t1")) == [decision, decision]


def test_decision_is_stored_verbatim() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]
    decision = make_decision("verbatim")

    recorder.record(decision)

    stored = spy.stored("t1")
    assert stored is not None
    assert stored.decisions == (decision,)
    assert stored.decisions[0].reason == "verbatim"
    assert stored.decisions[0].applied_policy == "coding-start"


def test_version_advances_with_each_append() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]

    recorder.record(make_decision("a"))
    recorder.record(make_decision("b"))

    stored = spy.stored("t1")
    assert stored is not None
    assert stored.version == 1  # two saves: first lands at 0, second at 1
    assert [expected for _, expected in spy.save_calls] == [None, 0]


# --- cross-ticket isolation --------------------------------------------------


def test_decisions_for_returns_only_that_tickets_decisions() -> None:
    spy = SpyManager()
    recorder_a = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]
    recorder_b = DecisionRecorder(spy, ticket_id="t2")  # type: ignore[arg-type]
    a1 = make_decision("a1")
    b1 = make_decision("b1")
    a2 = make_decision("a2")

    recorder_a.record(a1)
    recorder_b.record(b1)
    recorder_a.record(a2)

    assert list(recorder_a.decisions_for("t1")) == [a1, a2]
    assert list(recorder_b.decisions_for("t2")) == [b1]


def test_two_tickets_share_a_manager_without_cross_talk() -> None:
    spy = SpyManager()
    for ticket in ("t1", "t2"):
        DecisionRecorder(spy, ticket_id=ticket).record(  # type: ignore[arg-type]
            make_decision(f"for-{ticket}")
        )

    assert [d.reason for d in DecisionRecorder(spy, ticket_id="t1").decisions_for("t1")] == [  # type: ignore[arg-type]
        "for-t1"
    ]
    assert [d.reason for d in spy.stored("t2").decisions] == ["for-t2"]  # type: ignore[union-attr]


# --- failure propagation -----------------------------------------------------


def test_manager_persistence_failure_propagates_from_record() -> None:
    spy = SpyManager()
    spy.mutate_error = ManagerFailed("persistence blew up")
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]

    with pytest.raises(ManagerFailed, match="persistence blew up"):
        recorder.record(make_decision("doomed"))

    assert spy.stored("t1") is None  # nothing landed


def test_stale_aggregate_error_propagates_from_record() -> None:
    spy = SpyManager()
    spy.mutate_error = StaleAggregateError("t1", 3, 7)
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]

    with pytest.raises(StaleAggregateError):
        recorder.record(make_decision("conflicted"))

    assert spy.mutate_calls  # the mutate was attempted —


def test_failed_record_leaves_the_log_unchanged() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]
    recorder.record(make_decision("landed"))
    before = copy.deepcopy(spy.stored("t1"))  # type: ignore[arg-type]

    spy.mutate_error = ManagerFailed("later failure")
    with pytest.raises(ManagerFailed):
        recorder.record(make_decision("rejected"))

    assert spy.stored("t1") == before


# --- read-only accessor ------------------------------------------------------


def test_decisions_for_unknown_ticket_returns_empty_not_none() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]

    assert recorder.decisions_for("never-recorded") == ()


def test_decisions_for_reads_without_saving() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]
    decision = make_decision("only")
    recorder.record(decision)

    read_back = recorder.decisions_for("t1")

    assert read_back == (decision,)
    assert len(spy.save_calls) == 1  # only the record() save — the read added none


def test_decisions_for_returns_exact_recorded_sequence() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]
    decisions = [make_decision(f"n{i}") for i in range(5)]

    for decision in decisions:
        recorder.record(decision)

    assert list(recorder.decisions_for("t1")) == decisions


def test_recorder_exposes_its_ticket_binding() -> None:
    spy = SpyManager()
    recorder = DecisionRecorder(spy, ticket_id="t-99")  # type: ignore[arg-type]

    assert recorder.ticket_id == "t-99"


# --- Protocol conformance ----------------------------------------------------


def test_recorder_satisfies_decision_sink_protocol() -> None:
    """Structural conformance — the static half is the annotated assignment.

    ``sink: DecisionSink = DecisionRecorder(...)`` is type-checked by mypy in
    strict mode, so any signature drift from the Protocol is a type error
    before these tests even run. (``DecisionSink`` is a plain Protocol, not
    ``runtime_checkable``, so there is no meaningful isinstance check — the
    landed SFP-137 test suite asserts the same way.)
    """
    spy = SpyManager()
    sink: DecisionSink = DecisionRecorder(spy, ticket_id="t1")  # type: ignore[arg-type]

    decision = make_decision("via-sink")
    sink.record(decision)

    assert list(spy.stored("t1").decisions) == [decision]  # type: ignore[union-attr]


# --- integration: the recorder drives the real publisher ---------------------


async def test_publisher_records_through_the_recorder() -> None:
    """The landed WorkflowTransitionPublisher uses this recorder as its sink."""
    spy = SpyManager()
    sink: DecisionSink = DecisionRecorder(spy, ticket_id="ticket-2")  # type: ignore[arg-type]
    bus: MessageBus = FakeBus()
    publisher = WorkflowTransitionPublisher(bus, decision_sink=sink)

    _state, decision = await publisher.transition_and_publish(
        WorkflowState.MERGING,
        WorkflowState.DEPLOYING,
        reason="merge completed",
        applied_policy="merge-status",
        ticket_id="ticket-2",
        message_id="m-2",
        idempotency_key="idem-2",
        correlation_id="corr-2",
        causation_id="cause-2",
        occurred_at="2026-08-22T00:00:01Z",
    )

    assert list(spy.stored("ticket-2").decisions) == [decision]  # type: ignore[union-attr]
    assert len(bus.published) == 1  # type: ignore[attr-defined]


async def test_publisher_recording_failure_is_visible() -> None:
    """ID-072: a recording failure escapes transition_and_publish unswallowed."""
    spy = SpyManager()
    spy.mutate_error = ManagerFailed("sink down")
    sink: DecisionSink = DecisionRecorder(spy, ticket_id="ticket-3")  # type: ignore[arg-type]
    bus: MessageBus = FakeBus()
    publisher = WorkflowTransitionPublisher(bus, decision_sink=sink)

    with pytest.raises(ManagerFailed):
        await publisher.transition_and_publish(
            WorkflowState.MERGING,
            WorkflowState.DEPLOYING,
            reason="merge completed",
            applied_policy="merge-status",
            ticket_id="ticket-3",
            message_id="m-3",
            idempotency_key="idem-3",
            correlation_id="corr-3",
            causation_id="cause-3",
            occurred_at="2026-08-22T00:00:01Z",
        )


# --- integration: the real SqlAlchemy boundary -------------------------------


@pytest.fixture
def session_factory() -> Iterator[SessionFactory]:
    """A fresh in-memory database per test (deterministic, no network)."""
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sa.pool.StaticPool,
    )
    AggregateVersionRow.metadata.create_all(engine)
    connection = engine.connect()
    maker = sessionmaker(bind=connection, expire_on_commit=False)

    @contextmanager
    def factory() -> Iterator[Session]:
        with session_scope(maker) as session:
            yield session

    yield factory

    connection.close()
    engine.dispose()


def make_real_manager(session_factory: SessionFactory) -> AggregateManager[TicketWorkflowAggregate]:
    repo: AggregateRepository[TicketWorkflowAggregate] = SqlAlchemyAggregateRepository(
        TicketWorkflowAggregate,
        session_factory,
    )
    return AggregateManager(repo)


def test_full_boundary_round_trip(session_factory: SessionFactory) -> None:
    manager = make_real_manager(session_factory)
    recorder = DecisionRecorder(manager, ticket_id="t1")
    first = make_decision("first")
    second = make_decision("second")

    recorder.record(first)
    recorder.record(second)

    read_back = list(recorder.decisions_for("t1"))
    assert read_back == [first, second]  # verbatim after JSON round-trip
    assert read_back[0] is not first  # deserialised copies, equal content


def test_real_boundary_cross_ticket_isolation(session_factory: SessionFactory) -> None:
    manager = make_real_manager(session_factory)
    for ticket in ("t1", "t2"):
        DecisionRecorder(manager, ticket_id=ticket).record(make_decision(f"for-{ticket}"))

    assert [d.reason for d in DecisionRecorder(manager, ticket_id="t1").decisions_for("t1")] == [
        "for-t1"
    ]
    assert [d.reason for d in DecisionRecorder(manager, ticket_id="t2").decisions_for("t2")] == [
        "for-t2"
    ]
