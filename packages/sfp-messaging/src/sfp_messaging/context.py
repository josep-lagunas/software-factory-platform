"""The :class:`MessageContext` value object + contextvars access API (SFP-44).

MAS §4.7 routes envelope metadata into a per-message context that the framework
binds around each handler invocation; the handler then receives ``(payload,
context)`` rather than the raw envelope. This module defines that context value
object and the contextvars access API that lets a handler (or any code it calls,
e.g. a logger) read the ambient context without it being threaded through every
signature.

Grounded in:
- MAS §4.7 — envelope metadata flows into ``MessageContext``; the handler
  receives the payload plus this context.
- Impl Notes §1 — the v0 ``MessageContext`` carries exactly five fields
  (``correlation_id``, ``causation_id``, ``message_id``, ``received_at``,
  ``retry_count``); trace/span ids and other framework services are deferred.
- SFP-48 — the sibling observability package's context-vars module is the
  access-API precedent mirrored here (bind/get over a raw ``contextvars.ContextVar``).

Design choices:
- :class:`MessageContext` is a pydantic v2 ``BaseModel`` that is ``frozen`` and
  ``extra='forbid'``: handlers read it but never reconstruct or mutate it (the
  framework is the sole constructor, via the module-private
  :func:`_build_message_context` factory).
- ``received_at`` is an ISO-8601 ``str`` (not ``datetime``), mirroring
  ``MessageEnvelope.occurred_at: str`` — clock-free, JSON-round-trippable.
- This module imports nothing outside the standard library and pydantic. In
  particular it does NOT import the observability package: the messaging package
  must stay decoupled from it at this layer (SFP-46's dispatch will bridge the
  two if/when needed).
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, Field


class MessageContext(BaseModel):
    """The per-message metadata a handler receives alongside its payload.

    Carries the routing/dedup metadata the framework extracts from the incoming
    envelope (MAS §4.7): the correlation/causation chain this message belongs
    to, this message's identity, when the framework received it, and how many
    times it has already been retried. Handlers read these fields; they never
    construct or mutate the object (the framework is the sole constructor via
    the module-private :func:`_build_message_context` factory).

    Fields, in declaration order:
    - ``correlation_id`` — the causal chain this message belongs to.
    - ``causation_id`` — the message that caused this one.
    - ``message_id`` — this message's identity.
    - ``received_at`` — when the framework received it (ISO-8601 ``str``, not
      ``datetime``, mirroring ``MessageEnvelope.occurred_at``).
    - ``retry_count`` — how many times dispatch has already retried this message.

    The model is ``frozen`` (fields are immutable) and rejects unknown fields
    (``extra='forbid'``). ``retry_count`` is validated strictly so a coerced
    string or float is never accepted (it must be a genuine ``int``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: str
    causation_id: str
    message_id: str
    received_at: str
    retry_count: int = Field(strict=True)


#: Module-private contextvar holding the current :class:`MessageContext`, or
#: ``None`` when none is bound (the default). Driven directly — this module does
#: NOT route through ``structlog.contextvars`` (that is an observability-layer
#: concern; this module stays decoupled from the observability package).
_current_context: contextvars.ContextVar[MessageContext | None] = contextvars.ContextVar(
    "sfp_messaging_current_context", default=None
)


def get_current_context() -> MessageContext:
    """Return the currently-bound :class:`MessageContext`.

    Returns the value set by the enclosing :func:`bind_message_context` block.
    Raises :class:`LookupError` (pinned exact type) when no context is bound, so
    callers can distinguish "no context" from a context whose fields are empty.
    """
    ctx = _current_context.get()
    if ctx is None:
        raise LookupError("No MessageContext is currently bound")
    return ctx


@contextlib.contextmanager
def bind_message_context(ctx: MessageContext) -> Iterator[None]:
    """Bind ``ctx`` as the current :class:`MessageContext` for a ``with`` block.

    Sets ``ctx`` on entry and restores the prior value (typically unset) on exit
    via ``ContextVar.reset``, including when the block exits by raising.
    Exceptions are NOT suppressed — they propagate after the context is reset.

    Args:
        ctx: The :class:`MessageContext` to bind for the lifetime of the block.

    Yields:
        ``None``. ``ctx`` is the current context for the lifetime of the block.
    """
    token = _current_context.set(ctx)
    try:
        yield
    finally:
        _current_context.reset(token)


def _build_message_context(
    *,
    correlation_id: str,
    causation_id: str,
    message_id: str,
    received_at: str,
    retry_count: int,
) -> MessageContext:
    """Construct a :class:`MessageContext` (the single framework path).

    Keyword-only so callers cannot couple to field order; this is the sole
    construction path the framework dispatch (SFP-46) uses. Handlers never call
    it — they receive a context, they do not build one. Not re-exported from the
    package (enforced by convention + the test suite).
    """
    return MessageContext(
        correlation_id=correlation_id,
        causation_id=causation_id,
        message_id=message_id,
        received_at=received_at,
        retry_count=retry_count,
    )
