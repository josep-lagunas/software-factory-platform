"""Tests for the :class:`ReviewerOutput` schema (SFP-33 / SFP-16).

Covers the acceptance criteria:
- (a) conformant payload round-trips through ``to_json``/``from_json``;
- (b) ``comments[]``, ``ci_passed``, ``validation_profile_gates_satisfied``
  rejected on construction AND ``from_json`` (extra='forbid');
- (c) every missing required field raises ``ValidationError``;
- (d) all four ``ReviewStatus`` members are accepted (parametrized);
- (e) ``quality_gates`` accepts all six booleans (parametrized per-field);
- (f) ``quality_gates`` rejects non-boolean values;
- (g) status string values equal the enum member names exactly;
- (h) malformed JSON is rejected.
"""

from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.agents.reviewer import (
    QualityGates,
    ReviewerOutput,
    ReviewStatus,
)

VALID_KWARGS: dict[str, Any] = {
    "pr_spec_id": "sfp-10-verify-prspec-linter",
    "review_status": ReviewStatus.APPROVED,
    "quality_gates": QualityGates(
        blueprint_compliance=True,
        acceptance_criteria_satisfied=True,
        test_plan_satisfied=True,
        no_unrelated_changes=True,
        maintainability_acceptable=True,
        security_acceptable=True,
    ),
}

REQUIRED_FIELDS = list(VALID_KWARGS.keys())


def make_output(**overrides: Any) -> ReviewerOutput:
    kwargs = dict(VALID_KWARGS)
    kwargs.update(overrides)
    return ReviewerOutput(**kwargs)


def test_round_trip_preserves_every_field() -> None:
    """(a) A conformant ReviewerOutput round-trips through JSON losslessly."""
    original = make_output()
    restored = ReviewerOutput.from_json(original.to_json())

    assert restored == original
    assert restored.pr_spec_id == "sfp-10-verify-prspec-linter"
    assert restored.review_status is ReviewStatus.APPROVED
    assert restored.quality_gates.blueprint_compliance is True
    assert restored.quality_gates.acceptance_criteria_satisfied is True
    assert restored.quality_gates.test_plan_satisfied is True
    assert restored.quality_gates.no_unrelated_changes is True
    assert restored.quality_gates.maintainability_acceptable is True
    assert restored.quality_gates.security_acceptable is True


@pytest.mark.parametrize(
    "extra",
    [
        {"comments": [{"body": "x", "path": "y"}]},
        {"ci_passed": True},
        {"validation_profile_gates_satisfied": True},
        {"unexpected": "x"},
    ],
)
def test_extra_fields_rejected_on_construction(extra: dict[str, Any]) -> None:
    """(b) Known and unknown extra fields are rejected at construction."""
    with pytest.raises(ValidationError):
        make_output(**extra)


def test_extra_fields_rejected_on_from_json() -> None:
    """(b) Extra fields are rejected when deserializing (extra='forbid')."""
    import json

    payload = json.loads(make_output().to_json())
    payload["comments"] = [{"body": "x", "path": "y"}]
    with pytest.raises(ValidationError):
        ReviewerOutput.from_json(json.dumps(payload))


@pytest.mark.parametrize("missing_field", REQUIRED_FIELDS)
def test_missing_required_field_raises(missing_field: str) -> None:
    """(c) Dropping any required field raises ValidationError."""
    kwargs = {k: v for k, v in VALID_KWARGS.items() if k != missing_field}
    with pytest.raises(ValidationError):
        ReviewerOutput(**kwargs)


@pytest.mark.parametrize("status", list(ReviewStatus))
def test_all_review_status_values_accepted(status: ReviewStatus) -> None:
    """(d) Every ReviewStatus member is a valid reviewer status."""
    output = make_output(review_status=status)
    assert output.review_status is status
    assert ReviewerOutput.from_json(output.to_json()).review_status is status


@pytest.mark.parametrize("status", list(ReviewStatus))
def test_status_string_values_match_names(status: ReviewStatus) -> None:
    """(e) The string value of each status equals its member name."""
    assert status.value == status.name


def test_quality_gates_all_true_accepted() -> None:
    """(f) QualityGates with all booleans True is accepted."""
    qg = QualityGates(
        blueprint_compliance=True,
        acceptance_criteria_satisfied=True,
        test_plan_satisfied=True,
        no_unrelated_changes=True,
        maintainability_acceptable=True,
        security_acceptable=True,
    )
    assert qg.blueprint_compliance is True
    assert qg.acceptance_criteria_satisfied is True
    assert qg.test_plan_satisfied is True
    assert qg.no_unrelated_changes is True
    assert qg.maintainability_acceptable is True
    assert qg.security_acceptable is True


def test_quality_gates_all_false_accepted() -> None:
    """(f) QualityGates with all booleans False is accepted."""
    qg = QualityGates(
        blueprint_compliance=False,
        acceptance_criteria_satisfied=False,
        test_plan_satisfied=False,
        no_unrelated_changes=False,
        maintainability_acceptable=False,
        security_acceptable=False,
    )
    assert qg.blueprint_compliance is False
    assert qg.acceptance_criteria_satisfied is False
    assert qg.test_plan_satisfied is False
    assert qg.no_unrelated_changes is False
    assert qg.maintainability_acceptable is False
    assert qg.security_acceptable is False


@pytest.mark.parametrize("field", list(QualityGates.model_fields.keys()))
def test_quality_gates_field_types(field: str) -> None:
    """(g) Each QualityGates field is typed as bool."""
    assert QualityGates.model_fields[field].annotation is bool


def test_invalid_review_status_string_rejected() -> None:
    """A review_status string outside the enum is rejected."""
    import json

    payload = json.loads(make_output().to_json())
    payload["review_status"] = "COMPLETED"
    with pytest.raises(ValidationError):
        ReviewerOutput.from_json(json.dumps(payload))


def test_malformed_json_rejected() -> None:
    """Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        ReviewerOutput.from_json("{not valid json")


def test_json_contains_no_comments_or_ci_passed() -> None:
    """The serialized JSON contains neither comments[] nor ci_passed."""
    output = make_output()
    json_str = output.to_json()

    assert "comments" not in json_str
    assert "ci_passed" not in json_str
    assert "validation_profile_gates_satisfied" not in json_str


def test_quality_gates_rejects_non_boolean_value() -> None:
    """QualityGates rejects a non-boolean value for a boolean field."""
    with pytest.raises(ValidationError):
        QualityGates(
            blueprint_compliance={"nested": "dict"},  # type: ignore[arg-type]
            acceptance_criteria_satisfied=True,
            test_plan_satisfied=True,
            no_unrelated_changes=True,
            maintainability_acceptable=True,
            security_acceptable=True,
        )


def test_quality_gates_rejects_missing_field() -> None:
    """QualityGates rejects a missing boolean field."""
    with pytest.raises(ValidationError):
        QualityGates(
            blueprint_compliance=True,
            acceptance_criteria_satisfied=True,
            test_plan_satisfied=True,
            no_unrelated_changes=True,
            # maintainability_acceptable missing
            security_acceptable=True,
        )
