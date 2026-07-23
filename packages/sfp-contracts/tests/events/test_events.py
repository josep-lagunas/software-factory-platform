"""Tests for the event contracts catalogue (MAS §5.4 / MAS §4.7 / SFP-219).

Covers the acceptance criteria:
- (a) all 11 events (now payload classes) and the single ``EventEnvelope``
  round-trip through ``to_json``/``from_json``;
- (b) envelope fields present (``message_id`` — renamed from ``event_id`` — the
  gained ``idempotency_key``/``correlation_id``/``causation_id``, ``occurred_at``,
  ``producer``, ``event_type``, ``payload``);
- (c) unknown extra fields rejected on construction AND ``from_json``
  (``extra='forbid'``);
- (d) every required field raises ``ValidationError`` when dropped;
- (e) the 11 ``event_type`` values are exact (the enum is exactly those names);
- (g) payloads reject extra fields and missing fields;
- (h) ``occurred_at`` is typed ``str`` (no runtime-only ``datetime``);
- (i) malformed JSON is rejected.

SFP-219 reconciliation guards (anti-gaming):
- the ``event_type``/payload consistency validator is GONE — a mismatched-but-
  valid member is now ACCEPTED (deferred to serde, SFP-45);
- ``event_id`` is GONE (renamed to ``message_id``): not in model_fields, not an
  attribute, and rejected on construction (extra='forbid');
- the old ``…Payload``-suffixed names are no longer importable;
- the private ``_Payload`` base is gone;
- ``EXPECTED_EVENT_TYPE`` / ``_enforce_expected_event_type`` /
  ``@model_validator`` are absent from the envelope source.

Note: the envelope ``payload`` is typed ``Any`` (inherited from
:class:`~sfp_contracts.messages.MessageEnvelope`). An event's concrete payload
round-trips as DATA through JSON (reconstructing its concrete *type* is a
serde concern, SFP-45); round-trip is therefore asserted at the JSON level,
while construction keeps the typed payload instance for field-by-field checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import sfp_contracts.events.envelope as envelope_module
from pydantic import ValidationError
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

#: The 11 events — their discriminant ``EventType`` member, their payload class
#: (the concrete event name, SFP-219) and a minimal valid payload.
EVENT_SPECS: list[tuple[EventType, type[EventPayload], dict[str, Any]]] = [
    (
        EventType.EXTERNAL_EVENT_RECEIVED,
        ExternalEventReceived,
        {"source": "github", "external_id": "wh-9"},
    ),
    (EventType.TICKET_UPDATED, TicketUpdated, {"ticket_id": "SFP-39", "status": "In Progress"}),
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

#: Common envelope metadata shared by every event (full routing set, SFP-219).
ENVELOPE: dict[str, Any] = {
    "message_id": "evt-0001",
    "idempotency_key": "idem-1",
    "correlation_id": "corr-1",
    "causation_id": "cause-1",
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

EVENT_IDS = [spec[1].__name__ for spec in EVENT_SPECS]

#: Fields with no default — dropping any of these must raise. ``event_type`` is
#: now REQUIRED at construction (the per-message subclasses are gone, SFP-219).
REQUIRED_FIELDS = [
    "message_id",
    "idempotency_key",
    "correlation_id",
    "causation_id",
    "occurred_at",
    "producer",
    "event_type",
    "payload",
]


def _full_kwargs(
    member: EventType, payload_cls: type[EventPayload], payload_kwargs: dict[str, Any]
) -> dict[str, Any]:
    return {**ENVELOPE, "event_type": member, "payload": payload_cls(**payload_kwargs)}


def _make_event(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
    **overrides: Any,
) -> EventEnvelope:
    kwargs = _full_kwargs(member, payload_cls, payload_kwargs)
    kwargs.update(overrides)
    return EventEnvelope(**kwargs)


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
# (a)(b)(c)(d)(i) per-event behaviour, parametrized over all 11 events
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_round_trip_preserves_every_field(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(a) A conformant event round-trips through JSON losslessly."""
    original = _make_event(member, payload_cls, payload_kwargs)
    restored = EventEnvelope.from_json(original.to_json())
    # payload returns untyped (the envelope payload is Any; reconstructing the
    # concrete payload type is a serde concern, SFP-45) — assert the JSON itself
    # round-trips losslessly, which exercises to_json AND from_json.
    assert restored.to_json() == original.to_json()


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_envelope_fields_present_and_correct(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(b) The envelope fields exist and carry the provided values."""
    event = _make_event(member, payload_cls, payload_kwargs)
    assert event.message_id == ENVELOPE["message_id"]
    assert event.idempotency_key == ENVELOPE["idempotency_key"]
    assert event.correlation_id == ENVELOPE["correlation_id"]
    assert event.causation_id == ENVELOPE["causation_id"]
    assert event.occurred_at == ENVELOPE["occurred_at"]
    assert event.producer == ENVELOPE["producer"]
    assert event.event_type is member
    assert event.payload == payload_cls(**payload_kwargs)


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_extra_fields_rejected_on_construction(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(c) Unknown extra fields are rejected at construction."""
    with pytest.raises(ValidationError):
        _make_event(member, payload_cls, payload_kwargs, unexpected="x")


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_extra_fields_rejected_on_from_json(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(c) Unknown extra fields are rejected when deserializing."""
    event = _make_event(member, payload_cls, payload_kwargs)
    payload = json.loads(event.to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        EventEnvelope.from_json(json.dumps(payload))


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_malformed_json_rejected(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(i) Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        EventEnvelope.from_json("{not valid json")


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_missing_required_field_raises(
    field: str,
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(d) Dropping any required field (incl. event_type, payload) raises."""
    kwargs = {
        k: v for k, v in _full_kwargs(member, payload_cls, payload_kwargs).items() if k != field
    }
    with pytest.raises(ValidationError):
        EventEnvelope(**kwargs)


# --------------------------------------------------------------------------- #
# (g) payloads reject extra / missing fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_payload_rejects_extra_fields(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """(g) Every payload rejects unknown extra fields (extra='forbid')."""
    bad = dict(payload_kwargs)
    bad["bogus"] = "x"
    with pytest.raises(ValidationError):
        payload_cls(**bad)


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_payload_rejects_missing_field(
    member: EventType,
    payload_cls: type[EventPayload],
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


def test_invalid_event_type_string_rejected() -> None:
    """An ``event_type`` string outside the enum is rejected."""
    member, payload_cls, payload_kwargs = EVENT_SPECS[1]
    event = _make_event(member, payload_cls, payload_kwargs)
    payload = json.loads(event.to_json())
    payload["event_type"] = "NOT_A_REAL_EVENT"
    with pytest.raises(ValidationError):
        EventEnvelope.from_json(json.dumps(payload))


def test_generic_envelope_round_trips_with_any_event_type() -> None:
    """A generic EventEnvelope round-trips with any EventType member."""
    member, payload_cls, payload_kwargs = EVENT_SPECS[1]
    event = _make_event(member, payload_cls, payload_kwargs)
    assert event.event_type is member
    assert EventEnvelope.from_json(event.to_json()).to_json() == event.to_json()


# --------------------------------------------------------------------------- #
# SFP-219 payload hierarchy + anti-gaming (validator gone, event_id rename)
# --------------------------------------------------------------------------- #


def test_concrete_events_are_event_payloads() -> None:
    """The concrete event names are :class:`EventPayload` subclasses."""
    for _member, payload_cls, _kw in EVENT_SPECS:
        assert issubclass(payload_cls, EventPayload)


@pytest.mark.parametrize(("member", "payload_cls", "payload_kwargs"), EVENT_SPECS, ids=EVENT_IDS)
def test_mismatched_event_type_now_accepted(
    member: EventType,
    payload_cls: type[EventPayload],
    payload_kwargs: dict[str, Any],
) -> None:
    """The consistency validator is GONE (SFP-45): a different valid member is
    accepted — proves the per-message validator was truly removed."""
    other = _other_member(member)
    event = _make_event(member, payload_cls, payload_kwargs, event_type=other)
    assert event.event_type is other


def test_event_id_is_gone_renamed_to_message_id() -> None:
    """``event_id`` is renamed to ``message_id`` (SFP-219)."""
    assert "event_id" not in EventEnvelope.model_fields
    assert "message_id" in EventEnvelope.model_fields
    assert not hasattr(EventEnvelope, "event_id")
    # Constructing with the old event_id kwarg is rejected (extra='forbid').
    member, payload_cls, payload_kwargs = EVENT_SPECS[1]
    with pytest.raises(ValidationError):
        _make_event(member, payload_cls, payload_kwargs, event_id="old")


def test_old_payload_suffix_names_not_importable() -> None:
    """Renamed (SFP-219): the old '…Payload'-suffixed names are gone."""
    import sfp_contracts.events.payloads as payloads

    for stale in ("UserInputReceivedPayload", "TicketUpdatedPayload", "MergeUpdatedPayload"):
        assert not hasattr(payloads, stale), f"{stale!r} should have been renamed"
    with pytest.raises(ImportError):
        from sfp_contracts.events.payloads import UserInputReceivedPayload  # noqa: F401


def test_private_payload_base_is_gone() -> None:
    """Renamed (SFP-219): the private ``_Payload`` base is gone (-> EventPayload)."""
    import sfp_contracts.events.payloads as payloads

    assert not hasattr(payloads, "_Payload")
    with pytest.raises(ImportError):
        from sfp_contracts.events.payloads import _Payload  # noqa: F401


def test_envelope_source_has_no_consistency_validator() -> None:
    """EXPECTED_EVENT_TYPE / its validator / @model_validator are all gone."""
    source = Path(envelope_module.__file__).read_text()
    assert "EXPECTED_EVENT_TYPE" not in source
    assert "_enforce_expected_event_type" not in source
    assert "model_validator" not in source


def test_external_ingress_event_external_event_id_unchanged() -> None:
    """Regression guard: ExternalIngressEvent.external_event_id is UNTOUCHED
    (§5.5 standalone model — does NOT subclass EventEnvelope, SFP-219)."""
    from sfp_contracts.events.external import ExternalIngressEvent

    assert "external_event_id" in ExternalIngressEvent.model_fields
    assert "external_event_id" not in EventEnvelope.model_fields
    assert not issubclass(ExternalIngressEvent, EventEnvelope)
