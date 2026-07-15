"""Tests for the :class:`ExternalIngressEvent` ingress wrapper (SFP-40 / MAS §5.5).

Covers the acceptance criteria:
- (a) a conformant payload round-trips through ``to_json``/``from_json``;
- (b) the opaque ``bytes`` payload survives a JSON round-trip (base64) —
  including non-UTF8 / binary bytes — and stays ``bytes``, never parsed;
- (c) extra fields are rejected on construction AND on ``from_json``
  (``extra='forbid'``);
- (d) every missing required field raises ``ValidationError``;
- (e) the ``headers`` dict round-trips losslessly;
- (f) the standalone model has no bus-event fields (``event_type`` /
  ``producer``) and does not subclass ``EventEnvelope`` (rename guarantee).
"""

from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.events.external import ExternalIngressEvent

VALID_KWARGS: dict[str, Any] = {
    "external_event_id": "evt_01HW6G3K2P4M7N8Q9R0S1T2V3W",
    "idempotency_key": "key_b9c2e4f6",
    "received_at": "2026-07-15T20:59:00Z",
    "provider": "github",
    "endpoint_id": "ep_webhook_github_main",
    "headers": {
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": "c8e2f4a6",
        "Content-Type": "application/json",
    },
    "payload": b'{"ref":"refs/heads/main","after":"0123abc"}',
}

REQUIRED_FIELDS = list(VALID_KWARGS.keys())


def make_event(**overrides: Any) -> ExternalIngressEvent:
    kwargs = dict(VALID_KWARGS)
    kwargs.update(overrides)
    return ExternalIngressEvent(**kwargs)


def test_round_trip_preserves_every_field() -> None:
    """(a) A conformant event round-trips through JSON losslessly."""
    original = make_event()
    restored = ExternalIngressEvent.from_json(original.to_json())

    assert restored == original
    assert restored.external_event_id == VALID_KWARGS["external_event_id"]
    assert restored.idempotency_key == VALID_KWARGS["idempotency_key"]
    assert restored.received_at == VALID_KWARGS["received_at"]
    assert restored.provider == "github"
    assert restored.endpoint_id == VALID_KWARGS["endpoint_id"]
    assert restored.headers == VALID_KWARGS["headers"]


def test_payload_bytes_round_trip() -> None:
    """(b) The opaque bytes payload survives a JSON round-trip (base64)."""
    original = make_event()
    restored = ExternalIngressEvent.from_json(original.to_json())

    assert restored.payload == original.payload


def test_payload_binary_non_utf8_round_trip() -> None:
    """(b) Binary / non-UTF8 payload bytes round-trip — proving opacity.

    If infrastructure parsed the body, bytes outside any text encoding would
    break the round-trip. They do not: the body is opaque base64.
    """
    binary_payload = bytes(range(256))  # full byte range, not valid UTF8
    original = make_event(payload=binary_payload)
    restored = ExternalIngressEvent.from_json(original.to_json())

    assert restored.payload == binary_payload


def test_payload_is_bytes_after_round_trip() -> None:
    """(b) After a round-trip the payload is still ``bytes``, not a str/dict."""
    restored = ExternalIngressEvent.from_json(make_event().to_json())

    assert isinstance(restored.payload, bytes)
    assert not isinstance(restored.payload, str)  # type: ignore[unreachable]


def test_payload_json_form_is_base64_string() -> None:
    """(b) The JSON form of payload is a base64 string, not structured JSON.

    Confirms the body stays opaque in transit: even valid-JSON-looking bytes
    are carried as base64, never re-shaped into an object the contract knows
    about (ID-026 / ID-041).
    """
    import json

    dumped = json.loads(make_event().to_json())
    assert isinstance(dumped["payload"], str)
    # The original text body does not appear verbatim in the JSON: it is
    # base64-encoded, so the raw characters are not present as a substring.
    assert VALID_KWARGS["payload"].decode() != dumped["payload"]


@pytest.mark.parametrize(
    "extra",
    [
        {"event_type": "EXTERNAL_EVENT_RECEIVED"},
        {"producer": "github"},
        {"unexpected": "x"},
    ],
)
def test_extra_fields_rejected_on_construction(extra: dict[str, Any]) -> None:
    """(c) Known bus-event fields and unknown extras are rejected at build time."""
    with pytest.raises(ValidationError):
        make_event(**extra)


def test_extra_fields_rejected_on_from_json() -> None:
    """(c) Extra fields are rejected when deserializing (extra='forbid')."""
    import json

    payload = json.loads(make_event().to_json())
    payload["event_type"] = "EXTERNAL_EVENT_RECEIVED"
    with pytest.raises(ValidationError):
        ExternalIngressEvent.from_json(json.dumps(payload))


@pytest.mark.parametrize("missing_field", REQUIRED_FIELDS)
def test_missing_required_field_raises(missing_field: str) -> None:
    """(d) Dropping any of the seven required fields raises ValidationError."""
    kwargs = {k: v for k, v in VALID_KWARGS.items() if k != missing_field}
    with pytest.raises(ValidationError):
        ExternalIngressEvent(**kwargs)


def test_headers_dict_round_trips() -> None:
    """(e) The headers dict round-trips losslessly, including multiple keys."""
    headers = {
        "X-Signature": "sha256=abc123",
        "X-Request-Id": "req-42",
        "User-Agent": "GitHub-Hookshot/abc",
    }
    original = make_event(headers=headers)
    restored = ExternalIngressEvent.from_json(original.to_json())

    assert restored.headers == headers
    assert restored.headers["X-Signature"] == "sha256=abc123"


def test_empty_headers_and_empty_payload_accepted() -> None:
    """Edge case: empty headers dict and empty payload bytes are valid."""
    original = make_event(headers={}, payload=b"")
    restored = ExternalIngressEvent.from_json(original.to_json())

    assert restored.headers == {}
    assert restored.payload == b""


def test_malformed_json_rejected() -> None:
    """Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        ExternalIngressEvent.from_json("{not valid json")


def test_not_subclass_of_event_envelope() -> None:
    """(f) Standalone model: does not subclass EventEnvelope and has no
    ``event_type``/``producer`` fields (rename guarantee)."""
    from sfp_contracts.events.envelope import EventEnvelope

    assert not issubclass(ExternalIngressEvent, EventEnvelope)
    field_names = set(ExternalIngressEvent.model_fields.keys())
    assert "event_type" not in field_names
    assert "producer" not in field_names
