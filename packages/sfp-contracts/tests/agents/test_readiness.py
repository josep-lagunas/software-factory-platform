"""Tests for the :class:`ReadinessOutput` schema (SFP-35).

Covers the acceptance criteria:
- (a) conformant payload round-trips through ``to_json``/``from_json``;
- (b) extra fields rejected on construction AND ``from_json`` (extra='forbid');
- (c) every missing required field raises ``ValidationError``;
- (d) all three ``ReadinessVerdict`` members are accepted (parametrized);
- (e) verdict string values equal the enum member names exactly;
- (f) malformed JSON is rejected;
- (g) ``rubric_results`` is a ``dict[str, bool]`` (non-boolean values rejected).
"""

from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.agents.readiness import ReadinessOutput, ReadinessVerdict

VALID_KWARGS: dict[str, Any] = {
    "ticket_id": "sfp-35-readiness-schema",
    "verdict": ReadinessVerdict.READY,
    "blocking_ambiguities": [],
    "missing_inputs": [],
    "rubric_results": {
        "acceptance_criteria_present": True,
        "dependencies_resolved": True,
        "scope_unambiguous": False,
    },
}

REQUIRED_FIELDS = list(VALID_KWARGS.keys())


def make_output(**overrides: Any) -> ReadinessOutput:
    kwargs = dict(VALID_KWARGS)
    kwargs.update(overrides)
    return ReadinessOutput(**kwargs)


def test_round_trip_preserves_every_field() -> None:
    """(a) A conformant ReadinessOutput round-trips through JSON losslessly."""
    original = make_output()
    restored = ReadinessOutput.from_json(original.to_json())

    assert restored == original
    assert restored.ticket_id == "sfp-35-readiness-schema"
    assert restored.verdict is ReadinessVerdict.READY
    assert restored.blocking_ambiguities == []
    assert restored.missing_inputs == []
    assert restored.rubric_results == {
        "acceptance_criteria_present": True,
        "dependencies_resolved": True,
        "scope_unambiguous": False,
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"comments": ["x"]},
        {"confidence": 0.9},
        {"escalation_reason": "needs human"},
        {"unexpected": "x"},
    ],
)
def test_extra_fields_rejected_on_construction(extra: dict[str, Any]) -> None:
    """(b) Extra fields are rejected at construction."""
    with pytest.raises(ValidationError):
        make_output(**extra)


def test_extra_fields_rejected_on_from_json() -> None:
    """(b) Extra fields are rejected when deserializing (extra='forbid')."""
    import json

    payload = json.loads(make_output().to_json())
    payload["confidence"] = 0.9
    with pytest.raises(ValidationError):
        ReadinessOutput.from_json(json.dumps(payload))


@pytest.mark.parametrize("missing_field", REQUIRED_FIELDS)
def test_missing_required_field_raises(missing_field: str) -> None:
    """(c) Dropping any required field raises ValidationError."""
    kwargs = {k: v for k, v in VALID_KWARGS.items() if k != missing_field}
    with pytest.raises(ValidationError):
        ReadinessOutput(**kwargs)


@pytest.mark.parametrize("verdict", list(ReadinessVerdict))
def test_all_verdict_values_accepted(verdict: ReadinessVerdict) -> None:
    """(d) Every ReadinessVerdict member is a valid verdict."""
    output = make_output(verdict=verdict)
    assert output.verdict is verdict
    assert ReadinessOutput.from_json(output.to_json()).verdict is verdict


@pytest.mark.parametrize("verdict", list(ReadinessVerdict))
def test_verdict_string_values_match_names(verdict: ReadinessVerdict) -> None:
    """(e) The string value of each verdict equals its member name."""
    assert verdict.value == verdict.name


def test_verdict_members_are_exactly_three() -> None:
    """(d) The contract exposes exactly the three routing verdicts (ID-065)."""
    assert {v.name for v in ReadinessVerdict} == {
        "READY",
        "NEEDS_CLARIFICATION",
        "MANUAL_REQUIRED",
    }


def test_invalid_verdict_string_rejected() -> None:
    """A verdict string outside the enum is rejected."""
    import json

    payload = json.loads(make_output().to_json())
    payload["verdict"] = "BLOCKED"
    with pytest.raises(ValidationError):
        ReadinessOutput.from_json(json.dumps(payload))


def test_malformed_json_rejected() -> None:
    """(f) Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        ReadinessOutput.from_json("{not valid json")


def test_rubric_results_accepts_empty_dict() -> None:
    """(g) An empty rubric_results mapping is accepted."""
    output = make_output(rubric_results={})
    assert output.rubric_results == {}
    assert ReadinessOutput.from_json(output.to_json()).rubric_results == {}


def test_rubric_results_rejects_non_boolean_value() -> None:
    """(g) rubric_results rejects a non-boolean value for a check.

    Pydantic v2 lax mode coerces bool-like scalars (``"yes"``/``"true"``/``1``)
    to ``bool``, so a list — unambiguously not a bool — is used to confirm the
    field enforces its declared type.
    """
    with pytest.raises(ValidationError):
        ReadinessOutput(
            ticket_id="sfp-35-readiness-schema",
            verdict=ReadinessVerdict.READY,
            blocking_ambiguities=[],
            missing_inputs=[],
            rubric_results={"acceptance_criteria_present": [1, 2]},  # type: ignore[dict-item]
        )


def test_blocking_ambiguities_and_missing_inputs_may_be_non_empty() -> None:
    """List fields carry bullet-style items when a ticket is not ready."""
    output = make_output(
        verdict=ReadinessVerdict.NEEDS_CLARIFICATION,
        blocking_ambiguities=["'messaging provider' is ambiguous"],
        missing_inputs=["no acceptance criteria attached"],
    )
    assert output.blocking_ambiguities == ["'messaging provider' is ambiguous"]
    assert output.missing_inputs == ["no acceptance criteria attached"]
    assert output.verdict is ReadinessVerdict.NEEDS_CLARIFICATION


def test_json_contains_no_extra_fields() -> None:
    """The serialized JSON contains only the five contracted fields."""
    import json

    payload = json.loads(make_output().to_json())
    assert set(payload.keys()) == {
        "ticket_id",
        "verdict",
        "blocking_ambiguities",
        "missing_inputs",
        "rubric_results",
    }
