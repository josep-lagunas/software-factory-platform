"""Tests for the :class:`PlannerOutput` payload and :class:`PrSpec` element.

Covers the SFP-14 / SFP-31 acceptance criteria:
- (a) a fully-populated ``pr_spec`` validates and round-trips through JSON;
- (b) extra/unknown fields are rejected on construction AND ``from_json``
  (``extra='forbid'``) for both ``PrSpec`` and ``PlannerOutput``;
- (c) dropping any required ``PrSpec`` field raises ``ValidationError``;
- (d) an empty ``pr_specs`` list is rejected (``min_length=1``);
- (e) every :class:`ValidationProfile` member is accepted as the
  ``validation_profile`` value, and a non-member string is rejected;
- (f) multiple PR-specs are accepted in a single ``PlannerOutput``;
- (g) ``PrSpec`` is not a valid ``validation_profile`` substitute for the list
  field, and ``pr_specs`` must hold ``PrSpec`` instances (structural rejection).
"""

import json

import pytest
from pydantic import ValidationError
from sfp_contracts.agents.planner import PlannerOutput, PrSpec
from sfp_contracts.validation.profiles import ValidationProfile

VALID_PRSPEC_KWARGS: dict[str, object] = {
    "id": "PR-1",
    "title": "Add PlannerOutput schema",
    "goal": "Define the deterministic planner-output contract.",
    "scope": ["Create planner.py", "Export PrSpec"],
    "out_of_scope": ["Coder/Reviewer payloads"],
    "acceptance_criteria": [
        "Validates a fully-populated pr_spec",
        "Rejects payloads missing required fields",
    ],
    "dependencies": ["SFP-13"],
    "validation_profile": ValidationProfile.LEVEL_1_INTERNAL,
    "validation_profile_reason": "Contracts-only change with no runtime impact.",
    "required_gates": ["ci", "unit"],
    "likely_files_or_modules": ["packages/sfp-contracts/src/sfp_contracts/agents/planner.py"],
    "risks": ["Schema may drift from ID-066 field list."],
    "implementation_notes": "Reuse the ClassVar[ConfigDict] pattern from envelope.py.",
}

PRSPEC_REQUIRED_FIELDS = list(VALID_PRSPEC_KWARGS.keys())


def make_prspec(**overrides: object) -> PrSpec:
    kwargs = dict(VALID_PRSPEC_KWARGS)
    kwargs.update(overrides)
    return PrSpec(**kwargs)


def make_output(pr_specs: list[PrSpec] | None = None) -> PlannerOutput:
    return PlannerOutput(pr_specs=pr_specs if pr_specs is not None else [make_prspec()])


# --- (a) fully-populated validates + round-trips -----------------------------


def test_fully_populated_prspec_validates() -> None:
    """(a) A fully-populated PrSpec constructs and preserves every field."""
    spec = make_prspec()
    assert spec.id == "PR-1"
    assert spec.title == "Add PlannerOutput schema"
    assert spec.goal == "Define the deterministic planner-output contract."
    assert spec.scope == ["Create planner.py", "Export PrSpec"]
    assert spec.out_of_scope == ["Coder/Reviewer payloads"]
    assert spec.acceptance_criteria == [
        "Validates a fully-populated pr_spec",
        "Rejects payloads missing required fields",
    ]
    assert spec.dependencies == ["SFP-13"]
    assert spec.validation_profile is ValidationProfile.LEVEL_1_INTERNAL
    assert spec.validation_profile_reason == "Contracts-only change with no runtime impact."
    assert spec.required_gates == ["ci", "unit"]
    assert spec.likely_files_or_modules == [
        "packages/sfp-contracts/src/sfp_contracts/agents/planner.py"
    ]
    assert spec.risks == ["Schema may drift from ID-066 field list."]
    assert spec.implementation_notes == ("Reuse the ClassVar[ConfigDict] pattern from envelope.py.")


def test_round_trip_preserves_every_field() -> None:
    """(a) A fully-populated PlannerOutput round-trips through JSON losslessly."""
    original = make_output()
    restored = PlannerOutput.from_json(original.to_json())
    assert restored == original
    assert restored.pr_specs[0].validation_profile is ValidationProfile.LEVEL_1_INTERNAL


# --- (b) extra='forbid' ------------------------------------------------------


@pytest.mark.parametrize("extra", [{"unexpected": "x"}, {"id_extra": "y"}])
def test_prspec_extra_fields_rejected_on_construction(extra: dict[str, str]) -> None:
    """(b) Unknown fields are rejected on PrSpec construction (extra='forbid')."""
    with pytest.raises(ValidationError):
        make_prspec(**extra)


def test_planneroutput_extra_fields_rejected_on_construction() -> None:
    """(b) Unknown fields are rejected on PlannerOutput construction."""
    with pytest.raises(ValidationError):
        PlannerOutput(pr_specs=[make_prspec()], unexpected="x")  # type: ignore[call-arg]


def test_extra_fields_rejected_on_from_json() -> None:
    """(b) Unknown fields are rejected when deserializing (extra='forbid')."""
    payload = json.loads(make_output().to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        PlannerOutput.from_json(json.dumps(payload))


def test_prspec_extra_fields_rejected_on_from_json() -> None:
    """(b) Unknown nested fields in a pr_spec are rejected on deserialize."""
    payload = json.loads(make_output().to_json())
    payload["pr_specs"][0]["unexpected"] = "x"
    with pytest.raises(ValidationError):
        PlannerOutput.from_json(json.dumps(payload))


# --- (c) missing required fields --------------------------------------------


@pytest.mark.parametrize("missing_field", PRSPEC_REQUIRED_FIELDS)
def test_missing_required_prspec_field_raises(missing_field: str) -> None:
    """(c) Dropping any required PrSpec field raises ValidationError."""
    kwargs = {k: v for k, v in VALID_PRSPEC_KWARGS.items() if k != missing_field}
    with pytest.raises(ValidationError):
        PrSpec(**kwargs)


def test_missing_pr_specs_field_raises() -> None:
    """(c) PlannerOutput requires the pr_specs field."""
    with pytest.raises(ValidationError):
        PlannerOutput()  # type: ignore[call-arg]


# --- (d) min_length=1 -------------------------------------------------------


def test_empty_pr_specs_rejected() -> None:
    """(d) An empty pr_specs list is rejected (min_length=1)."""
    with pytest.raises(ValidationError):
        PlannerOutput(pr_specs=[])


def test_empty_pr_specs_rejected_on_from_json() -> None:
    """(d) An empty pr_specs list is rejected when deserializing."""
    with pytest.raises(ValidationError):
        PlannerOutput.from_json('{"pr_specs": []}')


# --- (e) validation_profile enum -------------------------------------------


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_all_validation_profiles_accepted(profile: ValidationProfile) -> None:
    """(e) Every ValidationProfile member is a valid validation_profile."""
    spec = make_prspec(validation_profile=profile)
    assert spec.validation_profile is profile
    restored = PlannerOutput.from_json(make_output([spec]).to_json())
    assert restored.pr_specs[0].validation_profile is profile


def test_profile_string_values_match_names() -> None:
    """(e) The string value of each profile equals its member name."""
    for profile in ValidationProfile:
        assert profile.value == profile.name


def test_invalid_validation_profile_string_rejected() -> None:
    """(e) A validation_profile string outside the enum is rejected."""
    payload = json.loads(make_output().to_json())
    payload["pr_specs"][0]["validation_profile"] = "LEVEL_9_NONEXISTENT"
    with pytest.raises(ValidationError):
        PlannerOutput.from_json(json.dumps(payload))


# --- (f) multiple pr_specs --------------------------------------------------


def test_multiple_pr_specs_accepted() -> None:
    """(f) A PlannerOutput may carry more than one pr_spec."""
    first = make_prspec(id="PR-1")
    second = make_prspec(id="PR-2", title="Second PR-spec")
    output = make_output([first, second])
    assert [s.id for s in output.pr_specs] == ["PR-1", "PR-2"]
    restored = PlannerOutput.from_json(output.to_json())
    assert restored == output


# --- (g) structural rejection -----------------------------------------------


def test_pr_specs_must_hold_valid_prspecs() -> None:
    """(g) A pr_spec missing required nested fields is rejected."""
    payload = json.loads(make_output().to_json())
    del payload["pr_specs"][0]["id"]
    with pytest.raises(ValidationError):
        PlannerOutput.from_json(json.dumps(payload))


def test_malformed_json_rejected() -> None:
    """Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        PlannerOutput.from_json("{not valid json")
