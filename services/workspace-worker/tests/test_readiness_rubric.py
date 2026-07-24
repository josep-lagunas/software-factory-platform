"""Tests for :func:`evaluate_readiness_rubric` (SFP-67; ID-064 layer 1).

Covers the acceptance criteria:
- (a) all-present ticket -> READY, every section True, empty blocking_ambiguities;
- (b) each section missing (None) in isolation -> that section False, a reason
  naming it, NEEDS_CLARIFICATION, all others True;
- (c) each whitespace-only section in isolation -> missing (same as None);
- (d) ticket_id is echoed into the output;
- (e) the verdict is never MANUAL_REQUIRED (always READY or NEEDS_CLARIFICATION);
- (f) rubric_results keys are exactly the fixed eight ID-070 section names;
- (g) determinism: the same inputs always yield an equal output.

The expected section set is encoded INDEPENDENTLY here (not imported from the
implementation's ``_REQUIRED_SECTIONS``) so the test is a genuine oracle.
"""

import pytest
from sfp_contracts.agents.readiness import (
    ParsedTicket,
    ReadinessOutput,
    ReadinessVerdict,
)
from workspace_worker.workflow.readiness_rubric import evaluate_readiness_rubric

#: Independent oracle: the eight mandatory ID-070 section names, encoded here
#: WITHOUT consulting the implementation's ``_REQUIRED_SECTIONS`` tuple.
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
SECTION_SET: set[str] = set(SECTIONS)

_TICKET_ID = "sfp-67-readiness-rubric"


def _full_ticket() -> ParsedTicket:
    """A ticket with every section non-empty (the READY baseline)."""
    return ParsedTicket(**{name: f"<{name} content>" for name in SECTIONS})


def _ticket_with(section: str, value: str | None) -> ParsedTicket:
    """A ticket with one section overridden; all others non-empty."""
    kwargs: dict[str, str | None] = {name: f"<{name} content>" for name in SECTIONS}
    kwargs[section] = value
    return ParsedTicket(**kwargs)


def test_section_oracle_covers_eight() -> None:
    """Guard: the oracle table is exactly the eight ID-070 sections."""
    assert len(SECTIONS) == 8
    assert len(SECTION_SET) == 8


def test_all_present_yields_ready_all_true() -> None:
    """(a) A fully-populated ticket is READY with every section True."""
    result = evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.READY
    assert all(result.rubric_results.values())
    assert set(result.rubric_results) == SECTION_SET
    assert result.blocking_ambiguities == []
    assert result.missing_inputs == []
    assert result.ticket_id == _TICKET_ID


@pytest.mark.parametrize("section", list(SECTIONS))
def test_missing_none_section_is_false_and_blocks(section: str) -> None:
    """(b) A ``None`` section is False, names itself in a reason, and blocks."""
    result = evaluate_readiness_rubric(_ticket_with(section, None), ticket_id=_TICKET_ID)

    assert isinstance(result, ReadinessOutput)
    assert result.ticket_id == _TICKET_ID
    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert result.missing_inputs == []
    # The failing section is the only False entry.
    assert result.rubric_results[section] is False
    assert [k for k, v in result.rubric_results.items() if not v] == [section]
    # Exactly one reason, and it names the section.
    assert len(result.blocking_ambiguities) == 1
    assert result.blocking_ambiguities == [f"Missing required section: {section}"]
    # All other sections remain True.
    for other in SECTIONS:
        if other != section:
            assert result.rubric_results[other] is True


@pytest.mark.parametrize("section", list(SECTIONS))
def test_whitespace_only_section_treated_as_missing(section: str) -> None:
    """(c) A whitespace-only section is missing, identical to ``None``."""
    result = evaluate_readiness_rubric(_ticket_with(section, "   \n\t  "), ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert result.rubric_results[section] is False
    assert [k for k, v in result.rubric_results.items() if not v] == [section]
    assert result.blocking_ambiguities == [f"Missing required section: {section}"]


@pytest.mark.parametrize("section", list(SECTIONS))
def test_punctuation_only_section_is_present(section: str) -> None:
    """R4 — non-empty punctuation-only content is NOT missing (not stripped to empty)."""
    result = evaluate_readiness_rubric(_ticket_with(section, "..."), ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.READY
    assert result.rubric_results[section] is True
    assert result.blocking_ambiguities == []


@pytest.mark.parametrize("section", list(SECTIONS))
def test_ticket_id_echoed(section: str) -> None:
    """(d) The ticket_id argument is echoed into ReadinessOutput.ticket_id."""
    tid = f"SFP-67#{section}"
    result = evaluate_readiness_rubric(_ticket_with(section, None), ticket_id=tid)
    assert result.ticket_id == tid


@pytest.mark.parametrize("section", list(SECTIONS))
def test_verdict_never_manual_required(section: str) -> None:
    """(e) The rubric never emits MANUAL_REQUIRED (reserved for SFP-52)."""
    result = evaluate_readiness_rubric(_ticket_with(section, None), ticket_id=_TICKET_ID)
    assert result.verdict in {ReadinessVerdict.READY, ReadinessVerdict.NEEDS_CLARIFICATION}
    assert result.verdict is not ReadinessVerdict.MANUAL_REQUIRED


def test_all_missing_yields_all_false_needs_clarification() -> None:
    """A bare (all-None) ticket fails every section with eight distinct reasons."""
    result = evaluate_readiness_rubric(ParsedTicket(), ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert not any(result.rubric_results.values())
    assert len(result.blocking_ambiguities) == 8
    # Eight distinct reasons, each naming a different section.
    assert len(set(result.blocking_ambiguities)) == 8
    assert set(result.rubric_results) == SECTION_SET


def test_rubric_results_keys_exactly_the_fixed_eight() -> None:
    """(f) rubric_results keys are set-equal to the eight ID-070 names (READY)."""
    result = evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID)
    assert set(result.rubric_results.keys()) == SECTION_SET
    assert len(result.rubric_results) == 8


def test_rubric_results_keys_fixed_even_when_missing() -> None:
    """(f) The fixed key set holds even when sections are missing."""
    result = evaluate_readiness_rubric(ParsedTicket(), ticket_id=_TICKET_ID)
    assert set(result.rubric_results.keys()) == SECTION_SET


def test_determinism_same_inputs_equal_output() -> None:
    """(g) Calling twice with the same inputs yields equal outputs (READY case)."""
    a = evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID)
    b = evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID)
    assert a == b
    assert a.to_json() == b.to_json()


def test_determinism_same_inputs_equal_output_blocked() -> None:
    """(g) Determinism for the NEEDS_CLARIFICATION case (ordering matters)."""
    a = evaluate_readiness_rubric(ParsedTicket(), ticket_id=_TICKET_ID)
    b = evaluate_readiness_rubric(ParsedTicket(), ticket_id=_TICKET_ID)
    assert a == b
    assert a.blocking_ambiguities == b.blocking_ambiguities
    # The eight reasons appear in canonical section order (deterministic).
    assert a.blocking_ambiguities == [f"Missing required section: {s}" for s in SECTIONS]


def test_blocking_ambiguity_reason_format() -> None:
    """The reason string format is exactly 'Missing required section: <name>'."""
    result = evaluate_readiness_rubric(_ticket_with("requirements", None), ticket_id=_TICKET_ID)
    assert result.blocking_ambiguities == ["Missing required section: requirements"]
