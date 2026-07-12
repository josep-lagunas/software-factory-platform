"""Tests for the :class:`CoderOutput` payload and :class:`ValidationStatus` enum.

Covers the SFP-15 / SFP-32 acceptance criteria:
- (a) a fully-populated ``CoderOutput`` validates and round-trips through JSON;
- (b) extra/unknown fields are rejected on construction AND ``from_json``
  (``extra='forbid'``);
- (c) dropping any required field raises ``ValidationError``;
- (d) **no code-body field exists** — constructing with a ``code_body`` kwarg
  raises ``ValidationError`` (ID-066: code lives on the branch);
- (e) every :class:`ValidationStatus` member is accepted as
  ``validation_status``, and a non-member string is rejected.
"""

import json

import pytest
from pydantic import ValidationError
from sfp_contracts.agents.coder import CoderOutput, ValidationStatus

VALID_KWARGS: dict[str, object] = {
    "pr_spec_id": "PR-1",
    "branch_name": "sfp-32-coder-output-schema",
    "pull_request_url": "https://github.com/josep-lagunas/sfp/pull/1",
    "files_changed": [
        "packages/sfp-contracts/src/sfp_contracts/agents/coder.py",
    ],
    "tests_added_or_updated": [
        "packages/sfp-contracts/tests/agents/test_coder.py",
    ],
    "validation_status": ValidationStatus.PASSED,
    "validation_evidence": ["pytest: 12 passed", "mypy: no issues", "ruff: clean"],
    "known_limitations": [],
}

REQUIRED_FIELDS = list(VALID_KWARGS.keys())


def make_output(**overrides: object) -> CoderOutput:
    kwargs = dict(VALID_KWARGS)
    kwargs.update(overrides)
    return CoderOutput(**kwargs)


# --- (a) fully-populated validates + round-trips -----------------------------


def test_fully_populated_validates() -> None:
    """(a) A fully-populated CoderOutput constructs and preserves every field."""
    out = make_output()
    assert out.pr_spec_id == "PR-1"
    assert out.branch_name == "sfp-32-coder-output-schema"
    assert out.pull_request_url == "https://github.com/josep-lagunas/sfp/pull/1"
    assert out.files_changed == [
        "packages/sfp-contracts/src/sfp_contracts/agents/coder.py",
    ]
    assert out.tests_added_or_updated == [
        "packages/sfp-contracts/tests/agents/test_coder.py",
    ]
    assert out.validation_status is ValidationStatus.PASSED
    assert out.validation_evidence == ["pytest: 12 passed", "mypy: no issues", "ruff: clean"]
    assert out.known_limitations == []


def test_round_trip_preserves_every_field() -> None:
    """(a) A fully-populated CoderOutput round-trips through JSON losslessly."""
    original = make_output()
    restored = CoderOutput.from_json(original.to_json())
    assert restored == original
    assert restored.validation_status is ValidationStatus.PASSED


def test_from_json_accepts_bytes() -> None:
    """(a) from_json accepts bytes as well as str."""
    payload = make_output().to_json().encode("utf-8")
    assert CoderOutput.from_json(payload) == make_output()


# --- (b) extra='forbid' ------------------------------------------------------


@pytest.mark.parametrize("extra", [{"unexpected": "x"}, {"code_body": "print('hi')"}])
def test_extra_fields_rejected_on_construction(extra: dict[str, str]) -> None:
    """(b) Unknown fields (including any code body) are rejected on construction."""
    with pytest.raises(ValidationError):
        make_output(**extra)


def test_extra_fields_rejected_on_from_json() -> None:
    """(b) Unknown fields are rejected when deserializing (extra='forbid')."""
    payload = json.loads(make_output().to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        CoderOutput.from_json(json.dumps(payload))


def test_code_body_in_json_rejected_on_from_json() -> None:
    """(b)/(d) A code_body key in the JSON payload is rejected on deserialize."""
    payload = json.loads(make_output().to_json())
    payload["code_body"] = "print('leaked code')"
    with pytest.raises(ValidationError):
        CoderOutput.from_json(json.dumps(payload))


# --- (c) missing required fields --------------------------------------------


@pytest.mark.parametrize("missing_field", REQUIRED_FIELDS)
def test_missing_required_field_raises(missing_field: str) -> None:
    """(c) Dropping any required field raises ValidationError."""
    kwargs = {k: v for k, v in VALID_KWARGS.items() if k != missing_field}
    with pytest.raises(ValidationError):
        CoderOutput(**kwargs)


def test_no_arguments_raises() -> None:
    """(c) Constructing with no arguments raises ValidationError."""
    with pytest.raises(ValidationError):
        CoderOutput()  # type: ignore[call-arg]


# --- (d) no code-body field exists ------------------------------------------


def test_code_body_kwarg_rejected() -> None:
    """(d) Constructing with a code_body kwarg raises ValidationError.

    This is the ID-066 guarantee: the code is never carried in the payload, so
    a stray ``code_body`` argument must be rejected as an unknown field.
    """
    with pytest.raises(ValidationError):
        CoderOutput(**{**VALID_KWARGS, "code_body": "def f(): pass"})  # type: ignore[call-arg]


def test_code_body_is_not_a_model_field() -> None:
    """(d) ``code_body`` is absent from the model's declared field set."""
    assert "code_body" not in set(CoderOutput.model_fields)


def test_no_code_like_fields_present() -> None:
    """(d) No field with a 'code' substring exists in the schema."""
    codeish = {name for name in CoderOutput.model_fields if "code" in name.lower()}
    assert codeish == set()


# --- (e) validation_status enum --------------------------------------------


@pytest.mark.parametrize("status", list(ValidationStatus))
def test_all_validation_statuses_accepted(status: ValidationStatus) -> None:
    """(e) Every ValidationStatus member is a valid validation_status."""
    out = make_output(validation_status=status)
    assert out.validation_status is status
    restored = CoderOutput.from_json(out.to_json())
    assert restored.validation_status is status


def test_validation_status_string_values_match_names() -> None:
    """(e) The string value of each status equals its member name."""
    for status in ValidationStatus:
        assert status.value == status.name


def test_invalid_validation_status_string_rejected() -> None:
    """(e) A validation_status string outside the enum is rejected."""
    payload = json.loads(make_output().to_json())
    payload["validation_status"] = "SUCCESS"
    with pytest.raises(ValidationError):
        CoderOutput.from_json(json.dumps(payload))


def test_validation_status_has_exactly_four_members() -> None:
    """(e) The enum carries exactly the ID-066-mandated four members."""
    assert {s.name for s in ValidationStatus} == {
        "PASSED",
        "FAILED",
        "PENDING",
        "NOT_RUN",
    }


def test_validation_status_accepts_string_value() -> None:
    """(e) Constructing with the plain string value also works (StrEnum)."""
    out = make_output(validation_status="FAILED")
    assert out.validation_status is ValidationStatus.FAILED


# --- empty lists are permitted (no min_length policy) -----------------------


def test_empty_file_and_test_lists_permitted() -> None:
    """Schema imposes no min_length on list fields (policy is upstream)."""
    out = make_output(
        files_changed=[],
        tests_added_or_updated=[],
        validation_evidence=[],
        known_limitations=[],
    )
    assert out.files_changed == []
    assert out.tests_added_or_updated == []
    assert out.validation_evidence == []
    assert out.known_limitations == []


def test_malformed_json_rejected() -> None:
    """Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        CoderOutput.from_json("{not valid json")
