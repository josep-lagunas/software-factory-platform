"""The deterministic readiness rubric (SFP-67 / SFP-232; ID-064 layer 1).

This module is the *logic* half of the readiness gate's layer 1 — the pure
:func:`evaluate_readiness_rubric` function that rule-checks a
:class:`~sfp_contracts.agents.readiness.ParsedTicket` for the eight ID-070
sections and emits a :class:`~sfp_contracts.agents.readiness.ReadinessOutput`.
It depends on the contract in :mod:`sfp_contracts.agents.readiness` (the
workspace-worker declares ``sfp-contracts`` as a dependency).

Grounded in:
- ID-064 (amended) — layer 1 of the Readiness evaluator is a *deterministic
  rubric*: it checks the ticket carries the mandatory ID-070 sections and says
  nothing semantic. Layer 2 (the model evaluator, SFP-52) is a separate concern
  that populates ``blocking_ambiguities`` with semantic gaps; the two layers
  compose into one ``ReadinessOutput``. This module is layer 1 only.
- ID-070 — the fixed list of ticket sections; the rubric's key set is exactly
  these names (deterministic, not a per-ticket judgment call).
- ID-065 — the routing verdict: ``READY`` proceeds, ``NEEDS_CLARIFICATION``
  routes back to the user. The rubric **never** emits ``MANUAL_REQUIRED`` — that
  is reserved for the SFP-52 evaluator; this function's verdict is always one of
  ``{READY, NEEDS_CLARIFICATION}``.
- SFP-232 — *calibration*: not all eight ID-070 sections are mandatory for every
  ticket. Six **core** sections are required ALWAYS (present AND non-empty). Two
  **boundary** sections (``context_outputs_required_inputs``, ``dependencies``)
  are conditional: they live at the human/automatic frontier and are required
  there as **presence only** (the header is declared; the body may be empty /
  ``""``). Off-frontier they are optional (may be absent). The uncalibrated
  rubric over-required these two sections and blocked the entire backlog (the
  SFP-101 dogfood showed 17 ready tickets failing only on them).

Design choice (mirrors :mod:`workspace_worker.workflow.failure`): the section
names are expressed as module-level ordered tuples
(:data:`_CORE_SECTIONS`, :data:`_BOUNDARY_SECTIONS`) — not an inline
``if``/``elif`` ladder — so the ID-070 list is auditable in one place and drives
both the evaluation loop and the fixed ``rubric_results`` key set (which keeps
all eight keys for a stable contract). The function itself is a pure rule
check; it performs no I/O and consults no model, so the same input always yields
the same output.

The ``at_frontier`` input (computed deterministically by
:mod:`workspace_worker.workflow.frontier`) is the *only* external input to the
core/boundary split — the rubric itself makes no manual/automatic judgment.
"""

from sfp_contracts.agents.readiness import (
    ParsedTicket,
    ReadinessOutput,
    ReadinessVerdict,
)

#: The six **core** ID-070 ticket sections, required ALWAYS (present AND
#: non-empty) for every ticket. These carry the implementation substance a
#: Planner needs regardless of who executes the ticket. The entries double as
#: :class:`~sfp_contracts.agents.readiness.ParsedTicket` field names.
_CORE_SECTIONS: tuple[str, ...] = (
    "context",
    "requirements",
    "files_to_create_modify",
    "implementation_notes",
    "references",
    "acceptance_criteria",
)

#: The two **boundary** ID-070 ticket sections, required as **presence only**
#: (the header is declared; the body may be empty / ``""``) **iff** the ticket
#: is at the human/automatic frontier (``at_frontier``). Off-frontier they are
#: optional. They describe inter-ticket edges (required inputs, upstream deps)
#: that only matter when a human hand-off is involved. The entries double as
#: :class:`~sfp_contracts.agents.readiness.ParsedTicket` field names.
_BOUNDARY_SECTIONS: tuple[str, ...] = (
    "context_outputs_required_inputs",
    "dependencies",
)

#: All eight ID-070 sections (core then boundary), in evaluation order. This is
#: the fixed ``rubric_results`` key set: the dict always carries all eight keys
#: for a stable contract, regardless of ``at_frontier``.
_REQUIRED_SECTIONS: tuple[str, ...] = _CORE_SECTIONS + _BOUNDARY_SECTIONS


def evaluate_readiness_rubric(
    ticket: ParsedTicket, *, ticket_id: str, at_frontier: bool = False
) -> ReadinessOutput:
    """Rule-check a :class:`ParsedTicket` for the ID-070 sections (SFP-232).

    Six **core** sections are required ALWAYS: a non-empty value (after
    :meth:`str.strip`) sets ``rubric_results[section] = True``; ``None`` or a
    whitespace-only value sets it ``False`` and appends
    ``"Missing required section: <section>"`` to ``blocking_ambiguities``.

    Two **boundary** sections (``context_outputs_required_inputs``,
    ``dependencies``) are conditional on ``at_frontier``:

    - **At the frontier** (``at_frontier=True``) they are required as
      **presence only**: ``None`` (the header was absent) sets the section
      ``False`` and appends ``"Missing required section (frontier): <section>"``;
      any non-``None`` value — including the present-but-empty ``""`` the parser
      emits for a declared-but-empty header — sets it ``True``. Presence is the
      requirement; the body may be empty.
    - **Off the frontier** (``at_frontier=False``, the default) they are
      optional and always pass (``True``), regardless of presence or content.

    All eight sections always appear in ``rubric_results`` (stable key set). The
    ``verdict`` is :attr:`~ReadinessVerdict.NEEDS_CLARIFICATION` if any section
    failed, else :attr:`~ReadinessVerdict.READY` — it is **never**
    :attr:`~ReadinessVerdict.MANUAL_REQUIRED` (reserved for the SFP-52 model
    evaluator). ``ticket_id`` is echoed and ``missing_inputs`` is always empty
    (this layer finds *missing sections*, not unresolved inputs).

    The function is pure and deterministic: no I/O, no model, and the same
    ``(ticket, ticket_id, at_frontier)`` always yields an equal
    :class:`ReadinessOutput`.

    Args:
        ticket: The parsed ticket whose ID-070 sections are rule-checked.
        ticket_id: The ticket identifier to echo into ``ReadinessOutput.ticket_id``.
        at_frontier: Whether this ticket sits at the human/automatic frontier
            (the ticket itself is 👤 manual, or any upstream dependency is). When
            ``True`` the two boundary sections are required as presence only;
            when ``False`` (the default) they are optional. Computed
            deterministically by :func:`workspace_worker.workflow.frontier.compute_at_frontier`.

    Returns:
        The deterministic :class:`ReadinessOutput` (layer 1 only).
    """
    rubric_results: dict[str, bool] = {}
    blocking_ambiguities: list[str] = []

    # Core sections: always required (present AND non-empty).
    for section in _CORE_SECTIONS:
        value: str | None = getattr(ticket, section)
        if value is None or value.strip() == "":
            rubric_results[section] = False
            blocking_ambiguities.append(f"Missing required section: {section}")
        else:
            rubric_results[section] = True

    # Boundary sections: required as presence only iff at_frontier; else optional.
    for section in _BOUNDARY_SECTIONS:
        b_value: str | None = getattr(ticket, section)
        if at_frontier:
            # Presence is the requirement: None (absent header) fails; any
            # non-None value — including "" (declared-but-empty header) — passes.
            if b_value is None:
                rubric_results[section] = False
                blocking_ambiguities.append(f"Missing required section (frontier): {section}")
            else:
                rubric_results[section] = True
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
