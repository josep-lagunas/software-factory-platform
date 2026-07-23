"""Tests for the in-memory :class:`InMemoryTransport` (SFP-46 / AC1-AC3).

Covers the registry-dispatching test :class:`~sfp_messaging.bus.MessageBus`:
- (T1/AC1) ``publish`` dispatches the PAYLOAD (not the envelope) to a registered
  handler as ``(payload, ctx)``; the handler sees the bound context via
  ``get_current_context()``; the envelope's metadata dissolves into the context;
  no context remains bound after dispatch.
- (T1b/AC1) an EventEnvelope dispatch variant for dual command/event coverage.
- (T2/AC2) ``published_messages`` records every publish in publish order
  (identity-preserving, mixing command + event).
- (T3/AC2) record-before-raise: a handler that raises still leaves the message
  recorded.
- (T4/AC1) the handler's exception propagates UNCHANGED (exact type, no swallow).
- (T5/AC1) an unregistered payload type is recorded but NOT dispatched, and
  ``publish`` returns ``None`` (no raise).
- (T6) ``subscribe()`` raises ``NotImplementedError`` pointing at the registry.
- (T7/AC1) the transport satisfies the ``runtime_checkable`` ``MessageBus``
  Protocol (``isinstance``).

Both the module-level default registry and the module-private message-context
ContextVar are global and survive across tests, so an autouse fixture resets
both before and after every test (``publish`` drives both module globals).

Test style mirrors ``test_bus.py`` / ``test_context.py``: synchronous test fns
wrapping an ``async def main()`` in ``asyncio.run(main())``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
import sfp_messaging.context as context_module
from sfp_contracts.commands.envelope import CommandEnvelope, CommandType
from sfp_contracts.commands.payloads import ExecuteCodingJob
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_contracts.events.payloads import UserInputReceived
from sfp_messaging import MessageContext, get_current_context, get_default_registry
from sfp_messaging.bus import MessageBus
from sfp_messaging.transport.in_memory import InMemoryTransport


@pytest.fixture(autouse=True)
def _pristine_registry_and_context() -> Iterator[None]:
    """Reset the default registry AND the message-context ContextVar.

    ``publish`` dispatches through the module-level default registry (clearing
    it isolates each test) and binds the module-private message-context
    ContextVar (resetting it prevents a stale bound context leaking across
    tests). Resetting before AND after each test prevents cross-test leakage.
    """
    get_default_registry().clear()
    context_module._current_context.set(None)
    yield
    get_default_registry().clear()
    context_module._current_context.set(None)


def _command() -> CommandEnvelope:
    """Build a CommandEnvelope with an ExecuteCodingJob payload (DISTINCT values)."""
    return CommandEnvelope(
        message_id="cmd-mid-1",
        idempotency_key="cmd-idem-1",
        correlation_id="cmd-corr-1",
        causation_id="cmd-cause-1",
        occurred_at="2026-07-24T01:00:00Z",
        command_type=CommandType.EXECUTE_CODING_JOB,
        payload=ExecuteCodingJob(job_id="job-1", pr_spec_id="sfp-46"),
    )


def _event() -> EventEnvelope:
    """Build an EventEnvelope with a UserInputReceived payload (DISTINCT values)."""
    return EventEnvelope(
        message_id="evt-mid-2",
        idempotency_key="evt-idem-2",
        correlation_id="evt-corr-2",
        causation_id="evt-cause-2",
        occurred_at="2026-07-24T02:00:00Z",
        event_type=EventType.USER_INPUT_RECEIVED,
        producer="orchestrator",
        payload=UserInputReceived(session_id="sess-2", text="hello"),
    )


# --- (T1/AC1) publish dispatches the PAYLOAD to a registered handler ---------


def test_publish_dispatches_payload_to_command_handler() -> None:
    """(T1/AC1) publish dispatches the PAYLOAD (not envelope) with (payload, ctx).

    ANTI-GAMING: the handler received the payload object (identity ``is``), which
    is NOT a CommandEnvelope; and a MessageContext whose correlation/causation/
    message ids equal the envelope's, received_at == envelope.occurred_at, and
    retry_count == 0. Inside the handler the bound context is visible via
    ``get_current_context()``; after dispatch no context remains bound.
    """
    envelope = _command()
    received_payload: list[Any] = []
    received_ctx: list[MessageContext] = []
    seen_bound: list[MessageContext] = []

    async def handler(payload: Any, ctx: MessageContext) -> None:
        received_payload.append(payload)
        received_ctx.append(ctx)
        seen_bound.append(get_current_context())

    transport = InMemoryTransport()

    async def main() -> None:
        get_default_registry().register(ExecuteCodingJob, handler)
        await transport.publish(envelope)

    asyncio.run(main())

    # The handler received the PAYLOAD (identity), NOT the envelope.
    assert len(received_payload) == 1
    assert received_payload[0] is envelope.payload
    assert not isinstance(received_payload[0], CommandEnvelope)

    # The context dissolves the envelope's routing metadata (MAS §4.7).
    ctx = received_ctx[0]
    assert ctx.correlation_id == envelope.correlation_id
    assert ctx.causation_id == envelope.causation_id
    assert ctx.message_id == envelope.message_id
    assert ctx.received_at == envelope.occurred_at
    assert ctx.retry_count == 0

    # Inside the handler, the bound context was visible via get_current_context().
    assert seen_bound[0] is ctx

    # The handler received the SAME context object as the second argument.
    assert received_ctx[0] is seen_bound[0]

    # After dispatch: no context remains bound.
    with pytest.raises(LookupError):
        get_current_context()

    # The message was recorded (record-before-dispatch).
    assert transport.published_messages == [envelope]


# --- (T1b/AC1) event-variant dispatch for dual coverage ----------------------


def test_publish_dispatches_payload_to_event_handler() -> None:
    """(T1b/AC1) publish dispatches an EventEnvelope payload to its handler."""
    envelope = _event()
    received_payload: list[Any] = []
    received_ctx: list[MessageContext] = []

    async def handler(payload: Any, ctx: MessageContext) -> None:
        received_payload.append(payload)
        received_ctx.append(ctx)

    transport = InMemoryTransport()

    async def main() -> None:
        get_default_registry().register(UserInputReceived, handler)
        await transport.publish(envelope)

    asyncio.run(main())

    # Handler received the PAYLOAD (identity), not the envelope.
    assert received_payload[0] is envelope.payload
    assert not isinstance(received_payload[0], EventEnvelope)

    # Context dissolves the event envelope's routing metadata.
    ctx = received_ctx[0]
    assert ctx.correlation_id == envelope.correlation_id
    assert ctx.causation_id == envelope.causation_id
    assert ctx.message_id == envelope.message_id
    assert ctx.received_at == envelope.occurred_at
    assert ctx.retry_count == 0

    assert transport.published_messages == [envelope]


# --- (T2/AC2) published_messages records every publish in order --------------


def test_published_messages_records_in_publish_order() -> None:
    """(T2/AC2) published_messages holds every message in publish order (identity).

    A command and an event are published; each slot is the exact object (identity
    ``is``), and the order matches the publish order.
    """
    cmd = _command()
    evt = _event()

    async def handler(_payload: Any, _ctx: MessageContext) -> None: ...

    transport = InMemoryTransport()

    async def main() -> None:
        get_default_registry().register(ExecuteCodingJob, handler)
        get_default_registry().register(UserInputReceived, handler)
        await transport.publish(cmd)
        await transport.publish(evt)

    asyncio.run(main())

    assert len(transport.published_messages) == 2
    assert transport.published_messages[0] is cmd
    assert transport.published_messages[1] is evt


# --- (T3/AC2) record-before-raise -------------------------------------------


def test_record_before_raise_leaves_message_recorded() -> None:
    """(T3/AC2) a handler that raises still leaves the message recorded.

    The message is appended to published_messages BEFORE dispatch, so the record
    survives the handler's exception.
    """

    class _Boom(Exception):
        pass

    envelope = _command()

    async def handler(_payload: Any, _ctx: MessageContext) -> None:
        raise _Boom("handler failed")

    async def main() -> None:
        transport = InMemoryTransport()
        get_default_registry().register(ExecuteCodingJob, handler)
        with pytest.raises(_Boom):
            await transport.publish(envelope)
        # Even though the handler raised, the message was recorded.
        assert transport.published_messages == [envelope]

    asyncio.run(main())


# --- (T4/AC1) exception propagates unchanged --------------------------------


def test_handler_exception_propagates_unchanged() -> None:
    """(T4/AC1) the handler's exception propagates UNCHANGED (exact type, no swallow)."""

    class _CustomHandlerError(Exception):
        pass

    envelope = _command()

    async def handler(_payload: Any, _ctx: MessageContext) -> None:
        raise _CustomHandlerError("propagate me")

    async def main() -> None:
        transport = InMemoryTransport()
        get_default_registry().register(ExecuteCodingJob, handler)
        await transport.publish(envelope)

    with pytest.raises(_CustomHandlerError, match="propagate me") as exc_info:
        asyncio.run(main())
    # Exact type — not a wrapped/suppressed/re-raised different exception.
    assert type(exc_info.value) is _CustomHandlerError


# --- (T5/AC1) unregistered payload: recorded, no dispatch, returns None ------


def test_unregistered_payload_recorded_no_dispatch_returns_none() -> None:
    """(T5/AC1) an unregistered payload type is recorded but NOT dispatched.

    No handler is registered for the payload type. The message is still recorded,
    no handler runs, and ``publish`` returns ``None`` (does not raise).
    """
    envelope = _command()
    dispatched: list[Any] = []

    async def handler(payload: Any, ctx: MessageContext) -> None:
        dispatched.append(payload)

    async def main() -> None:
        transport = InMemoryTransport()
        # Deliberately do NOT register the handler for ExecuteCodingJob.
        result = await transport.publish(envelope)
        assert result is None
        # No dispatch occurred.
        assert dispatched == []
        # The message was still recorded.
        assert transport.published_messages == [envelope]

    asyncio.run(main())


# --- (T6) subscribe() raises NotImplementedError -----------------------------


def test_subscribe_raises_notimplemented_error() -> None:
    """(T6) subscribe() raises NotImplementedError pointing at the registry."""
    transport = InMemoryTransport()

    async def handler(_payload: Any, _ctx: MessageContext) -> None: ...

    async def main() -> None:
        with pytest.raises(NotImplementedError, match="registry"):
            await transport.subscribe(handler)

    asyncio.run(main())


# --- (T7/AC1) isinstance(transport, MessageBus) is True ----------------------


def test_transport_satisfies_messagebus_protocol() -> None:
    """(T7/AC1) InMemoryTransport satisfies the runtime_checkable MessageBus."""
    transport = InMemoryTransport()
    assert isinstance(transport, MessageBus)
