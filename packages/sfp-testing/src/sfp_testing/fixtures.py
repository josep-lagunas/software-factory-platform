"""The :func:`fake_context` factory + ``message_context`` fixture (SFP-50).

A deterministic factory for :class:`~sfp_messaging.context.MessageContext` plus
a thin pytest fixture over it. Tests (and the rest of sfp-testing) need a way to
build a valid context WITHOUT the framework envelope/dispatch path: the bus fake
(SFP-49) dissolves a real envelope into a context, but handler-adjacent unit
tests want a context they can poke at directly — without constructing an
envelope, without a registry, and without any clock or UUID source that would
make two calls disagree.

Grounded in:
- MAS §4.7 — the envelope dissolves into a MessageContext; the handler receives
  ``(payload, context)``. This factory produces exactly that context shape,
  outside any dispatch, so a test can stand one up by hand.
- Impl Notes §1 — the v0 ``MessageContext`` carries exactly five fields
  (``correlation_id``, ``causation_id``, ``message_id``, ``received_at``,
  ``retry_count``); these are the five keyword-only knobs ``fake_context``
  exposes, in declaration order.
- ID-049 — the sfp-testing package exists to host exactly these test doubles.
- SFP-44 — MessageContext (frozen, ``extra='forbid'``); this factory constructs
  it via the PUBLIC ``MessageContext(...)`` constructor, mirroring how a test
  author would — it does NOT import the module-private
  :func:`sfp_messaging.context._build_message_context` (that path is reserved for
  framework dispatch).
- SFP-49 — the sibling FakeBus; this module mirrors its determinism and
  no-AWS-leak guarantees.

Determinism contract (the whole point):
- Every default is a PINNED module-level literal. There is NO ``time.time()``,
  ``datetime.now()``, or ``uuid4()`` at import or anywhere in the default path.
  Two ``fake_context()`` calls with no overrides are pairwise equal across all
  five fields (T6). Tests that need distinct values pass overrides explicitly.
- ``fake_context`` builds a FRESH merged dict per call (``{**_DEFAULTS,
  **overrides}``); the module-level ``_DEFAULTS`` template is never mutated in
  place, so a caller's overrides can never leak into a later call (T11).

No pytest plugin (SFP-50 decision): the ``message_context`` fixture is
import-based — a test does ``from sfp_testing.fixtures import message_context``
and pytest resolves it from the module namespace. There is deliberately NO
``[pytest11]`` entry point in ``pyproject.toml``: sfp-testing is a normal
importable package, not an auto-installed pytest plugin.

No AWS leak (mirrors ``bus.py``): the only import is ``sfp-messaging`` (which
itself imports no transport SDK) and ``pytest``. No boto3/botocore/moto/
aiobotocore is imported here or transitively (T9).
"""

from __future__ import annotations

import pytest
from sfp_messaging.context import MessageContext

# --- Module-level deterministic defaults -------------------------------------
# Pinned literals — NO time.time()/datetime.now()/uuid4() at import or in the
# default path. Private (leading underscore): the public surface is fake_context
# and the message_context fixture, not these constants. A test asserts against
# the SAME literals hardcoded in its own body (T2), so renaming here without the
# spec is a visible break.

_DEFAULT_CORRELATION_ID: str = "test-correlation-id"
_DEFAULT_CAUSATION_ID: str = "test-causation-id"
_DEFAULT_MESSAGE_ID: str = "test-message-id"
#: ISO-8601 ``str`` (NOT ``datetime``) — mirrors MessageContext.received_at /
#: MessageEnvelope.occurred_at. Fixed, not wall-clock.
_DEFAULT_RECEIVED_AT: str = "2026-07-28T00:00:00Z"
_DEFAULT_RETRY_COUNT: int = 0

#: The merged-defaults template. ``fake_context`` builds a FRESH copy per call
#: (``{**_DEFAULTS, **overrides}``) — this shared dict is read-only by contract
#: and is never mutated in place, so one call's overrides cannot leak into the
#: next (T11).
_DEFAULTS: dict[str, str | int] = {
    "correlation_id": _DEFAULT_CORRELATION_ID,
    "causation_id": _DEFAULT_CAUSATION_ID,
    "message_id": _DEFAULT_MESSAGE_ID,
    "received_at": _DEFAULT_RECEIVED_AT,
    "retry_count": _DEFAULT_RETRY_COUNT,
}


def fake_context(
    *,
    correlation_id: str = _DEFAULT_CORRELATION_ID,
    causation_id: str = _DEFAULT_CAUSATION_ID,
    message_id: str = _DEFAULT_MESSAGE_ID,
    received_at: str = _DEFAULT_RECEIVED_AT,
    retry_count: int = _DEFAULT_RETRY_COUNT,
) -> MessageContext:
    """Build a deterministic :class:`~sfp_messaging.context.MessageContext`.

    All five fields are keyword-only (the signature carries ``*,``); parameter
    order and names match ``MessageContext`` exactly. Each defaults to its
    module-level pinned constant, so ``fake_context()`` with no args is fully
    deterministic — two such calls are pairwise equal across all five fields
    (T6). Pass overrides to vary the fields a test actually cares about.

    Construction goes through the PUBLIC ``MessageContext(...)`` constructor
    (NOT the module-private ``_build_message_context``): a test author building a
    context by hand would use the public constructor, and this factory mirrors
    that. Pydantic's ``extra='forbid'`` and ``retry_count`` strict validation
    therefore apply unchanged (T8 / T-E1).

    Args:
        correlation_id: the causal chain this message belongs to.
        causation_id: the message that caused this one.
        message_id: this message's identity.
        received_at: when the framework received it (ISO-8601 ``str``).
        retry_count: how many times dispatch has already retried this message.

    Returns:
        A fresh, frozen :class:`~sfp_messaging.context.MessageContext`.
    """
    # Fresh dict per call: the caller's overrides are merged ONTO a copy of the
    # defaults template, never into the shared _DEFAULTS (T11). Constructed via
    # the PUBLIC MessageContext constructor (NOT _build_message_context).
    merged: dict[str, str | int] = {
        **_DEFAULTS,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "message_id": message_id,
        "received_at": received_at,
        "retry_count": retry_count,
    }
    # pydantic tightens MessageContext.__init__ to its per-field types
    # (correlation_id: str, ..., retry_count: int), so mypy cannot prove the
    # heterogeneous ``**merged`` (``dict[str, str | int]``) assigns even though
    # every key holds a correctly-typed value by construction. This is the
    # spec-mandated PUBLIC construction path; the module deliberately imports no
    # ``typing`` helpers (TypedDict/cast/Any), so suppress the narrow arg-type
    # check here rather than widen the import surface.
    return MessageContext(**merged)  # type: ignore[arg-type]


@pytest.fixture
def message_context() -> MessageContext:
    """A default :class:`~sfp_messaging.context.MessageContext` (function scope).

    Returns :func:`fake_context` with no overrides — a valid, deterministic
    context for any test that just needs "a context" without standing one up by
    hand. Import-based (NOT a pytest plugin): a test resolves this fixture by
    name after ``from sfp_testing.fixtures import message_context``.

    Returns:
        A fresh, frozen :class:`~sfp_messaging.context.MessageContext`.
    """
    return fake_context()
