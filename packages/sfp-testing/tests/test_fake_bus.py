"""Tests for :class:`sfp_testing.bus.FakeBus` (SFP-49 / AC1-AC3).

Covers the in-memory Message Bus fake that COMPOSES
:class:`~sfp_messaging.transport.in_memory.InMemoryTransport` and layers the
pinned assertion helpers on top of its ``published_messages`` record:
- (T1/AC3) ``assert_published`` passes for a published payload type.
- (T2/AC3) ``assert_published`` raises ``AssertionError`` (helpful message) when
  no payload of the type was published.
- (T3/AC3) ``assert_published(times=N)`` asserts an EXACT count (pass exact,
  fail ``N-1`` / ``N+1``).
- (T4/AC3) ``assert_not_published`` passes when none published, fails after one.
- (T5/AC3) ``published_count`` returns 0 / 1 / k.
- (T6/AC3) ``messages_of`` returns the matching payloads in publish order
  (identity-preserving).
- (T7/AC2) delegation proof — a ``@command_handler``-registered handler IS
  dispatched with ``(payload, ctx)`` when publishing through the fake (the fake
  is a facade over the real transport + registry, not a recorder-only stub).
- (T8/AC1) ``published_messages`` preserves order across a command + event mix.
- (T9/AC2) NO AWS/SNS/SQS/boto3/moto/aiobotocore is importable from
  ``sfp_testing.bus``.

Both the module-level default registry and the module-private message-context
ContextVar are global and survive across tests, so an autouse fixture resets
both before and after every test (``publish`` drives both module globals through
the composed transport). Test style mirrors ``test_in_memory_transport.py``:
synchronous test fns wrapping an ``async def main()`` in ``asyncio.run``.
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
from sfp_messaging import MessageContext, command_handler, get_current_context, get_default_registry
from sfp_testing import FakeBus


@pytest.fixture(autouse=True)
def _pristine_registry_and_context() -> Iterator[None]:
    """Reset the default registry AND the message-context ContextVar.

    ``FakeBus.publish`` delegates to ``InMemoryTransport.publish``, which
    dispatches through the module-level default registry (clearing it isolates
    each test) and binds the module-private message-context ContextVar (resetting
    it prevents a stale bound context leaking across tests). Resetting before AND
    after each test prevents cross-test leakage.
    """
    get_default_registry().clear()
    context_module._current_context.set(None)
    yield
    get_default_registry().clear()
    context_module._current_context.set(None)


def _command(suffix: str = "1") -> CommandEnvelope:
    """Build a CommandEnvelope with an ExecuteCodingJob payload (DISTINCT values)."""
    return CommandEnvelope(
        message_id=f"cmd-mid-{suffix}",
        idempotency_key=f"cmd-idem-{suffix}",
        correlation_id=f"cmd-corr-{suffix}",
        causation_id=f"cmd-cause-{suffix}",
        occurred_at=f"2026-07-24T0{suffix}:00:00Z",
        command_type=CommandType.EXECUTE_CODING_JOB,
        payload=ExecuteCodingJob(job_id=f"job-{suffix}", pr_spec_id=f"sfp-49-{suffix}"),
    )


def _event(suffix: str = "2") -> EventEnvelope:
    """Build an EventEnvelope with a UserInputReceived payload (DISTINCT values)."""
    return EventEnvelope(
        message_id=f"evt-mid-{suffix}",
        idempotency_key=f"evt-idem-{suffix}",
        correlation_id=f"evt-corr-{suffix}",
        causation_id=f"evt-cause-{suffix}",
        occurred_at=f"2026-07-24T0{suffix}:00:00Z",
        event_type=EventType.USER_INPUT_RECEIVED,
        producer="orchestrator",
        payload=UserInputReceived(session_id=f"sess-{suffix}", text=f"hello-{suffix}"),
    )


# --- (T1/AC3) assert_published passes after publish --------------------------


def test_assert_published_passes_after_publish() -> None:
    """(T1/AC3) assert_published passes for a payload type that was published."""
    envelope = _command()
    bus = FakeBus()

    async def main() -> None:
        await bus.publish(envelope)

    asyncio.run(main())

    # No raise: ExecuteCodingJob was published.
    bus.assert_published(ExecuteCodingJob)
    # The message was recorded (delegated through the transport).
    assert bus.published_messages == [envelope]


# --- (T2/AC3) assert_published raises AssertionError when no match ------------


def test_assert_published_raises_when_no_match() -> None:
    """(T2/AC3) assert_published raises a helpful AssertionError on no match.

    A command was published, but we assert on the event payload type — the
    AssertionError must be raised and its message must be helpful (name the
    expected type and list what was actually published).
    """
    envelope = _command()
    bus = FakeBus()

    async def main() -> None:
        await bus.publish(envelope)

    asyncio.run(main())

    with pytest.raises(AssertionError, match="UserInputReceived") as exc_info:
        bus.assert_published(UserInputReceived)

    msg = str(exc_info.value)
    # Helpful: names the expected type AND what was actually published.
    assert "UserInputReceived" in msg
    assert "ExecuteCodingJob" in msg


# --- (T3/AC3) assert_published(times=N) exact count --------------------------


def test_assert_published_times_exact_count() -> None:
    """(T3/AC3) times=N asserts EXACT count: pass on exact, fail on N-1 / N+1."""
    k = 3
    envelopes = [_command(str(i)) for i in range(k)]
    bus = FakeBus()

    async def main() -> None:
        for env in envelopes:
            await bus.publish(env)

    asyncio.run(main())

    # Passes on the exact count.
    bus.assert_published(ExecuteCodingJob, times=k)

    # Fails on N-1 (too few expected).
    with pytest.raises(AssertionError, match="but found 3"):
        bus.assert_published(ExecuteCodingJob, times=k - 1)

    # Fails on N+1 (too many expected).
    with pytest.raises(AssertionError, match="but found 3"):
        bus.assert_published(ExecuteCodingJob, times=k + 1)


# --- (T4/AC3) assert_not_published -------------------------------------------


def test_assert_not_published_passes_when_none_then_fails_after_one() -> None:
    """(T4/AC3) assert_not_published passes when none published, fails after one."""
    bus = FakeBus()

    # Passes on a fresh bus (nothing published yet).
    bus.assert_not_published(ExecuteCodingJob)
    bus.assert_not_published(UserInputReceived)

    envelope = _command()

    async def main() -> None:
        await bus.publish(envelope)

    asyncio.run(main())

    # Fails now that one ExecuteCodingJob was published.
    with pytest.raises(AssertionError, match="ExecuteCodingJob"):
        bus.assert_not_published(ExecuteCodingJob)

    # A DIFFERENT payload type was still NOT published (identity, ID-052).
    bus.assert_not_published(UserInputReceived)


# --- (T5/AC3) published_count 0 / 1 / k --------------------------------------


def test_published_count_zero_one_and_k() -> None:
    """(T5/AC3) published_count returns 0 on a fresh bus, 1 after one, k after k."""
    bus = FakeBus()

    # Fresh bus: zero.
    assert bus.published_count(ExecuteCodingJob) == 0

    envelopes = [_command(str(i)) for i in range(3)]

    async def main() -> None:
        await bus.publish(envelopes[0])
        assert bus.published_count(ExecuteCodingJob) == 1
        await bus.publish(envelopes[1])
        await bus.publish(envelopes[2])
        assert bus.published_count(ExecuteCodingJob) == 3

    asyncio.run(main())

    # A different payload type is still uncounted (identity, ID-052).
    assert bus.published_count(UserInputReceived) == 0


# --- (T6/AC3) messages_of publish-order identity-preserving -------------------


def test_messages_of_publish_order_identity_preserving() -> None:
    """(T6/AC3) messages_of returns matching payloads in publish order (identity).

    Publish a command, an event, then another command. ``messages_of`` over the
    command type returns exactly the two command payloads, in publish order,
    each ``is``-equal to the published envelope's payload — the interleaved event
    is excluded (identity, ID-052).
    """
    cmd_a = _command("1")
    evt = _event("2")
    cmd_b = _command("3")
    bus = FakeBus()

    async def main() -> None:
        await bus.publish(cmd_a)
        await bus.publish(evt)
        await bus.publish(cmd_b)

    asyncio.run(main())

    commands = bus.messages_of(ExecuteCodingJob)
    assert len(commands) == 2
    # Publish order preserved.
    assert commands[0] is cmd_a.payload
    assert commands[1] is cmd_b.payload
    # The interleaved event payload is excluded.
    assert bus.messages_of(UserInputReceived) == [evt.payload]
    assert evt.payload is bus.messages_of(UserInputReceived)[0]


# --- (T7/AC2) delegation proof: @command_handler dispatches through the fake --


def test_command_handler_dispatched_through_fake() -> None:
    """(T7/AC2) a @command_handler-registered handler IS dispatched via the fake.

    ANTI-GAMING: FakeBus is a facade over the real InMemoryTransport + registry,
    NOT a recorder-only stub. A handler registered with ``@command_handler`` is
    dispatched with ``(payload, ctx)`` when publishing through the fake; the
    payload is the exact envelope payload (identity), and the context dissolves
    the envelope's routing metadata (MAS §4.7) with ``retry_count == 0``.
    """
    envelope = _command()
    received_payload: list[Any] = []
    received_ctx: list[MessageContext] = []
    seen_bound: list[MessageContext] = []

    @command_handler(ExecuteCodingJob)
    async def handler(payload: Any, ctx: MessageContext) -> None:
        received_payload.append(payload)
        received_ctx.append(ctx)
        seen_bound.append(get_current_context())

    bus = FakeBus()

    async def main() -> None:
        await bus.publish(envelope)

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

    # After dispatch: no context remains bound.
    with pytest.raises(LookupError):
        get_current_context()

    # The message was recorded through the delegated transport.
    assert bus.published_messages == [envelope]
    bus.assert_published(ExecuteCodingJob, times=1)


# --- (T8/AC1) published_messages preserves order across command + event ------


def test_published_messages_preserves_order_across_command_and_event() -> None:
    """(T8/AC1) published_messages holds a command+event mix in publish order.

    A command and an event are published; each slot is the exact object
    (identity ``is``), and the order matches the publish order.
    """
    cmd = _command("1")
    evt = _event("2")
    bus = FakeBus()

    async def handler(_payload: Any, _ctx: MessageContext) -> None: ...

    async def main() -> None:
        get_default_registry().register(ExecuteCodingJob, handler)
        get_default_registry().register(UserInputReceived, handler)
        await bus.publish(cmd)
        await bus.publish(evt)

    asyncio.run(main())

    assert len(bus.published_messages) == 2
    assert bus.published_messages[0] is cmd
    assert bus.published_messages[1] is evt


# --- (T9/AC2) no AWS/SNS/SQS/boto3/moto/aiobotocore importable from bus ------


def test_no_aws_imports_from_bus_module() -> None:
    """(T9/AC2) NO AWS/SNS/SQS/boto3/moto/aiobotocore importable from sfp_testing.bus.

    The bus module's only runtime dependency is ``sfp-messaging`` (which itself
    imports no transport SDK). Asserting the forbidden AWS modules are absent
    from ``sys.modules`` after import proves the fake stays vendor-clean —
    handlers run without AWS/SNS/SQS/LocalStack (AC2).
    """
    import sys

    import sfp_testing.bus as bus_mod  # noqa: F401  (import side-effect is the check)

    forbidden = {"boto3", "botocore", "moto", "aiobotocore"}
    present = forbidden & set(sys.modules)
    assert not present, f"sfp_testing.bus transitively imported AWS libraries: {sorted(present)}"
    # The bus module must not expose any AWS attribute either.
    leaked_attrs = {name for name in forbidden if hasattr(bus_mod, name)}
    assert not leaked_attrs, f"sfp_testing.bus exposes AWS attributes: {sorted(leaked_attrs)}"
