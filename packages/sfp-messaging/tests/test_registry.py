"""Tests for the message-type-keyed HandlerRegistry (SFP-43 / AC1).

Covers the registry directly (no decorator layer):
- (AC1) ``register`` + ``resolve`` round-trips the same callable (identity);
- last-write-wins replaces a prior binding under the same type;
- an unregistered type resolves to ``None`` (PIN: returns None, never raises —
  ID-052 keeps the registry policy-free);
- two distinct concrete classes route to their own handlers independently;
- exact-key semantics — registering a parent does NOT resolve a local subclass
  (no MRO walk);
- ``clear`` empties every binding;
- ``register`` raises ``TypeError`` for a non-class message type, an instance
  used as a message type, and a non-callable handler;
- ``get_default_registry`` returns the same module-level singleton each call.

Each test builds a fresh ``HandlerRegistry()`` so the module-level default
registry is never mutated (hermetic).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sfp_contracts.commands.envelope import CommandEnvelope
from sfp_contracts.commands.models import ExecuteCodingJob, RequestMerge
from sfp_contracts.events.envelope import EventEnvelope
from sfp_messaging.registry import HandlerRegistry, get_default_registry

type _Handler = Callable[[CommandEnvelope | EventEnvelope], Awaitable[None]]


# --- (AC1) register + resolve round-trip -----------------------------------


def test_register_and_resolve_returns_same_callable() -> None:
    """(AC1) A registered handler is returned by resolve with object identity."""

    async def handler(message: CommandEnvelope | EventEnvelope) -> None: ...

    registry = HandlerRegistry()
    registry.register(ExecuteCodingJob, handler)
    assert registry.resolve(ExecuteCodingJob) is handler


# --- resolve returns None for unregistered types ---------------------------


def test_resolve_unregistered_type_returns_none() -> None:
    """PIN (ID-052): an unregistered type resolves to None, not raised."""
    registry = HandlerRegistry()
    assert registry.resolve(ExecuteCodingJob) is None


# --- last-write-wins --------------------------------------------------------


def test_last_write_wins_replaces_binding() -> None:
    """A later registration under the same type replaces the prior binding."""

    async def first(message: CommandEnvelope | EventEnvelope) -> None: ...

    async def second(message: CommandEnvelope | EventEnvelope) -> None: ...

    registry = HandlerRegistry()
    registry.register(ExecuteCodingJob, first)
    registry.register(ExecuteCodingJob, second)
    assert registry.resolve(ExecuteCodingJob) is second


# --- two concrete types route independently --------------------------------


def test_two_types_route_independently() -> None:
    """Two concrete command classes resolve to their own handlers."""

    async def job_handler(message: CommandEnvelope | EventEnvelope) -> None: ...

    async def merge_handler(message: CommandEnvelope | EventEnvelope) -> None: ...

    registry = HandlerRegistry()
    registry.register(ExecuteCodingJob, job_handler)
    registry.register(RequestMerge, merge_handler)
    assert registry.resolve(ExecuteCodingJob) is job_handler
    assert registry.resolve(RequestMerge) is merge_handler


# --- exact-key: no MRO walk ------------------------------------------------


def test_resolve_subclass_does_not_walk_mro() -> None:
    """Exact-key: a parent binding does NOT resolve a subclass (no MRO walk)."""

    async def parent_handler(message: CommandEnvelope | EventEnvelope) -> None: ...

    class _Child(ExecuteCodingJob):
        """Local subclass — must not inherit the parent's binding."""

    registry = HandlerRegistry()
    registry.register(ExecuteCodingJob, parent_handler)
    assert registry.resolve(_Child) is None
    assert registry.resolve(ExecuteCodingJob) is parent_handler


# --- clear empties ----------------------------------------------------------


def test_clear_empties_bindings() -> None:
    """clear() removes every binding; resolve returns None afterwards."""

    async def handler(message: CommandEnvelope | EventEnvelope) -> None: ...

    registry = HandlerRegistry()
    registry.register(ExecuteCodingJob, handler)
    assert registry.resolve(ExecuteCodingJob) is handler
    registry.clear()
    assert registry.resolve(ExecuteCodingJob) is None


# --- TypeError guards -------------------------------------------------------


def test_register_non_class_message_type_raises_typeerror() -> None:
    """register raises TypeError when message_type is not a class (a string)."""

    async def handler(message: CommandEnvelope | EventEnvelope) -> None: ...

    registry = HandlerRegistry()
    with pytest.raises(TypeError):
        registry.register("ExecuteCodingJob", handler)  # type: ignore[arg-type]


def test_register_instance_message_type_raises_typeerror() -> None:
    """register raises TypeError when message_type is an instance, not a class."""

    async def handler(message: CommandEnvelope | EventEnvelope) -> None: ...

    registry = HandlerRegistry()
    with pytest.raises(TypeError):
        registry.register(42, handler)  # type: ignore[arg-type]


def test_register_non_callable_handler_raises_typeerror() -> None:
    """register raises TypeError when the handler is not callable."""
    registry = HandlerRegistry()
    with pytest.raises(TypeError):
        registry.register(ExecuteCodingJob, "not-a-callable")  # type: ignore[arg-type]


# --- default registry singleton --------------------------------------------


def test_get_default_registry_returns_singleton() -> None:
    """get_default_registry() returns the same object across calls."""
    first = get_default_registry()
    second = get_default_registry()
    assert first is second
    assert isinstance(first, HandlerRegistry)
