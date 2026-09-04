"""Read-model queries over the per-ticket decision log (MAS §5.12, SFP-158+159).

The query surface of the platform: pure, read-only projections of a ticket's
workflow history. Two altitudes, one data source:

- :class:`WorkflowContextQuery` (SFP-158) answers "where is this ticket's
  workflow and what recently happened to it?" in full detail — the bounded
  recent tail included — for consumers (status messages, context handoffs).
- :class:`TicketSummaryQuery` (SFP-159) answers the same question in one
  compact row — state, decision count, last reason and transition position —
  for listings, and :class:`ProjectQuery` (SFP-159) fans that summary out
  over a **caller-supplied** ticket membership to answer "where is this
  project's work?" without the platform owning any project aggregate.

All of it **without** giving any consumer a way to move the workflow.

Grounded in:

- MAS §5.12 — the query side reads the decision log; the view is *exactly*
  what the log says. No write, no re-derivation, no join with live runtime
  state: the current state **is** the last decision's ``resulting_state``,
  because every state change is recorded as a decision (MAS §8.5/§8.7).
- SFP-148 — the accessor :meth:`~orchestrator.application.decision_recorder.
  DecisionRecorder.decisions_for` is the only data source. Its ordering is
  append order (oldest first, newest last — the SFP-148 rule appends each
  decision in call order), so the *latest* decision is ``decisions[-1]`` and
  the recent tail is ``decisions[-limit:]``.
- SFP-158 — the reader seam is a Protocol mirroring that accessor's shape,
  so any reader with the same single read method structurally satisfies it.
  SFP-159 adds no new data source and no new seam: it reuses the same reader
  port and the same tail-limit discipline.
- AP-011 — every query here is a pure function of the decisions handed over
  by the reader: same decisions in, equal view out. No clock, randomness, or
  I/O of its own. Computed per call, never cached (the event-driven
  read-model projection belongs to the future control-plane UI, deferred).

Explicit emptiness, never a placeholder: an unknown ticket yields
``state_known=False`` with ``current_state=None`` — the model carries a
*boolean* emptiness signal rather than a fake state string, so a caller can
never mistake "never seen" for a real state. In the project fan-out the same
signal keeps an unknown member from ever aborting the projection: partial
membership projects partial data with unknowns, not an error.

The transition position. The landed :class:`WorkflowDecision` carries no
sequence field of its own (verified against the landed SFP-137/148 code —
its §8.5 field set is states/reason/policy/facts/changes/commands only, and
it deliberately carries no clock). The only ordering ground truth is the
reader's append order, which is exactly what the SFP-148 aggregate preserves
(one ``version`` bump per append). ``last_transition_at_sequence`` therefore
projects the **0-based append position of the last decision** — ``len-1`` of
the sequence the reader returned. It is a stable, deterministic log
position, not a timestamp (AP-011) and not an invented store field.

Transport/rendering is out of scope (SFP-159 scope: the views only). This
module is pure read-model: no HTTP, no formatting.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.domain.workflow.state_machine import WorkflowDecision
from orchestrator.domain.workflow.states import WorkflowState

__all__ = [
    "DEFAULT_RECENT_DECISIONS_LIMIT",
    "ProjectQuery",
    "ProjectView",
    "TicketSummaryQuery",
    "TicketSummaryView",
    "UNKNOWN_TICKET_STATE_KEY",
    "WorkflowContextQuery",
    "WorkflowContextView",
    "WorkflowDecisionReader",
]

#: Bound on the recent-decision tail when the caller passes no explicit
#: ``limit`` (SFP-158). Views stay small by construction, never unbounded.
DEFAULT_RECENT_DECISIONS_LIMIT = 10


class WorkflowDecisionReader(Protocol):
    """Seam: read-only access to a ticket's recorded decision sequence.

    Mirrors the single read method of SFP-148's accessor —
    :meth:`~orchestrator.application.decision_recorder.DecisionRecorder.
    decisions_for` — and nothing else. Structural (Protocol) so the concrete
    recorder satisfies it without inheritance; deliberately **read-only**:
    a conforming object exposes no way to mutate the decision store.
    """

    def decisions_for(self, ticket_id: str) -> Sequence[WorkflowDecision]:
        """Return the ticket's decisions in append order (oldest first).

        A ticket with no recorded decisions yields an empty sequence, never
        ``None`` (the SFP-148 contract this query relies on).
        """
        ...  # pragma: no cover


class WorkflowContextView(BaseModel):
    """A frozen, self-contained view of one ticket's workflow context.

    Pure projection of the decision log (MAS §5.12): every field is either
    derived from the recorded decisions or the explicit emptiness signal —
    the query never invents, re-derives, or joins in live runtime state.

    - ``current_state`` — the **last** decision's ``resulting_state``; the
      log's final word on where the workflow is.
    - ``state_known`` — the explicit emptiness signal: ``False`` iff the log
      is empty. Never encode "unknown" as a fake state string.
    - ``last_decision`` — the full latest decision (``None`` iff empty).
    - ``decision_count`` — total decisions in the log, independent of the
      recent-tail bound.
    - ``recent_decisions`` — the bounded tail (append order preserved), for
      context handoffs and audits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str
    current_state: WorkflowState | None
    state_known: bool
    last_decision: WorkflowDecision | None
    decision_count: int = Field(ge=0)
    recent_decisions: tuple[WorkflowDecision, ...] = ()


class WorkflowContextQuery:
    """Build :class:`WorkflowContextView`s from a decision-log reader.

    Constructor-injected seams:

    - ``reader`` — any :class:`WorkflowDecisionReader` (structurally, the
      SFP-148 accessor). The query calls it **once** per ``retrieve`` and
      reads nothing else: the store is never written through this object.
    - ``limit`` — the bound on ``recent_decisions`` (must be > 0; default
      :data:`DEFAULT_RECENT_DECISIONS_LIMIT`). Applies only to the tail —
      ``decision_count`` always reflects the full log.

    Deterministic and pure (AP-011): the same decision log yields an equal
    view on every call.
    """

    def __init__(
        self,
        reader: WorkflowDecisionReader,
        limit: int = DEFAULT_RECENT_DECISIONS_LIMIT,
    ) -> None:
        """Wire the reader and validate the tail bound.

        Raises:
            ValueError: if ``limit`` is not a positive integer.
        """
        if limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit}")
        self._reader = reader
        self._limit = limit

    @property
    def limit(self) -> int:
        """The recent-decision tail bound this query was built with."""
        return self._limit

    def retrieve(self, ticket_id: str) -> WorkflowContextView:
        """Project one ticket's decision log into a :class:`WorkflowContextView`.

        One read, zero writes: the reader is consulted exactly once and the
        result is shaped into the view. Empty log → ``state_known=False``,
        ``current_state=None``, ``last_decision=None``, count 0, empty tail
        — an explicit shape, never a fake state string.

        Args:
            ticket_id: the ticket whose workflow context to view.

        Returns:
            The frozen view. ``current_state``/``last_decision`` come from
            the **last** decision (the reader's append order is oldest
            first); ``recent_decisions`` is the final ``limit`` decisions in
            order; ``decision_count`` is the total.
        """
        decisions = self._reader.decisions_for(ticket_id)
        if not decisions:
            return WorkflowContextView(
                ticket_id=ticket_id,
                current_state=None,
                state_known=False,
                last_decision=None,
                decision_count=0,
                recent_decisions=(),
            )
        last = decisions[-1]
        return WorkflowContextView(
            ticket_id=ticket_id,
            current_state=last.resulting_state,
            state_known=True,
            last_decision=last,
            decision_count=len(decisions),
            recent_decisions=tuple(decisions[-self._limit :]),
        )


#: Bucket under which an unknown ticket's absence is counted in a
#: :class:`ProjectView.tickets_by_state`. It is a *counting* label for the
#: explicit emptiness signal — never presented as, or convertible to, a real
#: :class:`~orchestrator.domain.workflow.states.WorkflowState`.
UNKNOWN_TICKET_STATE_KEY = "UNKNOWN"


class TicketSummaryView(BaseModel):
    """A frozen, one-row summary of one ticket's workflow position.

    The compact counterpart to :class:`WorkflowContextView` (SFP-159):
    everything a listing needs and nothing it does not — no decision bodies,
    no recent tail. Every field is either derived from the recorded decisions
    or the explicit emptiness signal; nothing is invented.

    - ``current_state`` — the **last** decision's ``resulting_state`` (``None``
      iff the log is empty); the log's final word, never re-derived.
    - ``state_known`` — the explicit emptiness signal: ``False`` iff the log
      is empty. Never encode "unknown" as a fake state string.
    - ``decision_count`` — total decisions in the log (see
      ``decision_count_semantics`` below).
    - ``last_reason`` — the last decision's ``reason`` (``""`` iff empty).
    - ``last_transition_at_sequence`` — the 0-based append position of the
      last decision in the reader's sequence (``-1`` iff the log is empty),
      i.e. ``len(decisions) - 1``. The landed decision record carries no
      sequence field; append order is the only, and a stable, ground truth.

    ``decision_count`` semantics: the count is the number of records the
    reader handed over for the ticket. Under SFP-148's reader this is the
    full log; the tail-limit discipline bounds only what a *context* view
    materialises, not how many records a summary counts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str
    current_state: WorkflowState | None
    state_known: bool
    decision_count: int = Field(ge=0)
    last_reason: str
    last_transition_at_sequence: int = Field(ge=-1)


class TicketSummaryQuery:
    """Build :class:`TicketSummaryView`s from a decision-log reader.

    The same reader port, the same discipline, a smaller row (SFP-159):
    constructor-injected seams mirror :class:`WorkflowContextQuery` exactly
    and the underlying reader is consulted exactly once per ``retrieve``.
    The ``limit`` exists to keep the *bounded-tail* discipline uniform across
    the query family — a summary materialises no tail, but the bound still
    validates the same way and the same default applies.

    Deterministic and pure (AP-011): the same decision log yields an equal
    view on every call.
    """

    def __init__(
        self,
        reader: WorkflowDecisionReader,
        limit: int = DEFAULT_RECENT_DECISIONS_LIMIT,
    ) -> None:
        """Wire the reader and validate the tail bound.

        Raises:
            ValueError: if ``limit`` is not a positive integer.
        """
        if limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit}")
        self._reader = reader
        self._limit = limit

    @property
    def limit(self) -> int:
        """The recent-decision tail bound this query was built with."""
        return self._limit

    def retrieve(self, ticket_id: str) -> TicketSummaryView:
        """Project one ticket's decision log into a :class:`TicketSummaryView`.

        One read, zero writes: the reader is consulted exactly once. Empty
        log → ``state_known=False``, ``current_state=None``, count 0,
        ``last_reason=""``, ``last_transition_at_sequence=-1`` — an explicit
        shape, never a fake state string.

        Args:
            ticket_id: the ticket whose workflow summary to view.

        Returns:
            The frozen summary row. ``current_state``/``last_reason`` come
            from the **last** decision (the reader's append order is oldest
            first); ``last_transition_at_sequence`` is that decision's 0-based
            append position.
        """
        decisions = self._reader.decisions_for(ticket_id)
        if not decisions:
            return TicketSummaryView(
                ticket_id=ticket_id,
                current_state=None,
                state_known=False,
                decision_count=0,
                last_reason="",
                last_transition_at_sequence=-1,
            )
        last = decisions[-1]
        return TicketSummaryView(
            ticket_id=ticket_id,
            current_state=last.resulting_state,
            state_known=True,
            decision_count=len(decisions),
            last_reason=last.reason,
            last_transition_at_sequence=len(decisions) - 1,
        )


class ProjectView(BaseModel):
    """A frozen, one-row-per-ticket snapshot of a project's workflow state.

    Pure fan-out (SFP-159): the caller supplies the project's ticket
    membership (the identity service owns projects; this platform does not),
    and the view is exactly each member's :class:`TicketSummaryView` plus a
    state histogram. No project aggregate is read, joined, or invented.

    - ``project_id`` — the caller's identifier, carried verbatim.
    - ``ticket_summaries`` — one summary per requested ticket, **in the
      caller's order**, unknown members included (``state_known=False``).
    - ``tickets_by_state`` — counts keyed by state string. Known tickets
      count under their ``current_state``'s name; every unknown member
      counts once under :data:`UNKNOWN_TICKET_STATE_KEY`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    ticket_summaries: tuple[TicketSummaryView, ...] = ()
    tickets_by_state: dict[str, int] = Field(default_factory=dict)


class ProjectQuery:
    """Build :class:`ProjectView`s by fanning :class:`TicketSummaryQuery` out.

    The membership question is the caller's (identity owns projects); the
    workflow question is ours (the decision log). This query joins exactly
    those two facts per call and owns no aggregation state of its own —
    the same membership yields an equal view on every call (AP-011).

    Graceful by contract: an unknown member is not an error. The reader's
    not-found signal (the empty sequence, per the SFP-148/158 contract)
    projects as a ``state_known=False`` summary, so a partially-known
    membership still yields a complete, honest view. No exception is raised
    mid-projection for unknown data.
    """

    def __init__(
        self,
        reader: WorkflowDecisionReader,
        limit: int = DEFAULT_RECENT_DECISIONS_LIMIT,
    ) -> None:
        """Wire the shared reader; validate the bound once for the fan-out.

        Args:
            reader: the :class:`WorkflowDecisionReader` every member's
                summary is projected through (one read per member).
            limit: the tail bound forwarded to the per-ticket summary query
                (same default, same validation).

        Raises:
            ValueError: if ``limit`` is not a positive integer.
        """
        self._summary_query = TicketSummaryQuery(reader, limit=limit)

    @property
    def limit(self) -> int:
        """The recent-decision tail bound this query forwards."""
        return self._summary_query.limit

    def retrieve(self, project_id: str, ticket_ids: Sequence[str]) -> ProjectView:
        """Fan out over ``ticket_ids`` into one :class:`ProjectView`.

        Args:
            project_id: the caller's project identifier, carried verbatim.
            ticket_ids: the project's ticket membership, in display order.
                The caller owns this list (identity's data); an empty
                sequence is a valid, empty project — not an error.

        Returns:
            The frozen project view. Each member appears exactly once, in
            the given order; unknown members appear with ``state_known=False``
            and are counted under :data:`UNKNOWN_TICKET_STATE_KEY`.
        """
        summaries = tuple(self._summary_query.retrieve(ticket_id) for ticket_id in ticket_ids)
        tickets_by_state: dict[str, int] = {}
        for summary in summaries:
            key = (
                summary.current_state.name
                if summary.current_state is not None
                else UNKNOWN_TICKET_STATE_KEY
            )
            tickets_by_state[key] = tickets_by_state.get(key, 0) + 1
        return ProjectView(
            project_id=project_id,
            ticket_summaries=summaries,
            tickets_by_state=tickets_by_state,
        )
