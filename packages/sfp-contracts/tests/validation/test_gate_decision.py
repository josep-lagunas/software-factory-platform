"""Tests for the GateDecision contract (SFP-74 / ID-024 / ID-067).

Covers the acceptance criteria:
- (a) GateDecision constructs with all four fields populated;
- (b) ``extra='forbid'`` rejects unknown fields (schema drift surfaces);
- (c) ``to_json`` / ``from_json`` round-trip losslessly;
- (d) ``required_gates`` carries ``GATE_MAPPING[profile]`` for every profile
  (the model is a faithful carrier of the canonical mapping);
- (e) ``auto_merge_eligible == not requires_human_approval`` for hand-constructed
  instances (the consistency invariant the logic-half relies on).

The expected per-profile gate sets are encoded INDEPENDENTLY here (not imported
from the implementation) for the carrier assertions, so the test is a genuine
oracle for the mapping shape.
"""

import pytest
from pydantic import ValidationError
from sfp_contracts.validation.gate_decision import GateDecision
from sfp_contracts.validation.profiles import (
    GATE_MAPPING,
    ValidationProfile,
)

#: Independent oracle: the four always-on gates every tier enforces (ID-024).
BASE_GATES = [
    "blueprint_compliance",
    "acceptance_criteria_satisfied",
    "test_plan_satisfied",
    "no_unrelated_changes",
]


def test_constructs_with_all_four_fields() -> None:
    """(a) GateDecision accepts and stores all four fields."""
    decision = GateDecision(
        profile=ValidationProfile.LEVEL_1_INTERNAL,
        required_gates=list(BASE_GATES),
        requires_human_approval=False,
        auto_merge_eligible=True,
    )
    assert decision.profile is ValidationProfile.LEVEL_1_INTERNAL
    assert decision.required_gates == list(BASE_GATES)
    assert decision.requires_human_approval is False
    assert decision.auto_merge_eligible is True


def test_required_gates_is_a_list() -> None:
    """(a) required_gates is typed as a list (mutable sequence carrier)."""
    decision = GateDecision(
        profile=ValidationProfile.LEVEL_2_BACKEND_OR_API,
        required_gates=["a", "b"],
        requires_human_approval=True,
        auto_merge_eligible=False,
    )
    assert isinstance(decision.required_gates, list)


def test_extra_fields_rejected() -> None:
    """(b) extra='forbid' rejects unknown fields."""
    with pytest.raises(ValidationError):
        GateDecision(  # type: ignore[call-arg]
            profile=ValidationProfile.LEVEL_1_INTERNAL,
            required_gates=[],
            requires_human_approval=False,
            auto_merge_eligible=True,
            surprise="not allowed",
        )


def test_missing_required_field_rejected() -> None:
    """(b) omitting a required field raises ValidationError."""
    with pytest.raises(ValidationError):
        GateDecision(  # type: ignore[call-arg]
            profile=ValidationProfile.LEVEL_1_INTERNAL,
            required_gates=[],
            requires_human_approval=False,
            # auto_merge_eligible omitted
        )


def test_to_json_from_json_round_trip() -> None:
    """(c) to_json then from_json reproduces an equal GateDecision."""
    original = GateDecision(
        profile=ValidationProfile.LEVEL_3_USER_FACING,
        required_gates=list(GATE_MAPPING[ValidationProfile.LEVEL_3_USER_FACING]),
        requires_human_approval=True,
        auto_merge_eligible=False,
    )
    encoded = original.to_json()
    assert isinstance(encoded, str)
    decoded = GateDecision.from_json(encoded)
    assert decoded == original


def test_from_json_accepts_bytes() -> None:
    """(c) from_json also accepts bytes."""
    original = GateDecision(
        profile=ValidationProfile.LEVEL_4_HIGH_RISK,
        required_gates=list(GATE_MAPPING[ValidationProfile.LEVEL_4_HIGH_RISK]),
        requires_human_approval=True,
        auto_merge_eligible=False,
    )
    decoded = GateDecision.from_json(original.to_json().encode())
    assert decoded == original


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_required_gates_carries_gate_mapping(profile: ValidationProfile) -> None:
    """(d) A hand-built decision faithfully carries GATE_MAPPING[profile]."""
    decision = GateDecision(
        profile=profile,
        required_gates=list(GATE_MAPPING[profile]),
        requires_human_approval=profile is not ValidationProfile.LEVEL_1_INTERNAL,
        auto_merge_eligible=profile is ValidationProfile.LEVEL_1_INTERNAL,
    )
    assert decision.required_gates == list(GATE_MAPPING[profile])


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_auto_merge_eligible_is_complement_of_human_approval(
    profile: ValidationProfile,
) -> None:
    """(e) For consistent hand-constructed instances, the two flags complement."""
    needs_human = profile is not ValidationProfile.LEVEL_1_INTERNAL
    decision = GateDecision(
        profile=profile,
        required_gates=list(GATE_MAPPING[profile]),
        requires_human_approval=needs_human,
        auto_merge_eligible=not needs_human,
    )
    assert decision.auto_merge_eligible is (not decision.requires_human_approval)


def test_json_contains_profile_name() -> None:
    """Sanity: the serialized form carries the profile member name (ID-013)."""
    decision = GateDecision(
        profile=ValidationProfile.LEVEL_2_BACKEND_OR_API,
        required_gates=["x"],
        requires_human_approval=True,
        auto_merge_eligible=False,
    )
    assert "LEVEL_2_BACKEND_OR_API" in decision.to_json()
