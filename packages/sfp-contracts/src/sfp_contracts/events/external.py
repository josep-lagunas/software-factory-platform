"""The :class:`ExternalIngressEvent` — the raw authenticated ingress wrapper
(MAS §5.5).

Grounded in:
- MAS §5.5 — the ingress boundary: an external system delivers a raw,
  authenticated request body to an SFP endpoint. Infrastructure authenticates
  and envelopes it; it does **not** interpret the body.
- ID-031 — authoritative source for the ingress field set
  (``external_event_id``, ``idempotency_key``, ``received_at``, ``provider``,
  ``endpoint_id``, ``headers``, ``payload``).
- ID-026 / ID-041 — payload opacity: the owning service interprets the body via
  its own local schema. Infrastructure never parses it, so this contract keeps
  ``payload`` as opaque :class:`bytes`.
- ID-013 — JSON is the reference serialization format, which drives the
  ``to_json`` / ``from_json`` serde helpers.
- SFP-40 — the implementation ticket.

Rename note (SFP-40 vs SFP-39):
    The ticket literally names this type ``ExternalEventReceived``. That name is
    already taken: :mod:`sfp_contracts.events.models` (SFP-39, MAS §5.4)
    defines an :class:`~sfp_contracts.events.models.ExternalEventReceived` — the
    typed **bus** event that carries an
    :class:`~sfp_contracts.events.payloads.ExternalEventReceivedPayload` and an
    ``event_type`` discriminator. The two are different things:

    - The §5.4 **bus event** (``ExternalEventReceived``) is a structured,
      typed message published on the platform event bus once the ingress
      payload has been parsed by its owning service.
    - The §5.5 **ingress wrapper** (this class, ``ExternalIngressEvent``) is
      the raw, authenticated body *before* any service has interpreted it. It
      has no ``event_type`` and no ``producer``; it is not one of the 11 bus
      events and does **not** subclass
      :class:`~sfp_contracts.events.envelope.EventEnvelope`.

    To avoid a same-package name collision, this class is named
    ``ExternalIngressEvent`` rather than ``ExternalEventReceived``.

Design choices:
- ``payload`` is :class:`bytes` — the raw authenticated body. Pydantic v2's
  default ``bytes``-to-JSON serialization is UTF-8 text (which both leaks the
  body and raises ``PydanticSerializationError`` on non-UTF8 bytes), so this
  model attaches an explicit base64 field serializer (JSON-mode only) and a
  matching before-validator. The result: ``payload`` is opaque ``bytes`` at
  the Python level, and across ``to_json`` / ``from_json`` round trips it is
  carried as a base64 string — never parsed/structured, and robust to
  arbitrary binary bodies (ID-026 / ID-041).
- ``received_at`` is a string holding an ISO-8601 timestamp, not
  :class:`datetime.datetime`. The sibling schemas avoid runtime-only types so
  ``from_json`` round-trips stay deterministic and clock-free; ingress follows
  the same rule.
- ``headers`` is ``dict[str, str]`` (HTTP-style). Header semantics (case,
  multi-values) are the ingress adapter's concern, not the contract's.
- ``extra='forbid'`` rejects unknown fields, matching every other contract in
  this package.
"""

import base64
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator


class ExternalIngressEvent(BaseModel):
    """The raw authenticated ingress wrapper (MAS §5.5 / ID-031 / SFP-40).

    Captures an external system's request at the SFP ingress boundary exactly as
    received: identity/tracing metadata plus the **opaque** body. Infrastructure
    authenticates and envelopes; the owning service interprets ``payload`` via
    its own local schema (ID-026 / ID-041).

    This is a standalone model: it is **not** one of the 11 bus events and does
    not subclass :class:`~sfp_contracts.events.envelope.EventEnvelope`. It has
    no ``event_type`` / ``producer`` (see the module docstring for the rename
    rationale).

    Fields:
        external_event_id: Stable identifier for this ingress event.
        idempotency_key: Caller-supplied key for deduplication / replay safety.
        received_at: ISO-8601 timestamp (string) marking when ingress occurred.
        provider: Originating external system (e.g. ``"github"``, ``"slack"``).
        endpoint_id: The SFP ingress endpoint that received the request.
        headers: HTTP-style request headers (``dict[str, str]``).
        payload: The raw authenticated body — opaque ``bytes``; never parsed by
            infrastructure.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    external_event_id: str
    idempotency_key: str
    received_at: str
    provider: str
    endpoint_id: str
    headers: dict[str, str]
    payload: bytes

    @field_validator("payload", mode="before")
    @classmethod
    def _decode_payload(cls, value: Any) -> Any:
        """Decode a base64 ``payload`` string back to opaque ``bytes``.

        Only triggers on string input (the JSON form); raw ``bytes`` passed at
        construction pass through unchanged. A malformed base64 string raises
        and is surfaced as a ``ValidationError``.
        """
        if isinstance(value, str):
            return base64.b64decode(value)
        return value

    @field_serializer("payload", when_used="json")
    def _encode_payload(self, value: bytes) -> str:
        """Encode the opaque ``bytes`` payload as a base64 string for JSON.

        JSON-mode only (``when_used='json'``) so ``model_dump()`` keeps
        returning raw ``bytes``; only ``model_dump_json()`` / ``to_json()``
        carry the base64 form. This keeps the body opaque in transit and
        robust to arbitrary binary bytes.
        """
        return base64.b64encode(value).decode("ascii")

    def to_json(self) -> str:
        """Serialize this ingress event to a JSON string (delegates to pydantic).

        The opaque ``bytes`` payload is base64-encoded (via the
        ``_encode_payload`` serializer) so the JSON form remains serializable
        without interpreting the body.
        """
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "ExternalIngressEvent":
        """Deserialize an ingress event from a JSON string or bytes.

        The base64-encoded ``payload`` is decoded back to opaque ``bytes`` (via
        the ``_decode_payload`` validator).
        """
        return cls.model_validate_json(data)
