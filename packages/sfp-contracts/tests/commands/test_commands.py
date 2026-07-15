"""Tests for the command contracts catalogue (SFP-38 / MAS §5.3 / ID-031).

Covers the acceptance criteria:
- (a) all 8 commands modelled and each round-trips through ``to_json``/``from_json``;
- (b) envelope fields present (``message_id``, ``idempotency_key``,
  ``correlation_id``, ``causation_id``, ``occurred_at``, ``payload``);
- (c) unknown extra fields rejected on construction AND ``from_json``
  (``extra='forbid'``);
- (d) every required field raises ``ValidationError`` when dropped;
- (e) the 8 ``command_type`` values are exact (the enum is exactly those names);
- (f) each command defaults its ``command_type`` to its own member, and a
  mismatched ``command_type`` is rejected;
- (g) payloads reject extra fields and missing fields;
- (h) ``occurred_at`` is typed ``str`` (no runtime-only ``datetime``);
- (i) malformed JSON is rejected.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.commands.envelope import CommandEnvelope, CommandType
from sfp_contracts.commands.models import (
    CancelCodingJob,
    CancelReviewJob,
    ExecuteCodingJob,
    NotifyUser,
    RequestMerge,
    RequestUserInput,
    ReviewPullRequest,
    SynchronizePullRequest,
)
from sfp_contracts.commands.payloads import (
    CancelCodingJobPayload,
    CancelReviewJobPayload,
    ExecuteCodingJobPayload,
    NotifyUserPayload,
    RequestMergePayload,
    RequestUserInputPayload,
    ReviewPullRequestPayload,
    SynchronizePullRequestPayload,
)

#: The 8 concrete commands, their discriminant ``CommandType`` member, their
#: payload class and a minimal valid payload. Driving every parametrized test
#: from this table guarantees all 8 commands are exercised identically.
COMMAND_SPECS: list[tuple[type[CommandEnvelope], CommandType, type[Any], dict[str, Any]]] = [
    (
        ExecuteCodingJob,
        CommandType.EXECUTE_CODING_JOB,
        ExecuteCodingJobPayload,
        {"job_id": "job-1", "pr_spec_id": "sfp-10-a"},
    ),
    (
        SynchronizePullRequest,
        CommandType.SYNCHRONIZE_PULL_REQUEST,
        SynchronizePullRequestPayload,
        {"pr_number": 42, "repo": "josep-lagunas/sfp"},
    ),
    (
        CancelCodingJob,
        CommandType.CANCEL_CODING_JOB,
        CancelCodingJobPayload,
        {"job_id": "job-1", "reason": "superseded"},
    ),
    (
        ReviewPullRequest,
        CommandType.REVIEW_PULL_REQUEST,
        ReviewPullRequestPayload,
        {"pr_number": 42, "repo": "josep-lagunas/sfp"},
    ),
    (
        CancelReviewJob,
        CommandType.CANCEL_REVIEW_JOB,
        CancelReviewJobPayload,
        {"job_id": "rev-3", "reason": "stale"},
    ),
    (
        RequestUserInput,
        CommandType.REQUEST_USER_INPUT,
        RequestUserInputPayload,
        {"session_id": "s-1", "prompt": "Pick a branch"},
    ),
    (
        NotifyUser,
        CommandType.NOTIFY_USER,
        NotifyUserPayload,
        {"session_id": "s-1", "message": "Build failed"},
    ),
    (
        RequestMerge,
        CommandType.REQUEST_MERGE,
        RequestMergePayload,
        {"pr_number": 42, "repo": "josep-lagunas/sfp"},
    ),
]

#: Common message-envelope metadata shared by every command (the 5 routing
#: fields — note this is the COMMAND envelope, distinct from the event envelope).
ENVELOPE: dict[str, Any] = {
    "message_id": "msg-0001",
    "idempotency_key": "idem-1",
    "correlation_id": "corr-1",
    "causation_id": "cause-1",
    "occurred_at": "2026-07-15T10:00:00Z",
}

#: The exact 8 names ID-031 fixes for ``CommandType`` (excludes the internal
#: ``GeneratePRSpecifications`` Orchestrator operation, MAS §5.3).
EXPECTED_COMMAND_TYPE_NAMES = frozenset(
    {
        "EXECUTE_CODING_JOB",
        "SYNCHRONIZE_PULL_REQUEST",
        "CANCEL_CODING_JOB",
        "REVIEW_PULL_REQUEST",
        "CANCEL_REVIEW_JOB",
        "REQUEST_USER_INPUT",
        "NOTIFY_USER",
        "REQUEST_MERGE",
    }
)

COMMAND_IDS = [spec[0].__name__ for spec in COMMAND_SPECS]

#: Fields with no default — dropping any of these must raise. ``command_type``
#: is excluded because each command defaults it to its own member.
REQUIRED_FIELDS = [
    "message_id",
    "idempotency_key",
    "correlation_id",
    "causation_id",
    "occurred_at",
    "payload",
]


def _full_kwargs(payload_cls: type[Any], payload_kwargs: dict[str, Any]) -> dict[str, Any]:
    return {**ENVELOPE, "payload": payload_cls(**payload_kwargs)}


def _make_command(
    cls: type[CommandEnvelope],
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
    **overrides: Any,
) -> CommandEnvelope:
    kwargs = _full_kwargs(payload_cls, payload_kwargs)
    kwargs.update(overrides)
    return cls(**kwargs)


def _other_member(member: CommandType) -> CommandType:
    """Return a ``CommandType`` member that is not ``member``."""
    for candidate in CommandType:
        if candidate != member:
            return candidate
    raise AssertionError("unreachable: CommandType has >1 member")


# --------------------------------------------------------------------------- #
# (e) the CommandType enum is exactly the 8 names
# --------------------------------------------------------------------------- #


def test_command_type_enum_has_exactly_eight_members() -> None:
    """(e) CommandType exposes precisely the 8 ID-031 command names."""
    assert len(CommandType) == 8
    assert {m.name for m in CommandType} == EXPECTED_COMMAND_TYPE_NAMES


def test_command_type_values_equal_member_names() -> None:
    """(e) Each member's string value equals its name (StrEnum)."""
    for member in CommandType:
        assert member.value == member.name


def test_command_type_excludes_generate_pr_specifications() -> None:
    """(e) The internal Orchestrator op is not an inter-agent command (MAS §5.3)."""
    assert "GENERATE_PR_SPECIFICATIONS" not in {m.name for m in CommandType}


# --------------------------------------------------------------------------- #
# (a)(b)(c)(d)(f) per-command behaviour, parametrized over all 8 commands
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_round_trip_preserves_every_field(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(a) A conformant command round-trips through JSON losslessly."""
    original = _make_command(cls, payload_cls, payload_kwargs)
    restored = cls.from_json(original.to_json())
    assert restored == original


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_envelope_fields_present_and_correct(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(b) The envelope fields exist and carry the provided values."""
    command = _make_command(cls, payload_cls, payload_kwargs)
    assert command.message_id == ENVELOPE["message_id"]
    assert command.idempotency_key == ENVELOPE["idempotency_key"]
    assert command.correlation_id == ENVELOPE["correlation_id"]
    assert command.causation_id == ENVELOPE["causation_id"]
    assert command.occurred_at == ENVELOPE["occurred_at"]
    assert command.command_type is member
    assert command.payload == payload_cls(**payload_kwargs)


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_default_command_type_matches_member(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(f) Omitting ``command_type`` defaults it to the command's own member."""
    kwargs = _full_kwargs(payload_cls, payload_kwargs)
    command = cls(**kwargs)
    assert command.command_type is member
    assert cls.from_json(command.to_json()).command_type is member


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_mismatched_command_type_rejected(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(f) A ``command_type`` from a different command is rejected at construction."""
    other = _other_member(member)
    with pytest.raises(ValidationError):
        _make_command(cls, payload_cls, payload_kwargs, command_type=other)


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_mismatched_command_type_rejected_on_from_json(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(f) A ``command_type`` mismatch is also caught on deserialization."""
    other = _other_member(member)
    payload = json.loads(_make_command(cls, payload_cls, payload_kwargs).to_json())
    payload["command_type"] = other.value
    with pytest.raises(ValidationError):
        cls.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_extra_fields_rejected_on_construction(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(c) Unknown extra fields are rejected at construction."""
    with pytest.raises(ValidationError):
        _make_command(cls, payload_cls, payload_kwargs, unexpected="x")


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_extra_fields_rejected_on_from_json(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(c) Unknown extra fields are rejected when deserializing."""
    payload = json.loads(_make_command(cls, payload_cls, payload_kwargs).to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        cls.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_malformed_json_rejected(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(i) Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        cls.from_json("{not valid json")


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_missing_required_field_raises(
    field: str,
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(d) Dropping any required field raises ValidationError."""
    kwargs = {k: v for k, v in _full_kwargs(payload_cls, payload_kwargs).items() if k != field}
    with pytest.raises(ValidationError):
        cls(**kwargs)


# --------------------------------------------------------------------------- #
# (g) payloads reject extra / missing fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_payload_rejects_extra_fields(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(g) Every payload rejects unknown extra fields (extra='forbid')."""
    bad = dict(payload_kwargs)
    bad["bogus"] = "x"
    with pytest.raises(ValidationError):
        payload_cls(**bad)


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    COMMAND_SPECS,
    ids=COMMAND_IDS,
)
def test_payload_rejects_missing_field(
    cls: type[CommandEnvelope],
    member: CommandType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(g) Dropping any payload field raises ValidationError."""
    key = next(iter(payload_kwargs))
    bad = {k: v for k, v in payload_kwargs.items() if k != key}
    with pytest.raises(ValidationError):
        payload_cls(**bad)


# --------------------------------------------------------------------------- #
# (h) occurred_at is str, not datetime; base envelope sanity
# --------------------------------------------------------------------------- #


def test_occurred_at_is_typed_str() -> None:
    """(h) ``occurred_at`` is annotated ``str`` to keep round-trips deterministic."""
    assert CommandEnvelope.model_fields["occurred_at"].annotation is str


def test_generic_envelope_accepts_any_command_type() -> None:
    """The base envelope no-ops the command_type validator (EXPECTED=None)."""
    envelope = CommandEnvelope(
        message_id="msg-base",
        idempotency_key="idem-base",
        correlation_id="corr-base",
        causation_id="cause-base",
        occurred_at="2026-07-15T00:00:00Z",
        command_type=CommandType.EXECUTE_CODING_JOB,
    )
    assert envelope.command_type is CommandType.EXECUTE_CODING_JOB
    # Round-trips through JSON.
    assert CommandEnvelope.from_json(envelope.to_json()) == envelope


def test_invalid_command_type_string_rejected() -> None:
    """A ``command_type`` string outside the enum is rejected."""
    cls, _member, payload_cls, payload_kwargs = COMMAND_SPECS[0]  # ExecuteCodingJob
    payload = json.loads(_make_command(cls, payload_cls, payload_kwargs).to_json())
    payload["command_type"] = "NOT_A_REAL_COMMAND"
    with pytest.raises(ValidationError):
        cls.from_json(json.dumps(payload))
