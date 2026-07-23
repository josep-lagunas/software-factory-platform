"""Tests for ``sfp_messaging.context`` (SFP-44 / AC1-AC9).

Covers the :class:`MessageContext` value object and its contextvars access API:
- (AC1/T1) MessageContext exposes exactly 5 fields, in declaration order;
- (AC2/T2) ``extra='forbid'`` rejects unknown fields;
- (AC3/T3) ``received_at`` is annotated ``str`` (a ``datetime`` is rejected);
- (AC4/T4) ``retry_count`` is a strict ``int`` (float + numeric-str rejected);
- (AC5/T5) the model is frozen (mutation raises ``ValidationError``);
- (AC6/T6) ``_build_message_context`` is a keyword-only factory round-trip;
- (AC5/T7-T10) the contextvars access API mirrors ``sfp_observability`` —
  bind sets/restores (T7), LIFO nesting (T8), unset raises ``LookupError`` (T9),
  and bind resets even when the block raises (T10);
- (AC6/T11) ``_build_message_context`` is NOT in the public namespace;
- (AC7/T12) the module-private ``_Handler`` alias in registry.py AND
  decorators.py is ``(Any, MessageContext)``-shaped (no envelope types);
- (AC9/T13) decoupling guard — ``context.py`` imports no ``sfp_observability``;
- (AC8/T14) re-export identity (``is``) + ``__all__`` set-equality and sorted.

The ContextVar is module-global and survives across tests in the same process,
so an autouse fixture resets it before and after every test (mirroring
``sfp_observability``'s ``_pristine_context``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
import sfp_messaging
import sfp_messaging.context as context_module
import sfp_messaging.decorators as decorators_module
import sfp_messaging.registry as registry_module
from pydantic import ValidationError
from sfp_contracts.commands.envelope import CommandEnvelope
from sfp_contracts.events.envelope import EventEnvelope
from sfp_messaging import MessageContext, bind_message_context, get_current_context
from sfp_messaging.context import _build_message_context


@pytest.fixture(autouse=True)
def _pristine_message_context() -> Iterator[None]:
    """Reset the module-private ContextVar before and after every test.

    ContextVars survive across tests in the same process; resetting before AND
    after each test prevents cross-test leakage that would otherwise produce
    flaky 'context bound' / 'context unset' assertions.
    """
    context_module._current_context.set(None)
    yield
    context_module._current_context.set(None)


def _ctx(**overrides: Any) -> MessageContext:
    """Build a MessageContext for bind/get tests via the framework factory."""
    fields: dict[str, Any] = dict(
        correlation_id="corr-1",
        causation_id="caus-1",
        message_id="msg-1",
        received_at="2026-07-23T00:00:00Z",
        retry_count=0,
    )
    fields.update(overrides)
    return _build_message_context(**fields)


# --- (AC1/T1) field set + order --------------------------------------------


def test_message_context_has_exactly_five_fields_in_order() -> None:
    """(T1/AC1) MessageContext exposes exactly the 5 fields, in declaration order."""
    expected = [
        "correlation_id",
        "causation_id",
        "message_id",
        "received_at",
        "retry_count",
    ]
    assert list(MessageContext.model_fields.keys()) == expected


# --- (AC2/T2) extra='forbid' -----------------------------------------------


def test_message_context_rejects_extra_fields() -> None:
    """(T2/AC2) extra='forbid' — an unknown field on the model raises ValidationError.

    The model is exercised directly: the module-private factory has a fixed
    keyword-only signature, so it would raise ``TypeError`` before the model's
    ``extra='forbid'`` ever ran. The decoupling target of this AC is the model.
    """
    with pytest.raises(ValidationError):
        MessageContext(
            correlation_id="c",
            causation_id="u",
            message_id="m",
            received_at="2026-07-23T00:00:00Z",
            retry_count=0,
            unexpected="nope",
        )


# --- (AC3/T3) received_at is str -------------------------------------------


def test_received_at_is_str_not_datetime() -> None:
    """(T3/AC3) received_at is annotated str; a datetime instance is rejected."""
    assert MessageContext.model_fields["received_at"].annotation is str
    with pytest.raises(ValidationError):
        _build_message_context(
            correlation_id="c",
            causation_id="u",
            message_id="m",
            received_at=datetime(2026, 7, 23, tzinfo=UTC),
            retry_count=0,
        )


# --- (AC4/T4) retry_count is int -------------------------------------------


def test_retry_count_must_be_a_strict_int() -> None:
    """(T4/AC4) retry_count is a strict int — a float and a numeric str are rejected."""
    base: dict[str, Any] = dict(
        correlation_id="c",
        causation_id="u",
        message_id="m",
        received_at="2026-07-23T00:00:00Z",
    )
    with pytest.raises(ValidationError):
        _build_message_context(**{**base, "retry_count": 1.5})
    with pytest.raises(ValidationError):
        _build_message_context(**{**base, "retry_count": "0"})


# --- (AC5/T5) frozen --------------------------------------------------------


def test_message_context_is_frozen() -> None:
    """(T5/AC5 frozen) Mutation of any field raises ValidationError."""
    ctx = _build_message_context(
        correlation_id="c",
        causation_id="u",
        message_id="m",
        received_at="2026-07-23T00:00:00Z",
        retry_count=0,
    )
    with pytest.raises(ValidationError):
        ctx.retry_count = 1
    with pytest.raises(ValidationError):
        ctx.correlation_id = "changed"


# --- (AC6/T6) keyword-only factory round-trip ------------------------------


def test_build_message_context_round_trip_and_keyword_only() -> None:
    """(T6/AC6) factory round-trips all 5 fields; a positional call raises TypeError."""
    ctx = _build_message_context(
        correlation_id="corr",
        causation_id="caus",
        message_id="msg",
        received_at="2026-07-23T00:00:00Z",
        retry_count=2,
    )
    assert ctx.correlation_id == "corr"
    assert ctx.causation_id == "caus"
    assert ctx.message_id == "msg"
    assert ctx.received_at == "2026-07-23T00:00:00Z"
    assert ctx.retry_count == 2

    # Keyword-only: a positional call must raise TypeError.
    with pytest.raises(TypeError):
        _build_message_context("corr", "caus", "msg", "2026-07-23T00:00:00Z", 2)  # type: ignore[misc]


# --- (AC5/T7) bind sets and restores ---------------------------------------


def test_bind_sets_current_context_and_restores_after_exit() -> None:
    """(T7/AC5) get_current_context() is ctx in-block; unset after exit."""
    ctx = _ctx()
    with bind_message_context(ctx):
        assert get_current_context() is ctx
    with pytest.raises(LookupError):
        get_current_context()


# --- (AC5/T8) nesting is LIFO ----------------------------------------------


def test_bind_nesting_restores_lifo() -> None:
    """(T8/AC5) Nested binds restore LIFO; each level sees its own ctx (identity)."""
    ctx1 = _ctx(message_id="m1")
    ctx2 = _ctx(message_id="m2")
    ctx3 = _ctx(message_id="m3")
    with bind_message_context(ctx1):
        assert get_current_context() is ctx1
        with bind_message_context(ctx2):
            assert get_current_context() is ctx2
            with bind_message_context(ctx3):
                assert get_current_context() is ctx3
            assert get_current_context() is ctx2
        assert get_current_context() is ctx1
    with pytest.raises(LookupError):
        get_current_context()


# --- (AC5/T9) unset raises LookupError (exact type) ------------------------


def test_get_current_context_unset_raises_lookup_error() -> None:
    """(T9/AC5) get_current_context() raises LookupError (exact type) when unset."""
    with pytest.raises(LookupError) as exc_info:
        get_current_context()
    assert type(exc_info.value) is LookupError


# --- (AC5/T10) bind resets on exception (propagates unchanged) -------------


def test_bind_resets_on_exception_and_propagates() -> None:
    """(T10/AC5) bind resets the ContextVar even when the block raises; the
    exception propagates unchanged (NOT suppressed)."""

    class _Boom(Exception):
        pass

    ctx = _ctx()
    with pytest.raises(_Boom, match="boom"):
        with bind_message_context(ctx):
            assert get_current_context() is ctx
            raise _Boom("boom")

    with pytest.raises(LookupError):
        get_current_context()


# --- (AC6/T11) _build_message_context not in public namespace --------------


def test_build_message_context_not_in_public_namespace() -> None:
    """(T11/AC6) _build_message_context is NOT re-exported (framework-only)."""
    assert not hasattr(sfp_messaging, "_build_message_context")
    assert "_build_message_context" not in sfp_messaging.__all__


# --- (AC7/T12) _Handler alias is (Any, MessageContext)-shaped --------------


@pytest.mark.parametrize(
    "module",
    [registry_module, decorators_module],
    ids=["registry", "decorators"],
)
def test_handler_alias_is_payload_message_context_shaped(module: Any) -> None:
    """(T12/AC7) _Handler resolves to Callable[[Any, MessageContext], Awaitable[None]].

    A PEP 695 ``type`` alias resolves its RHS lazily via ``__value__`` (the
    type-hint resolution path for such aliases). The resolved parameter list
    must contain ``MessageContext`` and ``Any``, and must NOT contain either
    envelope type (CommandEnvelope / EventEnvelope).
    """
    resolved = module._Handler.__value__
    assert get_origin(resolved) is Callable

    params, ret = get_args(resolved)
    assert MessageContext in params
    assert Any in params
    # No envelope types leaked into the handler signature.
    assert CommandEnvelope not in params
    assert EventEnvelope not in params
    # Return type is Awaitable[...].
    assert get_origin(ret) is Awaitable


# --- (AC9/T13) decoupling guard --------------------------------------------


def test_context_module_is_decoupled_from_observability() -> None:
    """(T13/AC9) context.py imports no sfp_observability (zero runtime deps)."""
    source = Path(context_module.__file__).read_text()
    assert "sfp_observability" not in source


# --- (AC8/T14) re-export identity + __all__ pinned/sorted ------------------


def test_reexport_identity_and_all_pinned_and_sorted() -> None:
    """(T14/AC8) re-exported names ARE submodule objects; __all__ is correct + sorted."""
    assert sfp_messaging.MessageContext is context_module.MessageContext
    assert sfp_messaging.get_current_context is context_module.get_current_context
    assert sfp_messaging.bind_message_context is context_module.bind_message_context

    expected = {
        "HandlerRegistry",
        "MessageBus",
        "MessageContext",
        "bind_message_context",
        "command_handler",
        "event_handler",
        "get_current_context",
        "get_default_registry",
    }
    assert set(sfp_messaging.__all__) == expected
    assert sfp_messaging.__all__ == sorted(sfp_messaging.__all__)
