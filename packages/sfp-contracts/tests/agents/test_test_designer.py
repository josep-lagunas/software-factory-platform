"""Tests for the :class:`TestDesignerOutput` schema (SFP-34 / SFP-17).

Covers the acceptance criteria:
- (a) conformant payload round-trips through ``to_json``/``from_json``;
- (b) extra fields rejected on construction AND ``from_json`` (extra='forbid');
- (c) every missing required field raises ``ValidationError`` — both the
  top-level output and every one of the seven ``test_plan`` buckets;
- (d) all seven ``test_plan`` list fields accept and round-trip values
  (parametrized per-field);
- (e) every ``test_plan`` field is typed ``list[str]`` (parametrized);
- (f) ``test_plan`` rejects non-string list elements;
- (g) malformed JSON is rejected.
"""

from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.agents.test_designer import TestDesignerOutput, TestPlan

VALID_TEST_PLAN_KWARGS: dict[str, Any] = {
    "unit_tests": ["validates conformant payload round-trips"],
    "integration_tests": ["plan deserializes inside an envelope payload"],
    "e2e_or_smoke_tests": ["boot the agent runtime end-to-end"],
    "negative_tests": ["rejects an unknown test_plan bucket"],
    "edge_cases": ["empty bucket list is accepted"],
    "regression_risks": ["schema drift between designer and coder consumers"],
    "required_validation_commands": ["uv run pytest packages/sfp-contracts -q"],
}

VALID_KWARGS: dict[str, Any] = {
    "pr_spec_id": "sfp-34-test-designer-schema",
    "test_plan": TestPlan(**VALID_TEST_PLAN_KWARGS),
}

TOP_LEVEL_REQUIRED_FIELDS = list(VALID_KWARGS.keys())
PLAN_REQUIRED_FIELDS = list(VALID_TEST_PLAN_KWARGS.keys())


def make_output(**overrides: Any) -> TestDesignerOutput:
    kwargs = dict(VALID_KWARGS)
    kwargs.update(overrides)
    return TestDesignerOutput(**kwargs)


def make_plan(**overrides: Any) -> TestPlan:
    kwargs = dict(VALID_TEST_PLAN_KWARGS)
    kwargs.update(overrides)
    return TestPlan(**kwargs)


def test_round_trip_preserves_every_field() -> None:
    """(a) A conformant TestDesignerOutput round-trips through JSON losslessly."""
    original = make_output()
    restored = TestDesignerOutput.from_json(original.to_json())

    assert restored == original
    assert restored.pr_spec_id == "sfp-34-test-designer-schema"
    assert restored.test_plan.unit_tests == ["validates conformant payload round-trips"]
    assert restored.test_plan.required_validation_commands == [
        "uv run pytest packages/sfp-contracts -q"
    ]


@pytest.mark.parametrize(
    "extra",
    [
        {"test_cases": [{"name": "x"}]},
        {"coverage_target": 0.9},
        {"unexpected": "x"},
    ],
)
def test_extra_fields_rejected_on_construction(extra: dict[str, Any]) -> None:
    """(b) Unknown extra fields are rejected at construction."""
    with pytest.raises(ValidationError):
        make_output(**extra)


def test_extra_fields_rejected_on_from_json() -> None:
    """(b) Extra fields are rejected when deserializing (extra='forbid')."""
    import json

    payload = json.loads(make_output().to_json())
    payload["test_cases"] = [{"name": "x"}]
    with pytest.raises(ValidationError):
        TestDesignerOutput.from_json(json.dumps(payload))


def test_test_plan_rejects_extra_field() -> None:
    """(b) TestPlan also rejects unknown buckets (extra='forbid')."""
    with pytest.raises(ValidationError):
        make_plan(coverage_target=0.9)


@pytest.mark.parametrize("missing_field", TOP_LEVEL_REQUIRED_FIELDS)
def test_missing_top_level_required_field_raises(missing_field: str) -> None:
    """(c) Dropping a top-level required field raises ValidationError."""
    kwargs = {k: v for k, v in VALID_KWARGS.items() if k != missing_field}
    with pytest.raises(ValidationError):
        TestDesignerOutput(**kwargs)


@pytest.mark.parametrize("missing_field", PLAN_REQUIRED_FIELDS)
def test_missing_test_plan_required_field_raises(missing_field: str) -> None:
    """(c) Dropping any of the seven test_plan buckets raises ValidationError."""
    kwargs = {k: v for k, v in VALID_TEST_PLAN_KWARGS.items() if k != missing_field}
    with pytest.raises(ValidationError):
        TestPlan(**kwargs)


@pytest.mark.parametrize("field", PLAN_REQUIRED_FIELDS)
def test_test_plan_field_accepts_and_round_trips(field: str) -> None:
    """(d) Every test_plan list field accepts values and survives a round-trip."""
    plan = make_plan(**{field: ["a description", "another description"]})
    assert getattr(plan, field) == ["a description", "another description"]

    output = make_output(test_plan=plan)
    restored = TestDesignerOutput.from_json(output.to_json())
    assert getattr(restored.test_plan, field) == ["a description", "another description"]


@pytest.mark.parametrize("field", PLAN_REQUIRED_FIELDS)
def test_test_plan_field_typed_list_of_str(field: str) -> None:
    """(e) Every test_plan field is annotated as ``list[str]``."""
    annotation = TestPlan.model_fields[field].annotation
    # list[str] == list[str]; compare against the literal generic alias.
    assert annotation == list[str]


def test_test_plan_accepts_empty_buckets() -> None:
    """An empty list for every bucket is accepted (emptiness is a policy call)."""
    plan = TestPlan(
        unit_tests=[],
        integration_tests=[],
        e2e_or_smoke_tests=[],
        negative_tests=[],
        edge_cases=[],
        regression_risks=[],
        required_validation_commands=[],
    )
    assert plan.unit_tests == []
    restored = TestDesignerOutput.from_json(make_output(test_plan=plan).to_json())
    assert restored.test_plan.negative_tests == []


def test_test_plan_rejects_non_string_elements() -> None:
    """(f) A non-string element inside a list[str] bucket is rejected."""
    with pytest.raises(ValidationError):
        TestPlan(
            unit_tests=["valid", {"not": "a string"}],  # type: ignore[list-item]
            integration_tests=[],
            e2e_or_smoke_tests=[],
            negative_tests=[],
            edge_cases=[],
            regression_risks=[],
            required_validation_commands=[],
        )


def test_malformed_json_rejected() -> None:
    """(g) Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        TestDesignerOutput.from_json("{not valid json")
