"""The concrete DecisionSink — durable per-ticket decision logs (SFP-148).

Grounded in:
- MAS §8.5 — every significant workflow transition produces an immutable
  :class:`~orchestrator.domain.workflow.state_machine.WorkflowDecision`
  recording why, applied policy, facts, aggregate changes, commands emitted,
  previous state, resulting state. Decisions are immutable history (§8.12):
  once recorded, an entry is never edited, reordered, or removed.
- MAS §8.7 — every workflow-affecting output is recorded in the decision that
  caused it, so the durable decision log is the audit trail of record.
- ID-072 / MAS §8.5 — recording failures must be *visible*: the publisher
  relies on a failed ``record()`` raising so a decision can never be silently
  un-recorded, while publishing failures never alter the decision.
- MAS §9.5 / SFP-147 — every change to the per-ticket aggregate flows through
  :meth:`~orchestrator.domain.aggregate_manager.AggregateManager.mutate`
  (load → rule → save, exactly one save per call). The recorder owns **no**
  session and performs **no** direct persistence: all writes cross the
  manager's transaction boundary; reads cross its ``load``.
- SFP-137 — the :class:`~orchestrator.domain.workflow.state_machine.
  DecisionSink` Protocol this class implements *exactly* (no widened
  signature; conformance is structural and verified by mypy plus a
  runtime-checkable isinstance test).
- AP-011 — determinism: the recorder introduces no clock, no randomness and
  no I/O of its own. Order is the call order; persistence effects flow only
  through the injected manager.

Ticket identity. The landed :class:`WorkflowDecision` carries *no* ticket id
(its §8.5 field set is states/reason/policy/facts/changes/commands only), and
the Protocol forbids widening ``record``'s signature — so the recorder takes
the ticket id as a constructor binding: one :class:`DecisionRecorder` per
ticket. This is not a design this ticket improvises; it is forced by the
landed Protocol (MAS §12.9: build on what is landed, never invent), and it
matches the hosting shape — the publisher's ``decision_sink`` parameter is
supplied per call by the wiring ticket (SFP-150), which knows the ticket, so
the sink it hands over can be (and here is) ticket-scoped. ``decisions_for``
takes the id as a read parameter for the query/audit consumers (SFP-158/159).

Store-verbatim rule: the recorder never blocks, filters, transforms, or
deduplicates a decision. Duplicate identical content is tolerated as an
append — consumers own dedup by the decision's identity fields (explicitly
out of scope here).
"""

from __future__ import annotations

from collections.abc import Sequence

from orchestrator.domain.aggregate_manager import Aggregate, AggregateManager
from orchestrator.domain.workflow.state_machine import (
    DecisionSink,
    WorkflowDecision,
)

__all__ = [
    "DecisionRecorder",
    "TicketWorkflowAggregate",
]


class TicketWorkflowAggregate(Aggregate):
    """The per-ticket decisions-list aggregate behind the recorder.

    One instance per ``ticket_id`` — the ``aggregate_id`` *is* the ticket id
    (MAS §6.6: plain-string identifiers at this level). Its only business
    state is the append-only ``decisions`` sequence: entries are only ever
    appended (by the recorder's rule, in call order) — never mutated,
    reordered, or dropped — so the stored list *is* the ticket's workflow
    audit trail (MAS §8.5/§8.7). The optimistic-concurrency ``version``
    comes from :class:`~orchestrator.domain.aggregate_manager.Aggregate` and
    is managed entirely by the SFP-147 boundary.

    Persisted shape: decisions are held verbatim as domain
    :class:`WorkflowDecision` values inside this aggregate's payload — the
    SFP-147 repository serialises the whole aggregate (pydantic ``model_dump``,
    states as plain strings per ID-013) and guards it with the shared version
    row. No additional decision row/model is needed: the aggregate payload is
    the persisted shape, and no direct session use exists anywhere in this
    module (the required-gate check).
    """

    decisions: tuple[WorkflowDecision, ...] = ()


class DecisionRecorder(DecisionSink):
    """Durable, append-only decision log behind the SFP-147 boundary.

    The first concrete
    :class:`~orchestrator.domain.workflow.state_machine.DecisionSink`. It is
    bound at construction to one ticket id and one
    :class:`~orchestrator.domain.aggregate_manager.AggregateManager` over
    :class:`TicketWorkflowAggregate`, and holds no other state: durable
    state lives only behind the manager, so every recorder instance is a
    thin, deterministic adapter over the injected boundary.
    """

    def __init__(
        self,
        manager: AggregateManager[TicketWorkflowAggregate],
        *,
        ticket_id: str,
    ) -> None:
        self._manager = manager
        self._ticket_id = ticket_id

    @property
    def ticket_id(self) -> str:
        """The ticket this recorder is bound to (the aggregate's id)."""
        return self._ticket_id

    def record(self, decision: WorkflowDecision) -> None:
        """Append ``decision`` to this ticket's aggregate — one mutate call.

        The mutation rule appends the decision **verbatim** to the loaded
        aggregate (creating it on a load miss) and returns the new aggregate
        with its version bumped, so the call order is preserved positionally
        and no prior entry is ever touched. The rule never inspects,
        transforms, filters, or deduplicates the decision.

        Any exception from the manager — a persistence error, a
        :class:`~orchestrator.domain.aggregate_manager.StaleAggregateError`
        — propagates uncaught: a decision that could not be durably recorded
        must be visible to the caller (ID-072), never swallowed or retried
        here.

        Args:
            decision: the engine-produced decision to persist verbatim.
        """

        def append(existing: TicketWorkflowAggregate | None) -> TicketWorkflowAggregate:
            if existing is None:
                return TicketWorkflowAggregate(
                    aggregate_id=self._ticket_id,
                    decisions=(decision,),
                )
            return TicketWorkflowAggregate(
                aggregate_id=existing.aggregate_id,
                decisions=existing.decisions + (decision,),
                version=existing.version + 1,
            )

        self._manager.mutate(self._ticket_id, append)

    def decisions_for(self, ticket_id: str) -> Sequence[WorkflowDecision]:
        """Return the decisions recorded for ``ticket_id`` — read-only.

        Exactly the decisions appended for that ticket, in call order, and
        nothing from any other ticket (per-ticket aggregates are disjoint by
        construction: one aggregate id per ticket). A ticket with no recorded
        decisions yields an empty tuple, never ``None``. This accessor never
        mutates: it performs a plain manager ``load`` (no rule, no save), so
        it can serve queries and audits (SFP-158/159) without participating
        in the transaction boundary.

        Args:
            ticket_id: the ticket whose recorded decision sequence to return.
        """
        aggregate = self._manager.load(ticket_id)
        if aggregate is None:
            return ()
        return aggregate.decisions
