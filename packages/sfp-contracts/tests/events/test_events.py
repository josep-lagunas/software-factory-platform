"""Tests for the event contracts catalogue (SFP-39 / MAS §5.4 / ID-031).

Covers the acceptance criteria:
- (a) all 11 events modelled and each round-trips through ``to_json``/``from_json``;
- (b) envelope fields present (``event_id``, ``occurred_at``, ``producer``,
  ``event_type``, ``payload``);
- (c) unknown extra fields rejected on construction AND ``from_json``
  (``extra='forbid'``);
- (d) every required field raises ``ValidationError`` when dropped;
- (e) the 11 ``event_type`` values are exact (the enum is exactly those names);
- (f) each event defaults its ``event_type`` to its own member, and a mismatched
  ``event_type`` is rejected;
- (g) payloads reject extra fields and missing fields;
- (h) ``occurred_at`` is typed ``str`` (no runtime-only ``datetime``);
- (i) malformed JSON is rejected.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_contracts.events.models import (
    CodingJobUpdated,
    DeploymentUpdated,
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
from sfp_contracts.events.payloads import (
    CodingJobUpdatedPayload,
    DeploymentUpdatedPayload,
    ExternalEventReceivedPayload,
    MergeUpdatedPayload,
    PRSpecificationsUpdatedPayload,
    ReviewUpdatedPayload,
    TicketUpdatedPayload,
    UserInputReceivedPayload,
    UserInteractionUpdatedPayload,
    UserQueryReceivedPayload,
    WorkflowUpdatedPayload,
)

#: The 11 concrete events, their discriminant ``EventType`` member, their payload
#: class and a minimal valid payload. Driving every parametrized test from this
#: table guarantees all 11 events are exercised identically.
EVENT_SPECS: list[tuple[type[EventEnvelope], EventType, type[Any], dict[str, Any]]] = [
    (
        ExternalEventReceived,
        EventType.EXTERNAL_EVENT_RECEIVED,
        ExternalEventReceivedPayload,
        {"source": "github", "external_id": "wh-9"},
    ),
    (
        TicketUpdated,
        EventType.TICKET_UPDATED,
        TicketUpdatedPayload,
        {"ticket_id": "SFP-39", "status": "In Progress"},
    ),
    (
        PRSpecificationsUpdated,
        EventType.PR_SPECIFICATIONS_UPDATED,
        PRSpecificationsUpdatedPayload,
        {"pr_spec_id": "sfp-10-a", "change": "created"},
    ),
    (
        CodingJobUpdated,
        EventType.CODING_JOB_UPDATED,
        CodingJobUpdatedPayload,
        {"job_id": "job-7", "status": "running"},
    ),
    (
        ReviewUpdated,
        EventType.REVIEW_UPDATED,
        ReviewUpdatedPayload,
        {"pr_number": 42, "review_status": "APPROVED"},
    ),
    (
        UserInputReceived,
        EventType.USER_INPUT_RECEIVED,
        UserInputReceivedPayload,
        {"session_id": "s-1", "text": "hello"},
    ),
    (
        UserInteractionUpdated,
        EventType.USER_INTERACTION_UPDATED,
        UserInteractionUpdatedPayload,
        {"session_id": "s-1", "state": "awaiting-choice"},
    ),
    (
        UserQueryReceived,
        EventType.USER_QUERY_RECEIVED,
        UserQueryReceivedPayload,
        {"session_id": "s-1", "query": "what next?"},
    ),
    (
        MergeUpdated,
        EventType.MERGE_UPDATED,
        MergeUpdatedPayload,
        {"pr_number": 42, "merge_status": "merged"},
    ),
    (
        DeploymentUpdated,
        EventType.DEPLOYMENT_UPDATED,
        DeploymentUpdatedPayload,
        {"deployment_id": "dep-3", "status": "deployed"},
    ),
    (
        WorkflowUpdated,
        EventType.WORKFLOW_UPDATED,
        WorkflowUpdatedPayload,
        {"workflow_id": "wf-1", "status": "completed"},
    ),
]

#: Common envelope metadata shared by every event.
ENVELOPE: dict[str, Any] = {
    "event_id": "evt-0001",
    "occurred_at": "2026-07-15T10:00:00Z",
    "producer": "orchestrator",
}

#: The exact 11 names ID-031 fixes for ``EventType``.
EXPECTED_EVENT_TYPE_NAMES = frozenset(
    {
        "EXTERNAL_EVENT_RECEIVED",
        "TICKET_UPDATED",
        "PR_SPECIFICATIONS_UPDATED",
        "CODING_JOB_UPDATED",
        "REVIEW_UPDATED",
        "USER_INPUT_RECEIVED",
        "USER_INTERACTION_UPDATED",
        "USER_QUERY_RECEIVED",
        "MERGE_UPDATED",
        "DEPLOYMENT_UPDATED",
        "WORKFLOW_UPDATED",
    }
)

EVENT_IDS = [spec[0].__name__ for spec in EVENT_SPECS]

#: Fields with no default — dropping any of these must raise. ``event_type`` is
#: excluded because each event defaults it to its own member.
REQUIRED_FIELDS = ["event_id", "occurred_at", "producer", "payload"]


def _full_kwargs(payload_cls: type[Any], payload_kwargs: dict[str, Any]) -> dict[str, Any]:
    return {**ENVELOPE, "payload": payload_cls(**payload_kwargs)}


def _make_event(
    cls: type[EventEnvelope],
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
    **overrides: Any,
) -> EventEnvelope:
    kwargs = _full_kwargs(payload_cls, payload_kwargs)
    kwargs.update(overrides)
    return cls(**kwargs)


def _other_member(member: EventType) -> EventType:
    """Return an ``EventType`` member that is not ``member``."""
    for candidate in EventType:
        if candidate != member:
            return candidate
    raise AssertionError("unreachable: EventType has >1 member")


# --------------------------------------------------------------------------- #
# (e) the EventType enum is exactly the 11 names
# --------------------------------------------------------------------------- #


def test_event_type_enum_has_exactly_eleven_members() -> None:
    """(e) EventType exposes precisely the 11 ID-031 event names."""
    assert len(EventType) == 11
    assert {m.name for m in EventType} == EXPECTED_EVENT_TYPE_NAMES


def test_event_type_values_equal_member_names() -> None:
    """(e) Each member's string value equals its name (StrEnum)."""
    for member in EventType:
        assert member.value == member.name


# --------------------------------------------------------------------------- #
# (a)(b)(c)(d)(f) per-event behaviour, parametrized over all 11 events
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_round_trip_preserves_every_field(
    cls: type[EventEnvelope],
    member: EventType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(a) A conformant event round-trips through JSON losslessly."""
    original = _make_event(cls, payload_cls, payload_kwargs)
    restored = cls.from_json(original.to_json())
    assert restored == original


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_envelope_fields_present_and_correct(
    cls: type[EventEnvelope],
    member: EventType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(b) The envelope fields exist and carry the provided values."""
    event = _make_event(cls, payload_cls, payload_kwargs)
    assert event.event_id == ENVELOPE["event_id"]
    assert event.occurred_at == ENVELOPE["occurred_at"]
    assert event.producer == ENVELOPE["producer"]
    assert event.event_type is member
    assert event.payload == payload_cls(**payload_kwargs)


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_default_event_type_matches_member(
    cls: type[EventEnvelope],
    member: EventType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(f) Omitting ``event_type`` defaults it to the event's own member."""
    kwargs = _full_kwargs(payload_cls, payload_kwargs)
    event = cls(**kwargs)
    assert event.event_type is member
    assert cls.from_json(event.to_json()).event_type is member


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_mismatched_event_type_rejected(
    cls: type[EventEnvelope],
    member: EventType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(f) An ``event_type`` from a different event is rejected at construction."""
    other = _other_member(member)
    with pytest.raises(ValidationError):
        _make_event(cls, payload_cls, payload_kwargs, event_type=other)


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_mismatched_event_type_rejected_on_from_json(
    cls: type[EventEnvelope],
    member: EventType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(f) An ``event_type`` mismatch is also caught on deserialization."""
    other = _other_member(member)
    payload = json.loads(_make_event(cls, payload_cls, payload_kwargs).to_json())
    payload["event_type"] = other.value
    with pytest.raises(ValidationError):
        cls.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_extra_fields_rejected_on_construction(
    cls: type[EventEnvelope],
    member: EventType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(c) Unknown extra fields are rejected at construction."""
    with pytest.raises(ValidationError):
        _make_event(cls, payload_cls, payload_kwargs, unexpected="x")


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_extra_fields_rejected_on_from_json(
    cls: type[EventEnvelope],
    member: EventType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(c) Unknown extra fields are rejected when deserializing."""
    payload = json.loads(_make_event(cls, payload_cls, payload_kwargs).to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        cls.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_malformed_json_rejected(
    cls: type[EventEnvelope],
    member: EventType,
    payload_cls: type[Any],
    payload_kwargs: dict[str, Any],
) -> None:
    """(i) Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        cls.from_json("{not valid json")


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
@pytest.mark.parametrize(
    ("cls", "member", "payload_cls", "payload_kwargs"),
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_missing_required_field_raises(
    field: str,
    cls: type[EventEnvelope],
    member: EventType,
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
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_payload_rejects_extra_fields(
    cls: type[EventEnvelope],
    member: EventType,
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
    EVENT_SPECS,
    ids=EVENT_IDS,
)
def test_payload_rejects_missing_field(
    cls: type[EventEnvelope],
    member: EventType,
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
    assert EventEnvelope.model_fields["occurred_at"].annotation is str


def test_generic_envelope_accepts_any_event_type() -> None:
    """The base envelope no-ops the event_type validator (EXPECTED_EVENT_TYPE=None)."""
    envelope = EventEnvelope(
        event_id="evt-base",
        occurred_at="2026-07-15T00:00:00Z",
        producer="external-events",
        event_type=EventType.EXTERNAL_EVENT_RECEIVED,
    )
    assert envelope.event_type is EventType.EXTERNAL_EVENT_RECEIVED
    # Round-trips through JSON.
    assert EventEnvelope.from_json(envelope.to_json()) == envelope


def test_invalid_event_type_string_rejected() -> None:
    """An ``event_type`` string outside the enum is rejected."""
    cls, _member, payload_cls, payload_kwargs = EVENT_SPECS[1]  # TicketUpdated
    payload = json.loads(_make_event(cls, payload_cls, payload_kwargs).to_json())
    payload["event_type"] = "NOT_A_REAL_EVENT"
    with pytest.raises(ValidationError):
        cls.from_json(json.dumps(payload))
