"""Read-model queries over the per-ticket decision log (MAS §5.12, SFP-158).

The first query surface of the platform: a pure, read-only projection of a
ticket's workflow history into a :class:`WorkflowContextView`. It answers
"where is this ticket's workflow and what recently happened to it?" for
consumers (status messages, context handoffs) **without** giving them any way
to move the workflow.

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
- AP-011 — the query is a pure function of the decisions handed over by the
  reader: same decisions in, equal view out. No clock, randomness, or I/O of
  its own.

Explicit emptiness, never a placeholder: an unknown ticket yields
``state_known=False`` with ``current_state=None`` — the model carries a
*boolean* emptiness signal rather than a fake state string, so a caller can
never mistake "never seen" for a real state.

Transport/rendering is out of scope (SFP-159 siblings: summary/project
views). This module is pure read-model: no HTTP, no formatting.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.domain.workflow.state_machine import WorkflowDecision
from orchestrator.domain.workflow.states import WorkflowState

__all__ = [
    "DEFAULT_RECENT_DECISIONS_LIMIT",
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
