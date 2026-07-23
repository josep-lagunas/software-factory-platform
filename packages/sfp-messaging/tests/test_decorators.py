"""Tests for the @command_handler / @event_handler decorators (SFP-43 / AC1-AC2).

Covers the declarative decorator layer above :class:`HandlerRegistry`:
- (AC1) ``@command_handler`` / ``@event_handler`` register the decorated callable
  in the module-level DEFAULT registry keyed by the concrete message class;
- (AC2) the decorators perform NO business logic — the decorated callable is
  returned UNCHANGED (``command_handler(T)(f) is f``);
- concrete-class keying (TD-06): resolving the ``CommandType`` enum member
  yields ``None`` while resolving the concrete class yields the handler;
- no ``MessageContext`` coupling (TD-11): the decorators source contains no
  ``MessageContext`` token (sibling SFP-44 is out of scope);
- re-export identity: the package-level names are the very objects in their
  submodules, and the module-private ``_default_registry`` is NOT re-exported
  at the package top level.

The default registry is module-global and shared, so an autouse fixture clears
it before and after every test. Decorators are applied imperatively inside each
test (never at module scope) so import-time registration is never wiped by the
clear.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest
import sfp_messaging
import sfp_messaging.decorators as decorators_module
from sfp_contracts.commands.envelope import CommandEnvelope, CommandType
from sfp_contracts.commands.payloads import ExecuteCodingJob, RequestMerge
from sfp_contracts.events.envelope import EventEnvelope
from sfp_contracts.events.payloads import UserInputReceived, UserQueryReceived
from sfp_messaging import (
    HandlerRegistry,
    command_handler,
    event_handler,
    get_default_registry,
)

type _Handler = Callable[[CommandEnvelope | EventEnvelope], Awaitable[None]]


@pytest.fixture(autouse=True)
def clean_default_registry() -> Iterator[None]:
    """Clear the module-level default registry before and after each test."""
    get_default_registry().clear()
    yield
    get_default_registry().clear()


# --- (AC1) decorators register into the default registry -------------------


def test_command_handler_registers_in_default_registry() -> None:
    """(AC1) @command_handler(ExecuteCodingJob) registers fn keyed by the class."""

    @command_handler(ExecuteCodingJob)
    async def fn(message: CommandEnvelope) -> None: ...

    assert get_default_registry().resolve(ExecuteCodingJob) is fn


def test_event_handler_registers_in_default_registry() -> None:
    """(AC1) @event_handler(UserInputReceived) registers fn keyed by the class."""

    @event_handler(UserInputReceived)
    async def fn(message: EventEnvelope) -> None: ...

    assert get_default_registry().resolve(UserInputReceived) is fn


# --- (AC2) decorators perform no business logic ----------------------------


def test_command_handler_returns_callable_unchanged() -> None:
    """(AC2) command_handler returns the callable verbatim (object identity)."""

    async def fn(message: CommandEnvelope) -> None: ...

    assert command_handler(ExecuteCodingJob)(fn) is fn


def test_event_handler_returns_callable_unchanged() -> None:
    """(AC2) event_handler returns the callable verbatim (object identity)."""

    async def fn(message: EventEnvelope) -> None: ...

    assert event_handler(UserInputReceived)(fn) is fn


# --- concrete-class keying, not the CommandType enum (TD-06) ---------------


def test_concrete_class_keying_not_enum() -> None:
    """(TD-06) resolve(ExecuteCodingJob) is fn; resolve(CommandType member) is None."""

    @command_handler(ExecuteCodingJob)
    async def fn(message: CommandEnvelope) -> None: ...

    assert get_default_registry().resolve(ExecuteCodingJob) is fn
    assert get_default_registry().resolve(CommandType.EXECUTE_CODING_JOB) is None


# --- two concrete types route independently --------------------------------


def test_two_command_handlers_route_independently() -> None:
    """Two command handlers under distinct classes resolve to their own fn."""

    @command_handler(ExecuteCodingJob)
    async def job_fn(message: CommandEnvelope) -> None: ...

    @command_handler(RequestMerge)
    async def merge_fn(message: CommandEnvelope) -> None: ...

    registry = get_default_registry()
    assert registry.resolve(ExecuteCodingJob) is job_fn
    assert registry.resolve(RequestMerge) is merge_fn


def test_two_event_handlers_route_independently() -> None:
    """Two event handlers under distinct classes resolve to their own fn."""

    @event_handler(UserInputReceived)
    async def input_fn(message: EventEnvelope) -> None: ...

    @event_handler(UserQueryReceived)
    async def query_fn(message: EventEnvelope) -> None: ...

    registry = get_default_registry()
    assert registry.resolve(UserInputReceived) is input_fn
    assert registry.resolve(UserQueryReceived) is query_fn


def test_command_and_event_handlers_coexist() -> None:
    """A command handler and an event handler coexist in the default registry."""

    @command_handler(ExecuteCodingJob)
    async def cmd_fn(message: CommandEnvelope) -> None: ...

    @event_handler(UserInputReceived)
    async def evt_fn(message: EventEnvelope) -> None: ...

    registry = get_default_registry()
    assert registry.resolve(ExecuteCodingJob) is cmd_fn
    assert registry.resolve(UserInputReceived) is evt_fn


# --- no MessageContext coupling (TD-11) ------------------------------------


def test_decorators_source_has_no_message_context_token() -> None:
    """(TD-11) decorators.py contains no 'MessageContext' token (sibling SFP-44)."""
    source = Path(decorators_module.__file__).read_text()
    assert "MessageContext" not in source


# --- re-export identity & module-private default registry ------------------


def test_reexported_names_are_the_submodule_objects() -> None:
    """Package-level names ARE the objects in their submodules."""
    assert sfp_messaging.command_handler is decorators_module.command_handler
    assert sfp_messaging.event_handler is decorators_module.event_handler

    import sfp_messaging.registry as registry_module

    assert sfp_messaging.HandlerRegistry is registry_module.HandlerRegistry
    assert sfp_messaging.get_default_registry is registry_module.get_default_registry


def test_default_registry_not_reexported_at_package_top_level() -> None:
    """The module-private _default_registry is NOT a package-level attribute."""
    assert not hasattr(sfp_messaging, "_default_registry")


def test_get_default_registry_returns_registry_instance() -> None:
    """get_default_registry re-export returns a HandlerRegistry instance."""
    assert isinstance(get_default_registry(), HandlerRegistry)
