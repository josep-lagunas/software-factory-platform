"""First-class tests for the :class:`MessageEnvelope` base (MAS §4.7 / SFP-219).

Covers the new uniform envelope that every command and event carries:
- (TD-MB-01) it is a pydantic ``BaseModel`` subclass;
- (TD-MB-02) it exposes the 6 uniform fields, in the PINNED declaration order
  ``message_id``, ``idempotency_key``, ``correlation_id``, ``causation_id``,
  ``occurred_at``, ``payload``;
- (TD-MB-03) ``occurred_at`` is typed ``str`` (not a runtime ``datetime``) and
  ``payload`` is typed :data:`typing.Any` (payload-agnostic base);
- (TD-MB-04) ``to_json`` / ``from_json`` round-trip losslessly;
- (TD-MB-05) unknown fields are rejected on construction AND ``from_json``
  (``extra='forbid'``);
- (TD-MB-06) dropping any of the 6 required fields raises ``ValidationError``;
- (TD-MB-07) the module imports NONE of the payloads submodules (no import
  cycle, R5) — a source-text guard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import sfp_contracts.messages as messages_module
from pydantic import BaseModel, ValidationError
from sfp_contracts.messages import MessageEnvelope

#: The 6 uniform fields in their authoritative declaration order (MAS §4.7).
UNIFORM_FIELDS: list[str] = [
    "message_id",
    "idempotency_key",
    "correlation_id",
    "causation_id",
    "occurred_at",
    "payload",
]

#: A minimal valid envelope kwargs block (``payload`` is a plain dict here: the
#: base is payload-agnostic, so a dict round-trips as a dict through Any).
ENVELOPE: dict[str, Any] = {
    "message_id": "msg-0001",
    "idempotency_key": "idem-1",
    "correlation_id": "corr-1",
    "causation_id": "cause-1",
    "occurred_at": "2026-07-23T10:00:00Z",
    "payload": {"key": "value", "n": 3},
}


def _make(**overrides: Any) -> MessageEnvelope:
    kwargs = dict(ENVELOPE)
    kwargs.update(overrides)
    return MessageEnvelope(**kwargs)


# --------------------------------------------------------------------------- #
# (TD-MB-01) MessageEnvelope is a pydantic BaseModel subclass
# --------------------------------------------------------------------------- #


def test_message_envelope_is_basemodel_subclass() -> None:
    """(TD-MB-01) MessageEnvelope subclasses pydantic BaseModel."""
    assert issubclass(MessageEnvelope, BaseModel)


# --------------------------------------------------------------------------- #
# (TD-MB-02) the 6 uniform fields exist, in the pinned declaration order
# --------------------------------------------------------------------------- #


def test_uniform_fields_present_in_pinned_order() -> None:
    """(TD-MB-02) model_fields exposes exactly the 6 fields, in this order."""
    assert list(MessageEnvelope.model_fields) == UNIFORM_FIELDS


# --------------------------------------------------------------------------- #
# (TD-MB-03) occurred_at is str; payload is typing.Any
# --------------------------------------------------------------------------- #


def test_occurred_at_is_typed_str() -> None:
    """(TD-MB-03) occurred_at is annotated str (no runtime-only datetime)."""
    assert MessageEnvelope.model_fields["occurred_at"].annotation is str


def test_payload_is_typed_any() -> None:
    """(TD-MB-03) payload is annotated typing.Any (payload-agnostic base)."""
    assert MessageEnvelope.model_fields["payload"].annotation is Any


# --------------------------------------------------------------------------- #
# (TD-MB-04) to_json / from_json round-trip
# --------------------------------------------------------------------------- #


def test_to_json_from_json_round_trip() -> None:
    """(TD-MB-04) A conformant envelope round-trips through JSON losslessly."""
    original = _make()
    restored = MessageEnvelope.from_json(original.to_json())
    assert restored == original
    assert restored.to_json() == original.to_json()


# --------------------------------------------------------------------------- #
# (TD-MB-05) extra='forbid' on construction and from_json
# --------------------------------------------------------------------------- #


def test_extra_fields_rejected_on_construction() -> None:
    """(TD-MB-05) Unknown fields are rejected at construction (extra='forbid')."""
    with pytest.raises(ValidationError):
        _make(unexpected="x")


def test_extra_fields_rejected_on_from_json() -> None:
    """(TD-MB-05) Unknown fields are rejected when deserializing."""
    payload = json.loads(_make().to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        MessageEnvelope.from_json(json.dumps(payload))


# --------------------------------------------------------------------------- #
# (TD-MB-06) missing required fields raise
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field", UNIFORM_FIELDS)
def test_missing_required_field_raises(field: str) -> None:
    """(TD-MB-06) Dropping any of the 6 required fields raises ValidationError."""
    kwargs = {k: v for k, v in ENVELOPE.items() if k != field}
    with pytest.raises(ValidationError):
        MessageEnvelope(**kwargs)


# --------------------------------------------------------------------------- #
# (TD-MB-07) source guard: the module imports NO payloads submodule (no cycle)
# --------------------------------------------------------------------------- #


def test_module_imports_no_payloads_submodule() -> None:
    """(TD-MB-07) messages.py has no import of a payloads module (no cycle, R5)."""
    source = Path(messages_module.__file__).read_text()
    import_lines = [
        line for line in source.splitlines() if line.strip().startswith(("import ", "from "))
    ]
    offenders = [line for line in import_lines if "payloads" in line]
    assert offenders == [], f"messages.py must not import payloads: {offenders!r}"
