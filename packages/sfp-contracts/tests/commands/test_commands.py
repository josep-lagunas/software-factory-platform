"""Tests for the command contracts catalogue (MAS §5.3 / MAS §4.7 / SFP-219).

Covers the acceptance criteria:
- (a) all 8 commands (now payload classes) and the single ``CommandEnvelope``
  round-trip through ``to_json``/``from_json``;
- (b) envelope fields present (the 6 base routing fields + ``command_type`` +
  ``payload``);
- (c) unknown extra fields rejected on construction AND ``from_json``
  (``extra='forbid'``);
- (d) every required field raises ``ValidationError`` when dropped;
- (e) the 8 ``command_type`` values are exact (the enum is exactly those names);
- (g) payloads reject extra fields and missing fields;
- (h) ``occurred_at`` is typed ``str`` (no runtime-only ``datetime``);
- (i) malformed JSON is rejected.

SFP-219 reconciliation guards (anti-gaming):
- the ``command_type``/payload consistency validator is GONE — a
  mismatched-but-valid member is now ACCEPTED (deferred to serde, SFP-45);
- the old ``…Payload``-suffixed names are no longer importable;
- the private ``_Payload`` base is gone;
- ``EXPECTED_COMMAND_TYPE`` / ``_enforce_expected_command_type`` /
  ``@model_validator`` are absent from the envelope source.

Note: the envelope ``payload`` is typed ``Any`` (inherited from
:class:`~sfp_contracts.messages.MessageEnvelope`). A command's concrete payload
round-trips as DATA through JSON (reconstructing its concrete *type* is a
serde concern, SFP-45); round-trip is therefore asserted at the JSON level,
while construction keeps the typed payload instance for field-by-field checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import sfp_contracts.commands.envelope as envelope_module
from pydantic import ValidationError
from sfp_contracts.commands.envelope import CommandEnvelope, CommandType
from sfp_contracts.commands.payloads import (
    CancelCodingJob,
    CancelReviewJob,
    CommandPayload,
    ExecuteCodingJob,
    NotifyUser,
    RequestMerge,
    RequestUserInput,
    ReviewPullRequest,
    SynchronizePullRequest,
)

#: The 8 commands — their discriminant ``CommandType`` member, their payload
#: class (the concrete command name, SFP-219) and a minimal valid payload.
#: Driving every parametrized test from this table guarantees all 8 commands are
#: exercised identically.
COMMAND_SPECS: list[tuple[CommandType, type[CommandPayload], dict[str, Any]]] = [
    (
        CommandType.EXECUTE_CODING_JOB,
        ExecuteCodingJob,
        {"job_id": "job-1", "pr_spec_id": "sfp-10-a"},
    ),
    (
        CommandType.SYNCHRONIZE_PULL_REQUEST,
        SynchronizePullRequest,
        {"pr_number": 42, "repo": "josep-lagunas/sfp"},
    ),
    (CommandType.CANCEL_CODING_JOB, CancelCodingJob, {"job_id": "job-1", "reason": "superseded"}),
    (
        CommandType.REVIEW_PULL_REQUEST,
        ReviewPullRequest,
        {"pr_number": 42, "repo": "josep-lagunas/sfp"},
    ),
    (CommandType.CANCEL_REVIEW_JOB, CancelReviewJob, {"job_id": "rev-3", "reason": "stale"}),
    (
        CommandType.REQUEST_USER_INPUT,
        RequestUserInput,
        {"session_id": "s-1", "prompt": "Pick a branch"},
    ),
    (CommandType.NOTIFY_USER, NotifyUser, {"session_id": "s-1", "message": "Build failed"}),
    (CommandType.REQUEST_MERGE, RequestMerge, {"pr_number": 42, "repo": "josep-lagunas/sfp"}),
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

COMMAND_IDS = [spec[1].__name__ for spec in COMMAND_SPECS]

#: Fields with no default — dropping any of these must raise. ``command_type``
#: is now REQUIRED at construction (the per-message subclasses that defaulted it
#: are gone, SFP-219).
REQUIRED_FIELDS = [
    "message_id",
    "idempotency_key",
    "correlation_id",
    "causation_id",
    "occurred_at",
    "command_type",
    "payload",
]


def _full_kwargs(
    member: CommandType, payload_cls: type[CommandPayload], payload_kwargs: dict[str, Any]
) -> dict[str, Any]:
    return {**ENVELOPE, "command_type": member, "payload": payload_cls(**payload_kwargs)}


def _make_command(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
    **overrides: Any,
) -> CommandEnvelope:
    kwargs = _full_kwargs(member, payload_cls, payload_kwargs)
    kwargs.update(overrides)
    return CommandEnvelope(**kwargs)


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
# (a)(b)(c)(d)(i) per-command behaviour, parametrized over all 8 commands
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_round_trip_preserves_every_field(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(a) A conformant command round-trips through JSON losslessly."""
    original = _make_command(member, payload_cls, payload_kwargs)
    restored = CommandEnvelope.from_json(original.to_json())
    # payload returns untyped (the envelope payload is Any; reconstructing the
    # concrete payload type is a serde concern, SFP-45) — assert the JSON itself
    # round-trips losslessly, which exercises to_json AND from_json.
    assert restored.to_json() == original.to_json()


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_envelope_fields_present_and_correct(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(b) The envelope fields exist and carry the provided values."""
    command = _make_command(member, payload_cls, payload_kwargs)
    assert command.message_id == ENVELOPE["message_id"]
    assert command.idempotency_key == ENVELOPE["idempotency_key"]
    assert command.correlation_id == ENVELOPE["correlation_id"]
    assert command.causation_id == ENVELOPE["causation_id"]
    assert command.occurred_at == ENVELOPE["occurred_at"]
    assert command.command_type is member
    assert command.payload == payload_cls(**payload_kwargs)


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_extra_fields_rejected_on_construction(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(c) Unknown extra fields are rejected at construction."""
    with pytest.raises(ValidationError):
        _make_command(member, payload_cls, payload_kwargs, unexpected="x")


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_extra_fields_rejected_on_from_json(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(c) Unknown extra fields are rejected when deserializing."""
    command = _make_command(member, payload_cls, payload_kwargs)
    payload = json.loads(command.to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        CommandEnvelope.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_malformed_json_rejected(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(i) Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        CommandEnvelope.from_json("{not valid json")


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_missing_required_field_raises(
    field: str,
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(d) Dropping any required field (incl. command_type, payload) raises."""
    kwargs = {
        k: v for k, v in _full_kwargs(member, payload_cls, payload_kwargs).items() if k != field
    }
    with pytest.raises(ValidationError):
        CommandEnvelope(**kwargs)


# --------------------------------------------------------------------------- #
# (g) payloads reject extra / missing fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_payload_rejects_extra_fields(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(g) Every payload rejects unknown extra fields (extra='forbid')."""
    bad = dict(payload_kwargs)
    bad["bogus"] = "x"
    with pytest.raises(ValidationError):
        payload_cls(**bad)


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_payload_rejects_missing_field(
    member: CommandType,
    payload_cls: type[CommandPayload],
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


def test_invalid_command_type_string_rejected() -> None:
    """A ``command_type`` string outside the enum is rejected."""
    member, payload_cls, payload_kwargs = COMMAND_SPECS[0]
    command = _make_command(member, payload_cls, payload_kwargs)
    payload = json.loads(command.to_json())
    payload["command_type"] = "NOT_A_REAL_COMMAND"
    with pytest.raises(ValidationError):
        CommandEnvelope.from_json(json.dumps(payload))


def test_generic_envelope_round_trips_with_explicit_member() -> None:
    """The single CommandEnvelope round-trips with an explicit command_type."""
    member, payload_cls, payload_kwargs = COMMAND_SPECS[0]
    command = _make_command(member, payload_cls, payload_kwargs)
    assert command.command_type is member
    assert CommandEnvelope.from_json(command.to_json()).to_json() == command.to_json()


# --------------------------------------------------------------------------- #
# SFP-219 payload hierarchy + anti-gaming (validator gone, renames)
# --------------------------------------------------------------------------- #


def test_concrete_commands_are_command_payloads() -> None:
    """The concrete command names are :class:`CommandPayload` subclasses."""
    for _member, payload_cls, _kw in COMMAND_SPECS:
        assert issubclass(payload_cls, CommandPayload)


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_mismatched_command_type_now_accepted(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """The consistency validator is GONE (SFP-45): a different valid member is
    accepted — proves the per-message validator was truly removed."""
    other = _other_member(member)
    command = _make_command(member, payload_cls, payload_kwargs, command_type=other)
    assert command.command_type is other


def test_old_payload_suffix_names_not_importable() -> None:
    """Renamed (SFP-219): the old '…Payload'-suffixed names are gone."""
    import sfp_contracts.commands.payloads as payloads

    for stale in ("ExecuteCodingJobPayload", "RequestMergePayload", "NotifyUserPayload"):
        assert not hasattr(payloads, stale), f"{stale!r} should have been renamed"
    with pytest.raises(ImportError):
        from sfp_contracts.commands.payloads import ExecuteCodingJobPayload  # noqa: F401


def test_private_payload_base_is_gone() -> None:
    """Renamed (SFP-219): the private ``_Payload`` base is gone (-> CommandPayload)."""
    import sfp_contracts.commands.payloads as payloads

    assert not hasattr(payloads, "_Payload")
    with pytest.raises(ImportError):
        from sfp_contracts.commands.payloads import _Payload  # noqa: F401


def test_envelope_source_has_no_consistency_validator() -> None:
    """EXPECTED_COMMAND_TYPE / its validator / @model_validator are all gone."""
    source = Path(envelope_module.__file__).read_text()
    assert "EXPECTED_COMMAND_TYPE" not in source
    assert "_enforce_expected_command_type" not in source
    assert "model_validator" not in source
