"""Tests for the MessageBus interface (SFP-42).

Covers the acceptance criteria:
- (a) ``MessageBus`` is a runtime-checkable Protocol (subclass of
      ``typing.Protocol``) and is re-exported from the package;
- (b) a minimal async concrete implementation satisfies the interface
      (``isinstance``) and REALLY dispatches when exercised via ``asyncio.run``
      (``subscribe`` registers handlers; ``publish`` awaits each once, in
      subscribe order, for both a CommandEnvelope and an EventEnvelope). An
      object lacking the methods, and an asymmetric stub providing only one
      method, do NOT satisfy the interface;
- (c) importing the module pulls in NO transport SDK and the source text
      contains no banned transport tokens (MAS §4.5 / AP-010);
- (d) the typing-only envelope imports do not leak into the module namespace
      (``CommandEnvelope`` is not a runtime attribute of ``sfp_messaging.bus``).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol as TypingProtocol

import sfp_messaging.bus as bus_module
from sfp_contracts.commands.envelope import CommandEnvelope, CommandType
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_messaging import MessageBus as ReexportedMessageBus
from sfp_messaging.bus import MessageBus

# A handler is an async callable over either typed envelope.
type _Handler = Callable[[CommandEnvelope | EventEnvelope], Awaitable[None]]

# Importable module names that would indicate a transport SDK was pulled in.
BANNED_MODULES = ("boto3", "moto", "aiobotocore", "kafka", "redis")
# Source-text tokens that must not appear at this transport-agnostic seam.
BANNED_SOURCE_TOKENS = ("boto3", "sns", "sqs", "aiobotocore", "kafka", "redis")
SOURCE_PATH = Path(bus_module.__file__)


def _command() -> CommandEnvelope:
    """Build a minimal generic CommandEnvelope (SFP-38)."""
    return CommandEnvelope(
        message_id="cmd-1",
        idempotency_key="idem-1",
        correlation_id="corr-1",
        causation_id="cause-1",
        occurred_at="2026-07-21T00:00:00Z",
        command_type=CommandType.EXECUTE_CODING_JOB,
    )


def _event() -> EventEnvelope:
    """Build a minimal generic EventEnvelope (SFP-39)."""
    return EventEnvelope(
        event_id="evt-1",
        occurred_at="2026-07-21T00:00:00Z",
        producer="orchestrator",
        event_type=EventType.TICKET_UPDATED,
    )


# --- (a) MessageBus is a runtime-checkable Protocol -------------------------


def test_messagebus_subclass_of_protocol() -> None:
    """(a) MessageBus subclasses typing.Protocol."""
    assert issubclass(MessageBus, TypingProtocol)


def test_messagebus_is_runtime_checkable() -> None:
    """(a) MessageBus is decorated runtime_checkable (isinstance-able)."""
    assert getattr(MessageBus, "_is_runtime_protocol", False) is True


def test_messagebus_reexported_from_package() -> None:
    """(a) The package re-exports MessageBus (from sfp_messaging import ...)."""
    assert ReexportedMessageBus is MessageBus


# --- (b) concrete implementations satisfy / do not satisfy the interface ----


class _StubBus:
    """Minimal duck-typed MessageBus that REALLY dispatches.

    ``subscribe`` registers async handlers; ``publish`` awaits each registered
    handler exactly once, in the order they were subscribed.
    """

    def __init__(self) -> None:
        self._handlers: list[_Handler] = []

    async def subscribe(self, handler: _Handler) -> None:
        self._handlers.append(handler)

    async def publish(self, message: CommandEnvelope | EventEnvelope) -> None:
        for handler in list(self._handlers):
            await handler(message)


class _PublishOnly:
    """Asymmetric stub: has publish but NOT subscribe."""

    async def publish(self, message: CommandEnvelope | EventEnvelope) -> None: ...


class _SubscribeOnly:
    """Asymmetric stub: has subscribe but NOT publish."""

    async def subscribe(self, handler: _Handler) -> None: ...


def test_stub_bus_is_instance_of_messagebus() -> None:
    """(b) A duck-typed object with both async methods is a MessageBus."""
    assert isinstance(_StubBus(), MessageBus)


def test_plain_object_not_instance_of_messagebus() -> None:
    """(b) An object lacking both methods is NOT a MessageBus."""
    assert not isinstance(object(), MessageBus)


def test_publish_only_stub_not_instance_of_messagebus() -> None:
    """(b) An object missing subscribe is NOT a MessageBus."""
    assert not isinstance(_PublishOnly(), MessageBus)


def test_subscribe_only_stub_not_instance_of_messagebus() -> None:
    """(b) An object missing publish is NOT a MessageBus."""
    assert not isinstance(_SubscribeOnly(), MessageBus)


def test_publish_dispatches_to_subscribers_in_order() -> None:
    """(b) subscribe registers; publish awaits handlers in subscribe order.

    A real CommandEnvelope and a real EventEnvelope are dispatched through the
    stub; each is delivered to every handler in registration order.
    """
    received: list[str] = []

    async def first(message: CommandEnvelope | EventEnvelope) -> None:
        received.append(f"first:{type(message).__name__}")

    async def second(message: CommandEnvelope | EventEnvelope) -> None:
        received.append(f"second:{type(message).__name__}")

    async def main() -> None:
        bus = _StubBus()
        await bus.subscribe(first)
        await bus.subscribe(second)
        await bus.publish(_command())
        await bus.publish(_event())

    asyncio.run(main())
    assert received == [
        "first:CommandEnvelope",
        "second:CommandEnvelope",
        "first:EventEnvelope",
        "second:EventEnvelope",
    ]


# --- (c) no transport SDK ---------------------------------------------------


def test_importing_module_loads_no_transport_sdk() -> None:
    """(c) No banned transport SDK is present in sys.modules after import."""
    loaded = {name.split(".")[0] for name in sys.modules}
    for banned in BANNED_MODULES:
        assert banned not in loaded, f"transport SDK {banned!r} was imported"


def test_source_contains_no_transport_tokens() -> None:
    """(c) The bus source text contains no banned transport tokens."""
    text = SOURCE_PATH.read_text().lower()
    for token in BANNED_SOURCE_TOKENS:
        assert token not in text, f"banned transport token {token!r} in bus source"


# --- (d) typing-only envelope imports do not leak ---------------------------


def test_envelopes_not_in_module_namespace() -> None:
    """(d) CommandEnvelope/EventEnvelope are typing-only (not runtime attrs)."""
    assert hasattr(bus_module, "CommandEnvelope") is False
    assert hasattr(bus_module, "EventEnvelope") is False
