"""Tests for :func:`classify_manual` (SFP-69 / DOC SFP-52; ID-065 v0).

Covers the binding decisions (Orchestrator 2026-07-29):
- ``is_manual=False`` -> ``None`` (defer to the SFP-68 model evaluator).
- ``is_manual=True`` -> a MANUAL_REQUIRED :class:`ReadinessOutput` whose
  ``blocking_ambiguities`` lead with :data:`MANUAL_REQUIRED_REASON` and include
  any layer-1 rubric missing-section messages; ``rubric_results`` pass-through;
  ``ticket_id`` echoed.
- Manual precedence: a missing ID-070 section still yields MANUAL_REQUIRED (not
  NEEDS_CLARIFICATION), and the human sees both the manual reason AND the rubric
  finding.
- Determinism.
"""

from sfp_contracts.agents.readiness import (
    ParsedTicket,
    ReadinessVerdict,
)
from workspace_worker.workflow.manual_classifier import (
    MANUAL_REQUIRED_REASON,
    classify_manual,
)
from workspace_worker.workflow.readiness_rubric import evaluate_readiness_rubric

SECTIONS: tuple[str, ...] = (
    "context",
    "requirements",
    "files_to_create_modify",
    "implementation_notes",
    "references",
    "context_outputs_required_inputs",
    "acceptance_criteria",
    "dependencies",
)

_TICKET_ID = "sfp-69-manual-classifier"


def _full_ticket() -> ParsedTicket:
    return ParsedTicket(**{name: f"<{name} content>" for name in SECTIONS})


def _ticket_with(section: str, value: str | None) -> ParsedTicket:
    kwargs: dict[str, str | None] = {name: f"<{name} content>" for name in SECTIONS}
    kwargs[section] = value
    return ParsedTicket(**kwargs)


def test_not_manual_returns_none() -> None:
    """``is_manual=False`` defers to the model evaluator (returns None)."""
    assert classify_manual(_full_ticket(), is_manual=False, ticket_id=_TICKET_ID) is None


def test_manual_full_ticket_is_manual_required() -> None:
    """A labeled, fully-populated ticket is MANUAL_REQUIRED."""
    result = classify_manual(_full_ticket(), is_manual=True, ticket_id=_TICKET_ID)

    assert result is not None
    assert result.verdict is ReadinessVerdict.MANUAL_REQUIRED
    assert result.ticket_id == _TICKET_ID
    # The manual reason leads blocking_ambiguities; a clean ticket adds nothing else.
    assert result.blocking_ambiguities == [MANUAL_REQUIRED_REASON]
    # rubric_results are the layer-1 pass-through (all True for a full ticket).
    assert (
        result.rubric_results
        == evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID).rubric_results
    )
    assert all(result.rubric_results.values())
    assert result.missing_inputs == []


def test_manual_reason_leads_even_with_missing_section() -> None:
    """Manual precedence wins; the rubric finding is still surfaced to the human."""
    ticket = _ticket_with("requirements", None)
    result = classify_manual(ticket, is_manual=True, ticket_id=_TICKET_ID)

    assert result is not None
    assert result.verdict is ReadinessVerdict.MANUAL_REQUIRED  # NOT NEEDS_CLARIFICATION
    # The manual reason leads; the rubric's missing-section message follows.
    assert result.blocking_ambiguities[0] == MANUAL_REQUIRED_REASON
    assert len(result.blocking_ambiguities) == 2
    assert "requirements" in result.blocking_ambiguities[1]
    # The rubric did run and flagged the section.
    assert result.rubric_results["requirements"] is False
    assert all(v for k, v in result.rubric_results.items() if k != "requirements")


def test_ticket_id_always_echoed() -> None:
    """The gate arg wins (no input-driven id)."""
    result = classify_manual(_full_ticket(), is_manual=True, ticket_id="manual-xyz")
    assert result is not None
    assert result.ticket_id == "manual-xyz"


def test_manual_is_pure_and_deterministic() -> None:
    """Equal inputs yield equal outputs (MAS §12.7)."""
    a = classify_manual(_full_ticket(), is_manual=True, ticket_id=_TICKET_ID)
    b = classify_manual(_full_ticket(), is_manual=True, ticket_id=_TICKET_ID)
    assert a == b
    assert a is not None
    # Two calls with identical inputs produce equal, independently-built results.
    assert a.blocking_ambiguities == b.blocking_ambiguities


def test_not_manual_is_deterministic_none() -> None:
    """Repeated False calls consistently defer."""
    for _ in range(3):
        assert classify_manual(_full_ticket(), is_manual=False, ticket_id=_TICKET_ID) is None
