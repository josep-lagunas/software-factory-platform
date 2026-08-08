"""Tests for :func:`evaluate_readiness_rubric` (SFP-67 / SFP-232; ID-064 layer 1).

Covers the *calibrated* acceptance criteria (SFP-232):
- 6 CORE sections required ALWAYS (present + non-empty), off- and on-frontier;
- 2 BOUNDARY sections (``context_outputs_required_inputs``, ``dependencies``):
  - at the frontier (``at_frontier=True``): absent (``None``) FAILS with a
    "(frontier)" reason; present-empty (``""``) PASSES; non-empty PASSES;
  - off the frontier (``at_frontier=False``, the default): always PASS, whether
    absent (``None``) or present-empty (``"");
- ``at_frontier`` defaults to ``False``;
- ``rubric_results`` always carries all 8 keys (stable contract);
- the verdict is never ``MANUAL_REQUIRED`` (always READY or NEEDS_CLARIFICATION);
- determinism.

The section sets are encoded INDEPENDENTLY here (not imported from the
implementation's ``_CORE_SECTIONS`` / ``_BOUNDARY_SECTIONS``) so the test is a
genuine oracle.
"""

import pytest
from sfp_contracts.agents.readiness import (
    ParsedTicket,
    ReadinessOutput,
    ReadinessVerdict,
)
from workspace_worker.workflow.readiness_rubric import evaluate_readiness_rubric

#: Independent oracle: the six CORE ID-070 section names (always required).
CORE_SECTIONS: tuple[str, ...] = (
    "context",
    "requirements",
    "files_to_create_modify",
    "implementation_notes",
    "references",
    "acceptance_criteria",
)

#: Independent oracle: the two BOUNDARY ID-070 section names (conditional).
BOUNDARY_SECTIONS: tuple[str, ...] = (
    "context_outputs_required_inputs",
    "dependencies",
)

#: All eight sections, core then boundary.
SECTIONS: tuple[str, ...] = CORE_SECTIONS + BOUNDARY_SECTIONS
SECTION_SET: set[str] = set(SECTIONS)

_TICKET_ID = "sfp-232-readiness-rubric"


def _full_ticket() -> ParsedTicket:
    """A ticket with every section non-empty (the READY baseline)."""
    return ParsedTicket(**{name: f"<{name} content>" for name in SECTIONS})


def _ticket_with(section: str, value: str | None) -> ParsedTicket:
    """A ticket with one section overridden; all others non-empty."""
    kwargs: dict[str, str | None] = {name: f"<{name} content>" for name in SECTIONS}
    kwargs[section] = value
    return ParsedTicket(**kwargs)


def _core_only_ticket() -> ParsedTicket:
    """A ticket with the 6 core sections non-empty and the 2 boundary ``None``.

    The READY baseline off-frontier (boundary optional) and a FAIL off/on-frontier
    only if a core section is missing.
    """
    kwargs: dict[str, str | None] = {name: f"<{name} content>" for name in CORE_SECTIONS}
    return ParsedTicket(**kwargs)


def test_section_oracle_covers_eight() -> None:
    """Guard: the oracle tables partition exactly the eight ID-070 sections."""
    assert len(SECTIONS) == 8
    assert len(SECTION_SET) == 8
    assert set(CORE_SECTIONS).isdisjoint(BOUNDARY_SECTIONS)
    assert SECTION_SET == set(CORE_SECTIONS) | set(BOUNDARY_SECTIONS)


# ---------------------------------------------------------------------------
# CORE sections: always required (present + non-empty)
# ---------------------------------------------------------------------------


def test_all_present_yields_ready_all_true_off_frontier() -> None:
    """A fully-populated ticket is READY off-frontier with every section True."""
    result = evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.READY
    assert all(result.rubric_results.values())
    assert set(result.rubric_results) == SECTION_SET
    assert result.blocking_ambiguities == []
    assert result.missing_inputs == []
    assert result.ticket_id == _TICKET_ID


def test_all_present_yields_ready_on_frontier() -> None:
    """A fully-populated ticket is READY on-frontier too."""
    result = evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID, at_frontier=True)

    assert result.verdict is ReadinessVerdict.READY
    assert all(result.rubric_results.values())
    assert result.blocking_ambiguities == []


@pytest.mark.parametrize("at_frontier", [False, True], ids=["off-frontier", "frontier"])
def test_core_missing_none_is_false_and_blocks(at_frontier: bool) -> None:
    """Each CORE section ``None`` -> False + 'Missing required section: X', both
    off- and on-frontier (core sections are ALWAYS required)."""
    for section in CORE_SECTIONS:
        result = evaluate_readiness_rubric(
            _ticket_with(section, None), ticket_id=_TICKET_ID, at_frontier=at_frontier
        )

        assert isinstance(result, ReadinessOutput)
        assert result.ticket_id == _TICKET_ID
        assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
        assert result.missing_inputs == []
        assert result.rubric_results[section] is False
        assert [k for k, v in result.rubric_results.items() if not v] == [section]
        assert len(result.blocking_ambiguities) == 1
        assert result.blocking_ambiguities == [f"Missing required section: {section}"]
        for other in SECTIONS:
            if other != section:
                assert result.rubric_results[other] is True


@pytest.mark.parametrize("section", list(CORE_SECTIONS))
def test_core_whitespace_only_treated_as_missing(section: str) -> None:
    """A whitespace-only CORE section is missing (same as None)."""
    result = evaluate_readiness_rubric(_ticket_with(section, "   \n\t  "), ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert result.rubric_results[section] is False
    assert result.blocking_ambiguities == [f"Missing required section: {section}"]


@pytest.mark.parametrize("section", list(CORE_SECTIONS))
def test_core_present_empty_string_treated_as_missing(section: str) -> None:
    """A present-but-empty (``""``) CORE section is still missing (core requires
    a non-empty body, even at the frontier)."""
    result = evaluate_readiness_rubric(
        _ticket_with(section, ""), ticket_id=_TICKET_ID, at_frontier=True
    )

    assert result.rubric_results[section] is False
    assert result.blocking_ambiguities == [f"Missing required section: {section}"]


# ---------------------------------------------------------------------------
# BOUNDARY sections: presence-only iff at_frontier; optional off-frontier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("section", list(BOUNDARY_SECTIONS))
def test_boundary_absent_passes_off_frontier(section: str) -> None:
    """Off-frontier: an absent (None) boundary section PASSES."""
    result = evaluate_readiness_rubric(
        _ticket_with(section, None), ticket_id=_TICKET_ID, at_frontier=False
    )

    assert result.verdict is ReadinessVerdict.READY
    assert result.rubric_results[section] is True
    assert result.blocking_ambiguities == []


@pytest.mark.parametrize("section", list(BOUNDARY_SECTIONS))
def test_boundary_present_empty_passes_off_frontier(section: str) -> None:
    """Off-frontier: a present-but-empty (``""``) boundary section PASSES."""
    result = evaluate_readiness_rubric(
        _ticket_with(section, ""), ticket_id=_TICKET_ID, at_frontier=False
    )

    assert result.verdict is ReadinessVerdict.READY
    assert result.rubric_results[section] is True


@pytest.mark.parametrize("section", list(BOUNDARY_SECTIONS))
def test_boundary_absent_fails_at_frontier(section: str) -> None:
    """At the frontier: an absent (None) boundary section FAILS with a
    '(frontier)' reason (presence is required)."""
    result = evaluate_readiness_rubric(
        _ticket_with(section, None), ticket_id=_TICKET_ID, at_frontier=True
    )

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert result.rubric_results[section] is False
    assert [k for k, v in result.rubric_results.items() if not v] == [section]
    assert result.blocking_ambiguities == [f"Missing required section (frontier): {section}"]


@pytest.mark.parametrize("section", list(BOUNDARY_SECTIONS))
def test_boundary_present_empty_passes_at_frontier(section: str) -> None:
    """At the frontier: a present-but-empty (``""``) boundary section PASSES —
    presence is the requirement, the body may be empty."""
    result = evaluate_readiness_rubric(
        _ticket_with(section, ""), ticket_id=_TICKET_ID, at_frontier=True
    )

    assert result.verdict is ReadinessVerdict.READY
    assert result.rubric_results[section] is True
    assert result.blocking_ambiguities == []


@pytest.mark.parametrize("section", list(BOUNDARY_SECTIONS))
def test_boundary_non_empty_passes_at_frontier(section: str) -> None:
    """At the frontier: a non-empty boundary section PASSES."""
    result = evaluate_readiness_rubric(
        _ticket_with(section, "<boundary body>"), ticket_id=_TICKET_ID, at_frontier=True
    )

    assert result.verdict is ReadinessVerdict.READY
    assert result.rubric_results[section] is True


@pytest.mark.parametrize("section", list(BOUNDARY_SECTIONS))
def test_boundary_whitespace_treated_as_present_at_frontier(section: str) -> None:
    """At the frontier: a whitespace-only boundary body is present-but-empty
    (the parser would yield ``""``); the rubric only checks presence -> PASS.

    A direct whitespace value is NOT ``None`` so it counts as present; the
    presence-only check passes.
    """
    result = evaluate_readiness_rubric(
        _ticket_with(section, "   "), ticket_id=_TICKET_ID, at_frontier=True
    )

    assert result.rubric_results[section] is True
    assert result.verdict is ReadinessVerdict.READY


# ---------------------------------------------------------------------------
# at_frontier default + contract stability
# ---------------------------------------------------------------------------


def test_at_frontier_defaults_to_false() -> None:
    """The default frontier mode is off (boundary sections optional)."""
    # A ticket with absent boundary sections is READY under the default.
    result = evaluate_readiness_rubric(_core_only_ticket(), ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.READY
    assert all(result.rubric_results.values())  # boundary True (optional)


def test_core_only_ticket_blocks_at_frontier() -> None:
    """A core-only ticket (boundary absent) FAILS at the frontier."""
    result = evaluate_readiness_rubric(_core_only_ticket(), ticket_id=_TICKET_ID, at_frontier=True)

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    # Both boundary sections fail (absent at the frontier).
    failed = [k for k, v in result.rubric_results.items() if not v]
    assert set(failed) == set(BOUNDARY_SECTIONS)
    assert all(
        f"Missing required section (frontier): {s}" in result.blocking_ambiguities
        for s in BOUNDARY_SECTIONS
    )


@pytest.mark.parametrize("section", list(SECTIONS))
@pytest.mark.parametrize("at_frontier", [False, True])
def test_verdict_never_manual_required(section: str, at_frontier: bool) -> None:
    """The rubric never emits MANUAL_REQUIRED (reserved for SFP-52)."""
    result = evaluate_readiness_rubric(
        _ticket_with(section, None), ticket_id=_TICKET_ID, at_frontier=at_frontier
    )
    assert result.verdict in {ReadinessVerdict.READY, ReadinessVerdict.NEEDS_CLARIFICATION}
    assert result.verdict is not ReadinessVerdict.MANUAL_REQUIRED


def test_rubric_results_keys_always_the_fixed_eight() -> None:
    """rubric_results keys are set-equal to the eight ID-070 names (READY)."""
    result = evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID)
    assert set(result.rubric_results.keys()) == SECTION_SET
    assert len(result.rubric_results) == 8


def test_rubric_results_keys_fixed_even_when_core_missing() -> None:
    """The fixed key set holds even when sections are missing."""
    result = evaluate_readiness_rubric(ParsedTicket(), ticket_id=_TICKET_ID)
    assert set(result.rubric_results.keys()) == SECTION_SET


def test_ticket_id_echoed() -> None:
    """The ticket_id argument is echoed into ReadinessOutput.ticket_id."""
    tid = "SFP-232#echo"
    result = evaluate_readiness_rubric(_full_ticket(), ticket_id=tid)
    assert result.ticket_id == tid


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_determinism_same_inputs_equal_output_ready() -> None:
    """Calling twice with the same inputs yields equal outputs (READY case)."""
    a = evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID, at_frontier=True)
    b = evaluate_readiness_rubric(_full_ticket(), ticket_id=_TICKET_ID, at_frontier=True)
    assert a == b
    assert a.to_json() == b.to_json()


def test_determinism_same_inputs_equal_output_blocked() -> None:
    """Determinism for the NEEDS_CLARIFICATION case (ordering matters)."""
    a = evaluate_readiness_rubric(ParsedTicket(), ticket_id=_TICKET_ID, at_frontier=True)
    b = evaluate_readiness_rubric(ParsedTicket(), ticket_id=_TICKET_ID, at_frontier=True)
    assert a == b
    assert a.blocking_ambiguities == b.blocking_ambiguities
    # 6 core (always fail) + 2 boundary (fail at frontier) = 8 reasons, in order.
    expected = [f"Missing required section: {s}" for s in CORE_SECTIONS] + [
        f"Missing required section (frontier): {s}" for s in BOUNDARY_SECTIONS
    ]
    assert a.blocking_ambiguities == expected


def test_blocking_ambiguity_reason_formats() -> None:
    """Both reason string formats are exactly as specified."""
    core_result = evaluate_readiness_rubric(
        _ticket_with("requirements", None), ticket_id=_TICKET_ID
    )
    assert core_result.blocking_ambiguities == ["Missing required section: requirements"]

    frontier_result = evaluate_readiness_rubric(
        _ticket_with("dependencies", None), ticket_id=_TICKET_ID, at_frontier=True
    )
    assert frontier_result.blocking_ambiguities == [
        "Missing required section (frontier): dependencies"
    ]
