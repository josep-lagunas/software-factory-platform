"""Tests for the validation profile -> gate mapping (SFP-41 / ID-024 / ID-067).

Covers the acceptance criteria:
- (a) the enum exposes exactly its four members with string values == names;
- (b) every profile maps to a non-empty gate list (parametrized);
- (c) the mapping is immutable data (read-only proxy, tuple values);
- (d) the mapping is graduated: the base four gates appear at every tier and
  gate count increases monotonically with risk;
- (e) tier-specific gates land at the right level (maintainability, security,
  dedicated security_review, migration_reversibility);
- (f) ``REQUIRES_HUMAN_APPROVAL`` excludes LEVEL_1 and includes L2/L3/L4;
- (g) ``requires_human_approval()`` returns False for L1, True for L2/3/4 and
  is consistent with the frozenset.
"""

from types import MappingProxyType

import pytest
from sfp_contracts.validation.profiles import (
    GATE_MAPPING,
    REQUIRES_HUMAN_APPROVAL,
    ValidationProfile,
    requires_human_approval,
)

EXPECTED_MEMBERS = {
    "LEVEL_1_INTERNAL",
    "LEVEL_2_BACKEND_OR_API",
    "LEVEL_3_USER_FACING",
    "LEVEL_4_HIGH_RISK",
}

#: The four always-on gates that must appear in every profile's list (ID-024).
BASE_GATES = {
    "blueprint_compliance",
    "acceptance_criteria_satisfied",
    "test_plan_satisfied",
    "no_unrelated_changes",
}


def test_enum_has_exactly_four_members() -> None:
    """(a) ValidationProfile exposes exactly the four ID-067 members."""
    assert {m.name for m in ValidationProfile} == EXPECTED_MEMBERS
    assert len(ValidationProfile) == 4


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_string_value_equals_member_name(profile: ValidationProfile) -> None:
    """(a) Each StrEnum value equals its member name (ID-013)."""
    assert profile.value == profile.name
    assert isinstance(profile.value, str)


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_every_profile_maps_to_nonempty_gate_list(profile: ValidationProfile) -> None:
    """(b) GATE_MAPPING has an entry for every profile, with >=1 gate."""
    assert profile in GATE_MAPPING
    gates = GATE_MAPPING[profile]
    assert len(gates) >= 1
    assert all(isinstance(g, str) and g for g in gates)


def test_gate_mapping_has_no_extra_or_missing_keys() -> None:
    """(b) The mapping keys are exactly the enum members."""
    assert set(GATE_MAPPING.keys()) == set(ValidationProfile)


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_gate_mapping_values_are_tuples(profile: ValidationProfile) -> None:
    """(c) Each gate list is a tuple (immutable sequence)."""
    assert isinstance(GATE_MAPPING[profile], tuple)


def test_gate_mapping_is_read_only_proxy() -> None:
    """(c) GATE_MAPPING is a MappingProxyType (immutable)."""
    assert isinstance(GATE_MAPPING, MappingProxyType)
    with pytest.raises(TypeError):
        GATE_MAPPING[ValidationProfile.LEVEL_1_INTERNAL] = ()  # type: ignore[index]


def test_base_gates_present_at_every_tier() -> None:
    """(d) The four always-on gates appear in every profile's list."""
    for profile in ValidationProfile:
        assert BASE_GATES.issubset(set(GATE_MAPPING[profile])), profile


def test_gate_count_increases_monotonically_with_risk() -> None:
    """(d) Higher risk tiers enforce a superset of lower-tier gates."""
    counts = [
        len(GATE_MAPPING[ValidationProfile.LEVEL_1_INTERNAL]),
        len(GATE_MAPPING[ValidationProfile.LEVEL_2_BACKEND_OR_API]),
        len(GATE_MAPPING[ValidationProfile.LEVEL_3_USER_FACING]),
        len(GATE_MAPPING[ValidationProfile.LEVEL_4_HIGH_RISK]),
    ]
    assert counts == sorted(counts)
    # And each tier is a strict superset of the previous (no gate dropped).
    l1 = set(GATE_MAPPING[ValidationProfile.LEVEL_1_INTERNAL])
    l2 = set(GATE_MAPPING[ValidationProfile.LEVEL_2_BACKEND_OR_API])
    l3 = set(GATE_MAPPING[ValidationProfile.LEVEL_3_USER_FACING])
    l4 = set(GATE_MAPPING[ValidationProfile.LEVEL_4_HIGH_RISK])
    assert l1 < l2 < l3 < l4


def test_level_1_is_exactly_the_base_gates() -> None:
    """(d) LEVEL_1 enforces only the four base gates (lightest tier)."""
    assert set(GATE_MAPPING[ValidationProfile.LEVEL_1_INTERNAL]) == BASE_GATES


def test_tier_specific_gates_land_at_correct_levels() -> None:
    """(e) Maintainability, security and dedicated gates tier correctly."""
    l2 = set(GATE_MAPPING[ValidationProfile.LEVEL_2_BACKEND_OR_API])
    l3 = set(GATE_MAPPING[ValidationProfile.LEVEL_3_USER_FACING])
    l4 = set(GATE_MAPPING[ValidationProfile.LEVEL_4_HIGH_RISK])

    # maintainability appears from LEVEL_2 upward.
    assert "maintainability_acceptable" in l2
    # security_acceptable + dedicated security_review appear from LEVEL_3 up.
    assert "security_acceptable" in l3
    assert "security_review" in l3
    # migration_reversibility only at the highest tier.
    assert "migration_reversibility" in l4
    assert "migration_reversibility" not in l3


def test_level_1_has_no_security_or_dedicated_gates() -> None:
    """(e) LEVEL_1 carries neither security_acceptable nor security_review."""
    l1 = set(GATE_MAPPING[ValidationProfile.LEVEL_1_INTERNAL])
    assert "security_acceptable" not in l1
    assert "security_review" not in l1
    assert "maintainability_acceptable" not in l1


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_no_duplicate_gates_within_a_profile(profile: ValidationProfile) -> None:
    """Sanity: a profile never lists the same gate twice."""
    gates = GATE_MAPPING[profile]
    assert len(gates) == len(set(gates))


def test_requires_human_approval_set_excludes_level_1() -> None:
    """(f) REQUIRES_HUMAN_APPROVAL is a frozenset without LEVEL_1."""
    assert isinstance(REQUIRES_HUMAN_APPROVAL, frozenset)
    assert ValidationProfile.LEVEL_1_INTERNAL not in REQUIRES_HUMAN_APPROVAL


def test_requires_human_approval_set_includes_l2_l3_l4() -> None:
    """(f) L2/L3/L4 all require human approval."""
    assert REQUIRES_HUMAN_APPROVAL == frozenset(
        {
            ValidationProfile.LEVEL_2_BACKEND_OR_API,
            ValidationProfile.LEVEL_3_USER_FACING,
            ValidationProfile.LEVEL_4_HIGH_RISK,
        }
    )


@pytest.mark.parametrize(
    "profile,expected",
    [
        (ValidationProfile.LEVEL_1_INTERNAL, False),
        (ValidationProfile.LEVEL_2_BACKEND_OR_API, True),
        (ValidationProfile.LEVEL_3_USER_FACING, True),
        (ValidationProfile.LEVEL_4_HIGH_RISK, True),
    ],
)
def test_requires_human_approval_function(profile: ValidationProfile, expected: bool) -> None:
    """(g) requires_human_approval() is False for L1, True for L2/3/4."""
    assert requires_human_approval(profile) is expected


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_function_consistent_with_frozenset(profile: ValidationProfile) -> None:
    """(g) The function delegates to REQUIRES_HUMAN_APPROVAL."""
    assert requires_human_approval(profile) == (profile in REQUIRES_HUMAN_APPROVAL)
