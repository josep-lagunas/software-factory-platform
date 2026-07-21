"""Tests for the failure-classification taxonomy and model (SFP-75).

Covers the acceptance criteria AC1-AC5 + AC10:
- (AC1) FailureCategory is a StrEnum with exactly DEVELOPMENT_FAILURE, BLOCKED.
- (AC2) BlockedCause is a StrEnum with exactly the 8 ID-068 members in order.
- (AC3) FailureSource is a StrEnum with exactly 15 members in the pinned order.
- (AC4) FailureClassification is a pydantic model with extra='forbid', cause
  defaults to None, detail defaults to '', recoverable has no default.
- (AC5) to_json/from_json round-trip for every combination, including cause=None.
- extra='forbid' on construction AND from_json.
- StrEnum members serialize to plain string (ID-013); malformed/invalid JSON and
  bytes handled by from_json.
"""

import json
from enum import StrEnum
from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.workflow.failure import (
    BlockedCause,
    FailureCategory,
    FailureClassification,
    FailureSource,
)

#: AC1 — exact, ordered FailureCategory members.
EXPECTED_CATEGORY: list[str] = ["DEVELOPMENT_FAILURE", "BLOCKED"]

#: AC2 — exact, ordered BlockedCause members (ID-068).
EXPECTED_CAUSES: list[str] = [
    "INCOMPLETE_DEPENDENCY",
    "MISSING_CONTEXT",
    "MISSING_SECRET",
    "REPO_INACCESSIBLE",
    "UNRESOLVED_CLARIFICATION",
    "MERGE_QUEUE_FAILURE",
    "DEPLOYMENT_FAILURE",
    "EXTERNAL_SYSTEM_UNAVAILABLE",
]

#: AC3 — exact, ordered FailureSource members (15; REPO added by SFP-75).
EXPECTED_SOURCES: list[str] = [
    "LINT",
    "TYPECHECK",
    "BUILD",
    "UNIT_TEST",
    "INTEGRATION_TEST",
    "CI",
    "DEPENDENCY",
    "SECRET",
    "CONTEXT",
    "CLARIFICATION",
    "MERGE",
    "DEPLOYMENT",
    "EXTERNAL_SYSTEM",
    "NETWORK",
    "REPO",
]


def _assert_strenum(cls: type[StrEnum], expected_names: list[str]) -> None:
    """Assert a StrEnum has exactly the expected members, in order, value==name."""
    members = list(cls.__members__)
    assert members == expected_names, f"{cls.__name__} members mismatch"
    for member in cls:
        assert member.value == member.name, f"{cls.__name__}.{member.name} value != name"


def test_failure_category_is_strenum() -> None:
    """FailureCategory subclasses StrEnum."""
    assert issubclass(FailureCategory, StrEnum)


def test_failure_category_members() -> None:
    """AC1 — exactly DEVELOPMENT_FAILURE, BLOCKED, in order, value==name."""
    _assert_strenum(FailureCategory, EXPECTED_CATEGORY)


def test_blocked_cause_members() -> None:
    """AC2 — exactly the 8 ID-068 causes, in order, value==name."""
    _assert_strenum(BlockedCause, EXPECTED_CAUSES)


def test_failure_source_members() -> None:
    """AC3 — exactly the 15 pinned sources, in order, value==name (REPO included)."""
    _assert_strenum(FailureSource, EXPECTED_SOURCES)
    assert len(list(FailureSource)) == 15


def test_model_extra_forbid_config() -> None:
    """AC4 — the model is configured with extra='forbid'."""
    assert FailureClassification.model_config.get("extra") == "forbid"


def test_model_field_set_and_annotations() -> None:
    """AC4 — the model exposes exactly the four pinned fields with right types."""
    fields = FailureClassification.model_fields
    assert set(fields) == {"category", "cause", "recoverable", "detail"}
    # cause is Optional[BlockedCause] -> annotation is the union with None.
    assert fields["cause"].is_required() is False
    assert fields["detail"].is_required() is False
    # recoverable is required (no default).
    assert fields["recoverable"].is_required() is True
    assert fields["category"].is_required() is True


def test_construction_rejects_unknown_field() -> None:
    """AC4 — extra='forbid' rejects an unknown field at construction."""
    with pytest.raises(ValidationError):
        FailureClassification(  # type: ignore[call-arg]
            category=FailureCategory.DEVELOPMENT_FAILURE,
            recoverable=False,
            unexpected="x",
        )


def test_from_json_rejects_unknown_field() -> None:
    """AC4 — extra='forbid' rejects an unknown field on deserialization."""
    payload = {
        "category": "DEVELOPMENT_FAILURE",
        "cause": None,
        "recoverable": False,
        "detail": "",
        "unexpected": "x",
    }
    with pytest.raises(ValidationError):
        FailureClassification.from_json(json.dumps(payload))


def test_cause_defaults_to_none_and_detail_empty() -> None:
    """AC4 — cause defaults to None and detail to '' when omitted."""
    obj = FailureClassification(category=FailureCategory.DEVELOPMENT_FAILURE, recoverable=False)
    assert obj.cause is None
    assert obj.detail == ""


def test_recoverable_is_required() -> None:
    """AC4 — recoverable has no default; omitting it raises ValidationError."""
    with pytest.raises(ValidationError):
        FailureClassification(category=FailureCategory.DEVELOPMENT_FAILURE)  # type: ignore[call-arg]


def test_category_is_required() -> None:
    """AC4 — category has no default; omitting it raises ValidationError."""
    with pytest.raises(ValidationError):
        FailureClassification(recoverable=False)  # type: ignore[call-arg]


def test_cause_accepts_all_blocked_cause_values() -> None:
    """cause accepts every BlockedCause member by its string value."""
    for cause in BlockedCause:
        obj = FailureClassification(category=FailureCategory.BLOCKED, cause=cause, recoverable=True)
        assert obj.cause is cause


def test_cause_rejects_non_enum_string() -> None:
    """A cause string outside the enum is rejected."""
    with pytest.raises(ValidationError):
        FailureClassification(
            category=FailureCategory.BLOCKED, cause="NOT_A_CAUSE", recoverable=True
        )


@pytest.mark.parametrize(
    "category, cause, recoverable, detail",
    [
        (FailureCategory.DEVELOPMENT_FAILURE, None, False, ""),
        (FailureCategory.DEVELOPMENT_FAILURE, None, False, "lint failed"),
        *[
            (FailureCategory.BLOCKED, cause, flag, f"blocked: {cause.value}")
            for cause in BlockedCause
            for flag in (True, False)
        ],
    ],
)
def test_round_trip(
    category: FailureCategory,
    cause: BlockedCause | None,
    recoverable: bool,
    detail: str,
) -> None:
    """AC5 — to_json/from_json round-trips losslessly (incl. cause=None)."""
    obj = FailureClassification(
        category=category, cause=cause, recoverable=recoverable, detail=detail
    )
    restored = FailureClassification.from_json(obj.to_json())
    assert restored == obj
    assert restored.category is category
    assert restored.cause is cause
    assert restored.recoverable is recoverable
    assert restored.detail == detail


def test_strenum_serializes_to_plain_string() -> None:
    """ID-013 — category/cause serialize to their plain member-name strings."""
    obj = FailureClassification(
        category=FailureCategory.BLOCKED,
        cause=BlockedCause.MISSING_SECRET,
        recoverable=True,
    )
    raw = json.loads(obj.to_json())
    assert raw["category"] == "BLOCKED"
    assert raw["cause"] == "MISSING_SECRET"
    assert raw["recoverable"] is True


def test_cause_none_serializes_to_null() -> None:
    """cause=None serializes to JSON null and round-trips back to None."""
    obj = FailureClassification(
        category=FailureCategory.DEVELOPMENT_FAILURE, cause=None, recoverable=False
    )
    raw = json.loads(obj.to_json())
    assert raw["cause"] is None
    assert FailureClassification.from_json(obj.to_json()).cause is None


def test_from_json_accepts_bytes() -> None:
    """from_json accepts bytes as well as str (str | bytes signature)."""
    obj = FailureClassification(
        category=FailureCategory.BLOCKED,
        cause=BlockedCause.REPO_INACCESSIBLE,
        recoverable=False,
        detail="clone failed",
    )
    restored = FailureClassification.from_json(obj.to_json().encode("utf-8"))
    assert restored == obj


def test_from_json_rejects_malformed_json() -> None:
    """Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        FailureClassification.from_json("{not valid json")


def test_from_json_rejects_invalid_enum_value() -> None:
    """An invalid category string is rejected on deserialization."""
    payload = {"category": "NOT_A_CATEGORY", "cause": None, "recoverable": False}
    with pytest.raises(ValidationError):
        FailureClassification.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"recoverable": False},  # missing category
        {"category": FailureCategory.BLOCKED},  # missing recoverable
        {"category": "DEVELOPMENT_FAILURE", "recoverable": "not-a-bool"},  # bad type
    ],
)
def test_invalid_construction_rejected(kwargs: dict[str, Any]) -> None:
    """Construction with missing/invalid required fields raises ValidationError."""
    with pytest.raises(ValidationError):
        FailureClassification(**kwargs)  # type: ignore[arg-type]
