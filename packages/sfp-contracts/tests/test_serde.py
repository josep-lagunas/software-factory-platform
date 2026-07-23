"""Tests for the discriminated envelope serde — typed JSON round-trip (SFP-45).

Exercises :mod:`sfp_contracts.serde` (:func:`load_command` / :func:`load_event`),
the opt-in typed path that closes SFP-219's ``payload: Any`` gap. The existing
``from_json`` classmethods stay generic (returning dict payloads); this module
proves the *typed* rebuild carries concrete payload instances, not dicts.

Test cases (mirroring the SFP-45 Test Designer):
- (TC-01) round-trip ALL 8 commands via ``to_json`` -> ``load_command`` with
  field-by-field equality on envelope metadata + payload (NOT overall ``==``);
- (TC-02) round-trip ALL 11 events via ``to_json`` -> ``load_event`` likewise;
- (TC-03) discriminator->class identity (``payload.__class__ is ExpectedClass``)
  for every mapping entry (8 + 11);
- (TC-04) the acronym edge case: ``PR_SPECIFICATIONS_UPDATED`` resolves to
  ``PRSpecificationsUpdated`` (capital ``PR``), NOT ``PrSpecificationsUpdated``;
- (TC-05) an unknown discriminator string -> :class:`pydantic.ValidationError`;
- (TC-06) a missing discriminator key -> :class:`pydantic.ValidationError`;
- (TC-07) ``extra='forbid'`` preserved at BOTH envelope level AND inside the
  payload sub-document (the latter only fires because the concrete class — not
  the envelope's ``Any`` — validates the payload);
- (TC-08) ``bytes`` input accepted;
- (TC-09) ANTI-GAMING: the round-tripped payload is an instance of the concrete
  class AND not a ``dict`` (closes SFP-219's ``payload: Any`` gap);
- (TC-10) NO-ORPHAN: the discriminator->class maps cover exactly the enum
  members (8 commands + 11 events) and the value-sets match the concrete classes;
- (TC-E3) wrong-shape payload (valid discriminator, payload missing a required
  field) -> :class:`pydantic.ValidationError` (falsifies dict-passthrough);
- (TC-E5) NON-INVASIVE regression guard: ``CommandEnvelope.from_json`` /
  ``EventEnvelope.from_json`` STILL return dict payloads (proves ``from_json``
  was not overridden);
- (TC-E6) malformed JSON -> :class:`pydantic.ValidationError`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
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
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_contracts.events.payloads import (
    CodingJobUpdated,
    DeploymentUpdated,
    EventPayload,
    ExternalEventReceived,
    MergeUpdated,
    PRSpecificationsUpdated,
    ReviewUpdated,
    TicketUpdated,
    UserInputReceived,
    UserInteractionUpdated,
    UserQueryReceived,
    WorkflowUpdated,
)
from sfp_contracts.serde import (
    _COMMAND_PAYLOAD_MAP,
    _EVENT_PAYLOAD_MAP,
    load_command,
    load_event,
)

#: The 8 commands — discriminator member, concrete payload class, minimal valid
#: payload kwargs. Driving every parametrized test from this table guarantees all
#: 8 commands are exercised identically (mirrors tests/commands/test_commands.py).
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

#: The 11 events — discriminator member, concrete payload class, minimal valid
#: payload kwargs (mirrors tests/events/test_events.py).
EVENT_SPECS: list[tuple[EventType, type[EventPayload], dict[str, Any]]] = [
    (
        EventType.EXTERNAL_EVENT_RECEIVED,
        ExternalEventReceived,
        {"source": "github", "external_id": "wh-9"},
    ),
    (EventType.TICKET_UPDATED, TicketUpdated, {"ticket_id": "SFP-45", "status": "In Progress"}),
    (
        EventType.PR_SPECIFICATIONS_UPDATED,
        PRSpecificationsUpdated,
        {"pr_spec_id": "sfp-10-a", "change": "created"},
    ),
    (EventType.CODING_JOB_UPDATED, CodingJobUpdated, {"job_id": "job-7", "status": "running"}),
    (EventType.REVIEW_UPDATED, ReviewUpdated, {"pr_number": 42, "review_status": "APPROVED"}),
    (EventType.USER_INPUT_RECEIVED, UserInputReceived, {"session_id": "s-1", "text": "hello"}),
    (
        EventType.USER_INTERACTION_UPDATED,
        UserInteractionUpdated,
        {"session_id": "s-1", "state": "awaiting-choice"},
    ),
    (
        EventType.USER_QUERY_RECEIVED,
        UserQueryReceived,
        {"session_id": "s-1", "query": "what next?"},
    ),
    (EventType.MERGE_UPDATED, MergeUpdated, {"pr_number": 42, "merge_status": "merged"}),
    (
        EventType.DEPLOYMENT_UPDATED,
        DeploymentUpdated,
        {"deployment_id": "dep-3", "status": "deployed"},
    ),
    (EventType.WORKFLOW_UPDATED, WorkflowUpdated, {"workflow_id": "wf-1", "status": "completed"}),
]

#: Common command-envelope metadata (no ``producer`` — commands have no issuer
#: field; identity is runtime policy, ID-072).
COMMAND_ENVELOPE: dict[str, Any] = {
    "message_id": "msg-0001",
    "idempotency_key": "idem-1",
    "correlation_id": "corr-1",
    "causation_id": "cause-1",
    "occurred_at": "2026-07-15T10:00:00Z",
}

#: Common event-envelope metadata (includes ``producer``).
EVENT_ENVELOPE: dict[str, Any] = {
    "message_id": "evt-0001",
    "idempotency_key": "idem-1",
    "correlation_id": "corr-1",
    "causation_id": "cause-1",
    "occurred_at": "2026-07-15T10:00:00Z",
    "producer": "orchestrator",
}

COMMAND_IDS = [spec[1].__name__ for spec in COMMAND_SPECS]
EVENT_IDS = [spec[1].__name__ for spec in EVENT_SPECS]


def _make_command(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
    **overrides: Any,
) -> CommandEnvelope:
    kwargs: dict[str, Any] = {
        **COMMAND_ENVELOPE,
        "command_type": member,
        "payload": payload_cls(**payload_kwargs),
    }
    kwargs.update(overrides)
    return CommandEnvelope(**kwargs)


def _make_event(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
    **overrides: Any,
) -> EventEnvelope:
    kwargs: dict[str, Any] = {
        **EVENT_ENVELOPE,
        "event_type": member,
        "payload": payload_cls(**payload_kwargs),
    }
    kwargs.update(overrides)
    return EventEnvelope(**kwargs)


# --------------------------------------------------------------------------- #
# (TC-01) round-trip ALL 8 commands — field-by-field equality (not overall ==)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_command_round_trip_field_by_field(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(TC-01) ``to_json`` -> ``load_command`` preserves every field, typed."""
    original = _make_command(member, payload_cls, payload_kwargs)
    loaded = load_command(original.to_json())
    assert isinstance(loaded, CommandEnvelope)
    # Envelope metadata, field-by-field (deliberately NOT ``loaded == original``).
    assert loaded.message_id == COMMAND_ENVELOPE["message_id"]
    assert loaded.idempotency_key == COMMAND_ENVELOPE["idempotency_key"]
    assert loaded.correlation_id == COMMAND_ENVELOPE["correlation_id"]
    assert loaded.causation_id == COMMAND_ENVELOPE["causation_id"]
    assert loaded.occurred_at == COMMAND_ENVELOPE["occurred_at"]
    assert loaded.command_type is member
    # Payload value equality (field-by-field under the hood for pydantic models).
    assert loaded.payload == payload_cls(**payload_kwargs)


# --------------------------------------------------------------------------- #
# (TC-02) round-trip ALL 11 events — field-by-field equality (not overall ==)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_event_round_trip_field_by_field(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(TC-02) ``to_json`` -> ``load_event`` preserves every field, typed."""
    original = _make_event(member, payload_cls, payload_kwargs)
    loaded = load_event(original.to_json())
    assert isinstance(loaded, EventEnvelope)
    assert loaded.message_id == EVENT_ENVELOPE["message_id"]
    assert loaded.idempotency_key == EVENT_ENVELOPE["idempotency_key"]
    assert loaded.correlation_id == EVENT_ENVELOPE["correlation_id"]
    assert loaded.causation_id == EVENT_ENVELOPE["causation_id"]
    assert loaded.occurred_at == EVENT_ENVELOPE["occurred_at"]
    assert loaded.producer == EVENT_ENVELOPE["producer"]
    assert loaded.event_type is member
    assert loaded.payload == payload_cls(**payload_kwargs)


# --------------------------------------------------------------------------- #
# (TC-03) discriminator -> class identity for every mapping entry (8 + 11)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_command_discriminator_resolves_to_exact_class(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(TC-03) ``load_command`` resolves the discriminator to the exact class."""
    command = _make_command(member, payload_cls, payload_kwargs)
    loaded = load_command(command.to_json())
    assert loaded.payload.__class__ is payload_cls


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_event_discriminator_resolves_to_exact_class(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(TC-03) ``load_event`` resolves the discriminator to the exact class."""
    event = _make_event(member, payload_cls, payload_kwargs)
    loaded = load_event(event.to_json())
    assert loaded.payload.__class__ is payload_cls


# --------------------------------------------------------------------------- #
# (TC-04) acronym edge case: PR_SPECIFICATIONS_UPDATED -> PRSpecificationsUpdated
# --------------------------------------------------------------------------- #


def test_pr_specifications_updated_acronym_is_capital_pr() -> None:
    """(TC-04) The acronym resolves to ``PRSpecificationsUpdated`` (capital PR),
    NOT ``PrSpecificationsUpdated`` — the explicit dict literal avoids the
    SCREAMING_SNAKE->title-case pitfall (R2)."""
    event = _make_event(
        EventType.PR_SPECIFICATIONS_UPDATED,
        PRSpecificationsUpdated,
        {"pr_spec_id": "sfp-10-a", "change": "created"},
    )
    loaded = load_event(event.to_json())
    assert isinstance(loaded.payload, PRSpecificationsUpdated)
    assert loaded.payload.__class__.__name__ == "PRSpecificationsUpdated"
    assert loaded.payload.__class__.__name__ != "PrSpecificationsUpdated"


# --------------------------------------------------------------------------- #
# (TC-05) unknown discriminator -> ValidationError
# --------------------------------------------------------------------------- #


def test_unknown_command_discriminator_rejected() -> None:
    """(TC-05) An unknown ``command_type`` string raises ValidationError."""
    member, payload_cls, payload_kwargs = COMMAND_SPECS[0]
    data = json.loads(_make_command(member, payload_cls, payload_kwargs).to_json())
    data["command_type"] = "NOT_A_REAL_COMMAND"
    with pytest.raises(ValidationError):
        load_command(json.dumps(data))


def test_unknown_event_discriminator_rejected() -> None:
    """(TC-05) An unknown ``event_type`` string raises ValidationError."""
    member, payload_cls, payload_kwargs = EVENT_SPECS[0]
    data = json.loads(_make_event(member, payload_cls, payload_kwargs).to_json())
    data["event_type"] = "NOT_A_REAL_EVENT"
    with pytest.raises(ValidationError):
        load_event(json.dumps(data))


# --------------------------------------------------------------------------- #
# (TC-06) missing discriminator key -> ValidationError
# --------------------------------------------------------------------------- #


def test_missing_command_discriminator_rejected() -> None:
    """(TC-06) A missing ``command_type`` key raises ValidationError."""
    member, payload_cls, payload_kwargs = COMMAND_SPECS[0]
    data = json.loads(_make_command(member, payload_cls, payload_kwargs).to_json())
    del data["command_type"]
    with pytest.raises(ValidationError):
        load_command(json.dumps(data))


def test_missing_event_discriminator_rejected() -> None:
    """(TC-06) A missing ``event_type`` key raises ValidationError."""
    member, payload_cls, payload_kwargs = EVENT_SPECS[0]
    data = json.loads(_make_event(member, payload_cls, payload_kwargs).to_json())
    del data["event_type"]
    with pytest.raises(ValidationError):
        load_event(json.dumps(data))


# --------------------------------------------------------------------------- #
# (TC-07) extra='forbid' preserved — envelope level AND payload sub-document
# --------------------------------------------------------------------------- #


def test_command_envelope_level_extra_rejected() -> None:
    """(TC-07) An unknown field at envelope level raises ValidationError."""
    member, payload_cls, payload_kwargs = COMMAND_SPECS[0]
    data = json.loads(_make_command(member, payload_cls, payload_kwargs).to_json())
    data["unexpected"] = "x"
    with pytest.raises(ValidationError):
        load_command(json.dumps(data))


def test_command_payload_subdoc_extra_rejected() -> None:
    """(TC-07) An unknown field INSIDE the payload sub-document raises
    ValidationError — only fires because the concrete class (not the envelope's
    Any) validates the payload."""
    member, payload_cls, payload_kwargs = COMMAND_SPECS[0]
    data = json.loads(_make_command(member, payload_cls, payload_kwargs).to_json())
    data["payload"]["bogus"] = "x"
    with pytest.raises(ValidationError):
        load_command(json.dumps(data))


def test_event_envelope_level_extra_rejected() -> None:
    """(TC-07) An unknown field at envelope level raises ValidationError."""
    member, payload_cls, payload_kwargs = EVENT_SPECS[0]
    data = json.loads(_make_event(member, payload_cls, payload_kwargs).to_json())
    data["unexpected"] = "x"
    with pytest.raises(ValidationError):
        load_event(json.dumps(data))


def test_event_payload_subdoc_extra_rejected() -> None:
    """(TC-07) An unknown field INSIDE the payload sub-document raises
    ValidationError (concrete-class validation, not dict-passthrough)."""
    member, payload_cls, payload_kwargs = EVENT_SPECS[0]
    data = json.loads(_make_event(member, payload_cls, payload_kwargs).to_json())
    data["payload"]["bogus"] = "x"
    with pytest.raises(ValidationError):
        load_event(json.dumps(data))


# --------------------------------------------------------------------------- #
# (TC-08) bytes input accepted
# --------------------------------------------------------------------------- #


def test_command_bytes_input_accepted() -> None:
    """(TC-08) ``load_command`` accepts ``bytes`` input."""
    member, payload_cls, payload_kwargs = COMMAND_SPECS[0]
    command = _make_command(member, payload_cls, payload_kwargs)
    loaded = load_command(command.to_json().encode("utf-8"))
    assert isinstance(loaded.payload, payload_cls)


def test_event_bytes_input_accepted() -> None:
    """(TC-08) ``load_event`` accepts ``bytes`` input."""
    member, payload_cls, payload_kwargs = EVENT_SPECS[0]
    event = _make_event(member, payload_cls, payload_kwargs)
    loaded = load_event(event.to_json().encode("utf-8"))
    assert isinstance(loaded.payload, payload_cls)


# --------------------------------------------------------------------------- #
# (TC-09) ANTI-GAMING: payload is the concrete class, NOT a dict (all 19)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("member", "payload_cls", "payload_kwargs"), COMMAND_SPECS, ids=COMMAND_IDS
)
def test_command_payload_is_typed_instance_not_dict(
    member: CommandType,
    payload_cls: type[CommandPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(TC-09) The round-tripped payload is a concrete instance, not a dict."""
    command = _make_command(member, payload_cls, payload_kwargs)
    loaded = load_command(command.to_json())
    assert isinstance(loaded.payload, payload_cls)
    assert not isinstance(loaded.payload, dict)


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_event_payload_is_typed_instance_not_dict(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(TC-09) The round-tripped payload is a concrete instance, not a dict."""
    event = _make_event(member, payload_cls, payload_kwargs)
    loaded = load_event(event.to_json())
    assert isinstance(loaded.payload, payload_cls)
    assert not isinstance(loaded.payload, dict)


# --------------------------------------------------------------------------- #
# (TC-10) NO-ORPHAN: maps cover exactly the enums + value-sets match classes
# --------------------------------------------------------------------------- #


def test_command_payload_map_covers_every_command_type() -> None:
    """(TC-10) The command map has exactly the 8 CommandType members and its
    value-set matches the concrete payload classes (no orphan either way)."""
    assert set(_COMMAND_PAYLOAD_MAP) == set(CommandType)
    assert len(_COMMAND_PAYLOAD_MAP) == 8
    expected_classes = {spec[1] for spec in COMMAND_SPECS}
    assert set(_COMMAND_PAYLOAD_MAP.values()) == expected_classes
    for cls in _COMMAND_PAYLOAD_MAP.values():
        assert issubclass(cls, CommandPayload)


def test_event_payload_map_covers_every_event_type() -> None:
    """(TC-10) The event map has exactly the 11 EventType members and its
    value-set matches the concrete payload classes (no orphan either way)."""
    assert set(_EVENT_PAYLOAD_MAP) == set(EventType)
    assert len(_EVENT_PAYLOAD_MAP) == 11
    expected_classes = {spec[1] for spec in EVENT_SPECS}
    assert set(_EVENT_PAYLOAD_MAP.values()) == expected_classes
    for cls in _EVENT_PAYLOAD_MAP.values():
        assert issubclass(cls, EventPayload)


# --------------------------------------------------------------------------- #
# (TC-E3) wrong-shape payload (valid discriminator, missing required field)
# --------------------------------------------------------------------------- #


def test_command_wrong_shape_payload_rejected() -> None:
    """(TC-E3) A payload sub-document missing a required field raises
    ValidationError — falsifies dict-passthrough (only the concrete class
    enforces required fields)."""
    member, payload_cls, payload_kwargs = COMMAND_SPECS[0]
    data = json.loads(_make_command(member, payload_cls, payload_kwargs).to_json())
    del data["payload"][next(iter(payload_kwargs))]
    with pytest.raises(ValidationError):
        load_command(json.dumps(data))


def test_event_wrong_shape_payload_rejected() -> None:
    """(TC-E3) A payload sub-document missing a required field raises
    ValidationError (concrete-class validation, not dict-passthrough)."""
    member, payload_cls, payload_kwargs = EVENT_SPECS[0]
    data = json.loads(_make_event(member, payload_cls, payload_kwargs).to_json())
    del data["payload"][next(iter(payload_kwargs))]
    with pytest.raises(ValidationError):
        load_event(json.dumps(data))


# --------------------------------------------------------------------------- #
# (TC-E5) NON-INVASIVE regression guard: from_json STILL returns dict payloads
# --------------------------------------------------------------------------- #


def test_command_from_json_still_returns_dict_payload() -> None:
    """(TC-E5) ``CommandEnvelope.from_json`` is untouched — payload is still a
    dict (proves the typed path is opt-in via ``serde.load_command`` only)."""
    member, payload_cls, payload_kwargs = COMMAND_SPECS[0]
    command = _make_command(member, payload_cls, payload_kwargs)
    restored = CommandEnvelope.from_json(command.to_json())
    assert isinstance(restored.payload, dict)
    assert not isinstance(restored.payload, payload_cls)


def test_event_from_json_still_returns_dict_payload() -> None:
    """(TC-E5) ``EventEnvelope.from_json`` is untouched — payload is still a
    dict (proves the typed path is opt-in via ``serde.load_event`` only)."""
    member, payload_cls, payload_kwargs = EVENT_SPECS[0]
    event = _make_event(member, payload_cls, payload_kwargs)
    restored = EventEnvelope.from_json(event.to_json())
    assert isinstance(restored.payload, dict)
    assert not isinstance(restored.payload, payload_cls)


# --------------------------------------------------------------------------- #
# (TC-E6) malformed JSON -> ValidationError
# --------------------------------------------------------------------------- #


def test_command_malformed_json_rejected() -> None:
    """(TC-E6) Malformed JSON raises ValidationError (pydantic wraps JSON-decode)."""
    with pytest.raises(ValidationError):
        load_command("{not valid json")


def test_event_malformed_json_rejected() -> None:
    """(TC-E6) Malformed JSON raises ValidationError (pydantic wraps JSON-decode)."""
    with pytest.raises(ValidationError):
        load_event("{not valid json")
