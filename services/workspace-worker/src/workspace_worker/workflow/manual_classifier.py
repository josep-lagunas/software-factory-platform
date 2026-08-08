"""The manual-required classifier (SFP-69 / DOC SFP-52; ID-065 v0).

This module is the *deterministic pre-filter* that runs BEFORE the model readiness
evaluator (:func:`evaluate_readiness`, SFP-68). It classifies a ticket whose
``manual`` (person) label is set as :attr:`~ReadinessVerdict.MANUAL_REQUIRED`,
short-circuiting the model run entirely. Unlabeled tickets defer to the model
evaluator, which retains its own model-driven MANUAL fallback.

Grounded in:
- ID-065 — ``MANUAL_REQUIRED`` escalates a ticket to a human; one of the three
  routing verdicts the Readiness evaluator can return.
- ID-065 v0 (binding, Orchestrator 2026-07-29) — the classifier does NOT
  keyword-detect; it takes an explicit ``is_manual: bool`` input. The composition
  root / caller reads the Jira ``manual`` label and passes it. Deterministic and
  consistent with ID-065.
- SFP-68 (binding, Orchestrator 2026-07-29) — placement is a deterministic
  pre-filter BEFORE :func:`evaluate_readiness`. The Orchestrator calls this
  classifier first; if it returns a non-``None`` :class:`ReadinessOutput`, that is
  the gate result; otherwise it calls :func:`evaluate_readiness`. SFP-68's
  model-MANUAL path remains as a fallback for unlabeled tickets the model judges
  manual.
- SFP-67 — the classifier STILL runs the deterministic layer-1 rubric
  (:func:`evaluate_readiness_rubric`) to populate ``rubric_results`` and surface
  any missing ID-070 sections, so the returned :class:`ReadinessOutput` is
  complete and the human sees why the ticket is blocked *in addition* to being
  manual.

Binding combination rule (all gate ambiguities pre-resolved):
- No contract change. :class:`ReadinessOutput` (SFP-18) is landed and has no
  ``manual_required_reason`` field; the manual reason is carried in
  ``blocking_ambiguities``. :attr:`~ReadinessVerdict.MANUAL_REQUIRED` is already a
  valid :class:`ReadinessVerdict`, so no enum change either.
- ``verdict`` is MANUAL_REQUIRED whenever ``is_manual`` is set — manual precedence
  wins over any missing ID-070 section (a human sees both signals).
- ``rubric_results`` <- layer-1 rubric pass-through ONLY.
- ``blocking_ambiguities`` <- the manual-required reason FIRST (decisive), then the
  rubric's missing-section messages.
- ``missing_inputs`` <- the rubric's value (always empty at this layer).
- ``ticket_id`` <- always the ``ticket_id`` argument.

Pure and deterministic: no I/O, no model call; equal inputs always yield an equal
result (or ``None``).
"""

from __future__ import annotations

from sfp_contracts.agents.readiness import (
    ParsedTicket,
    ReadinessOutput,
    ReadinessVerdict,
)

from workspace_worker.workflow.readiness_rubric import evaluate_readiness_rubric

__all__ = ["classify_manual", "MANUAL_REQUIRED_REASON"]

#: The canonical manual-required reason string (binding: the reason lives in
#: ``blocking_ambiguities``, not a dedicated field — SFP-18 has none). Exposed as
#: a module constant so callers/tests reference the exact text.
MANUAL_REQUIRED_REASON: str = "Manual-required: ticket is marked manual (ID-065)."


def classify_manual(
    ticket: ParsedTicket, *, is_manual: bool, ticket_id: str
) -> ReadinessOutput | None:
    """Classify a ticket as manual-required from its label (ID-065 v0).

    If ``is_manual`` is ``False``, return ``None`` — the caller defers to the
    model readiness evaluator (:func:`evaluate_readiness`, SFP-68). If
    ``is_manual`` is ``True``, run the deterministic layer-1 rubric
    (:func:`evaluate_readiness_rubric`, SFP-67) to populate ``rubric_results`` and
    surface any missing ID-070 sections, then return a :class:`ReadinessOutput`
    whose ``verdict`` is :attr:`~ReadinessVerdict.MANUAL_REQUIRED`.

    Note (SFP-232): a 👤-labeled ticket **is** the frontier by definition, so the
    rubric is invoked with ``at_frontier=True`` — its two *boundary* sections are
    required as presence only. (When ``is_manual`` is ``False`` this function
    returns ``None`` before the rubric is ever called, so no frontier computation
    is needed here.)

    Args:
        ticket: The parsed ticket whose ID-070 sections are rule-checked by the
            layer-1 rubric (its findings are surfaced to the human).
        is_manual: Whether the ticket carries the Jira ``manual`` (person) label.
            The caller reads the label and passes it; this function performs NO
            keyword detection (ID-065 v0, binding).
        ticket_id: The ticket identifier — always echoed into the result.

    Returns:
        ``None`` when ``is_manual`` is ``False`` (defer to the model evaluator);
        otherwise a MANUAL_REQUIRED :class:`ReadinessOutput` with the
        :data:`MANUAL_REQUIRED_REASON` leading ``blocking_ambiguities`` (followed
        by any rubric missing-section messages) and the rubric's
        ``rubric_results``/``missing_inputs`` passed through.
    """
    if not is_manual:
        return None

    # A 👤 ticket IS the frontier (SFP-232): the boundary sections are required
    # as presence only. (is_manual False already returned above.)
    rubric = evaluate_readiness_rubric(ticket, ticket_id=ticket_id, at_frontier=True)

    # Manual-required reason leads (the decisive classification); the rubric's
    # missing-section messages follow so a human sees any structural gaps too.
    blocking = [MANUAL_REQUIRED_REASON, *rubric.blocking_ambiguities]

    return ReadinessOutput(
        ticket_id=ticket_id,
        verdict=ReadinessVerdict.MANUAL_REQUIRED,
        blocking_ambiguities=blocking,
        missing_inputs=list(rubric.missing_inputs),
        rubric_results=dict(rubric.rubric_results),
    )
