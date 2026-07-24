"""The deterministic readiness rubric (SFP-67; ID-064 layer 1).

This module is the *logic* half of the readiness gate's layer 1 — the pure
:func:`evaluate_readiness_rubric` function that rule-checks a
:class:`~sfp_contracts.agents.readiness.ParsedTicket` for the eight mandatory
ID-070 sections and emits a :class:`~sfp_contracts.agents.readiness.ReadinessOutput`.
It depends on the contract in :mod:`sfp_contracts.agents.readiness` (the
workspace-worker declares ``sfp-contracts`` as a dependency).

Grounded in:
- ID-064 (amended) — layer 1 of the Readiness evaluator is a *deterministic
  rubric*: it checks the ticket carries the mandatory ID-070 sections and says
  nothing semantic. Layer 2 (the model evaluator, SFP-52) is a separate concern
  that populates ``blocking_ambiguities`` with semantic gaps; the two layers
  compose into one ``ReadinessOutput``. This module is layer 1 only.
- ID-070 — the fixed list of mandatory ticket sections; the rubric's key set is
  exactly these names (deterministic, not a per-ticket judgment call).
- ID-065 — the routing verdict: ``READY`` proceeds, ``NEEDS_CLARIFICATION``
  routes back to the user. The rubric **never** emits ``MANUAL_REQUIRED`` — that
  is reserved for the SFP-52 evaluator; this function's verdict is always one of
  ``{READY, NEEDS_CLARIFICATION}``.

Design choice (mirrors :mod:`workspace_worker.workflow.failure`): the section
names are expressed as a module-level ordered tuple (``_REQUIRED_SECTIONS``) —
not an inline ``if``/``elif`` ladder — so the ID-070 list is auditable in one
place and drives both the evaluation loop and the fixed ``rubric_results`` key
set. The function itself is a pure rule check; it performs no I/O and consults
no model, so the same input always yields the same output.
"""

from sfp_contracts.agents.readiness import (
    ParsedTicket,
    ReadinessOutput,
    ReadinessVerdict,
)

#: The eight mandatory ID-070 ticket sections, in canonical order. This tuple
#: drives both the evaluation loop and the fixed ``rubric_results`` key set, so
#: the section list lives in exactly one auditable place. The entries double as
#: :class:`~sfp_contracts.agents.readiness.ParsedTicket` field names.
_REQUIRED_SECTIONS: tuple[str, ...] = (
    "context",
    "requirements",
    "files_to_create_modify",
    "implementation_notes",
    "references",
    "context_outputs_required_inputs",
    "acceptance_criteria",
    "dependencies",
)


def evaluate_readiness_rubric(ticket: ParsedTicket, *, ticket_id: str) -> ReadinessOutput:
    """Rule-check a :class:`ParsedTicket` for the eight mandatory ID-070 sections.

    For each section in :data:`_REQUIRED_SECTIONS`: a non-empty value (after
    :meth:`str.strip`) sets ``rubric_results[section] = True``; ``None`` or a
    whitespace-only value sets it ``False`` and appends
    ``"Missing required section: <section>"`` to ``blocking_ambiguities``. The
    ``verdict`` is :attr:`~ReadinessVerdict.NEEDS_CLARIFICATION` if any section
    failed, else :attr:`~ReadinessVerdict.READY` — it is **never**
    :attr:`~ReadinessVerdict.MANUAL_REQUIRED` (reserved for the SFP-52 model
    evaluator). ``ticket_id`` is echoed and ``missing_inputs`` is always empty
    (this layer finds *missing sections*, not unresolved inputs).

    The function is pure and deterministic: no I/O, no model, and the same
    ``(ticket, ticket_id)`` always yields an equal :class:`ReadinessOutput`.

    Args:
        ticket: The parsed ticket whose ID-070 sections are rule-checked.
        ticket_id: The ticket identifier to echo into ``ReadinessOutput.ticket_id``.

    Returns:
        The deterministic :class:`ReadinessOutput` (layer 1 only).
    """
    rubric_results: dict[str, bool] = {}
    blocking_ambiguities: list[str] = []

    for section in _REQUIRED_SECTIONS:
        value: str | None = getattr(ticket, section)
        if value is None or value.strip() == "":
            rubric_results[section] = False
            blocking_ambiguities.append(f"Missing required section: {section}")
        else:
            rubric_results[section] = True

    verdict = (
        ReadinessVerdict.NEEDS_CLARIFICATION
        if any(not passed for passed in rubric_results.values())
        else ReadinessVerdict.READY
    )

    return ReadinessOutput(
        ticket_id=ticket_id,
        verdict=verdict,
        blocking_ambiguities=blocking_ambiguities,
        missing_inputs=[],
        rubric_results=rubric_results,
    )
