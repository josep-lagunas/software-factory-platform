"""Tests for :mod:`sfp_testing.fixtures` (SFP-50 / AC1-AC3).

Covers the deterministic :func:`fake_context` factory and the ``message_context``
pytest fixture:
- (T1/AC1) ``fake_context()`` returns a ``MessageContext`` (isinstance AND exact).
- (T2/AC1) default fields equal the pinned literals (hardcoded in the test).
- (T3/AC1) each of the 5 fields is independently overridable; the other four
  stay at defaults.
- (T4/AC1) a positional call raises ``TypeError`` (keyword-only signature).
- (T5/AC1) all 5 fields overridable at once.
- (T6/AC1) two default calls are pairwise equal across all 5 fields (anti-drift).
- (T7/AC3) the ``message_context`` fixture (requested by name) yields a usable
  default context.
- (T8/AC2) assigning to a field raises ``ValidationError`` (frozen model).
- (T9/AC2) NO AWS/SNS/SQS/boto3/moto/aiobotocore is importable from
  ``sfp_testing.fixtures`` (mirrors ``test_fake_bus.py``'s T9).
- (T10/AC3) ``sfp_testing.fake_context is sfp_testing.fixtures.fake_context``
  (re-export identity) AND ``"fake_context" in sfp_testing.__all__``.
- (T11/AC1) calling ``fake_context`` with and without overrides does NOT mutate
  the module-level default constants.
- (T-E1/AC2) strict-passthrough negatives: ``retry_count="0"`` / ``1.5`` /
  ``received_at=<datetime>`` each raise ``ValidationError``; an unknown kwarg is
  rejected (the closed keyword-only signature raises ``TypeError`` before pydantic,
  which is consistent with — and stricter than — ``extra='forbid'``).

Synchronous throughout (no transport, no registry, no module-global mutable
state): deliberately NO autouse reset fixture and NO ``asyncio.run`` wrapper
(unlike ``test_fake_bus.py``, which needs both because ``publish`` drives the
module-level registry + context ContextVar).
"""

from __future__ import annotations

import copy
import datetime
import sys

import pytest
from pydantic import ValidationError
from sfp_messaging.context import MessageContext

# Import the fixture by name so pytest resolves it from this module's namespace
# (import-based — sfp_testing ships NO [pytest11] pytest plugin entry point).
# message_context is "used" implicitly by pytest (resolved by parameter name),
# not by a direct value reference — hence the F401 suppression here, and the F811
# suppression on the T7 parameter (same name, required for fixture injection).
from sfp_testing.fixtures import fake_context, message_context  # noqa: F401

# The pinned literals, hardcoded HERE (not imported from fixtures) so T2 is a
# real anti-drift check: renaming the constant in fixtures.py without a spec
# change breaks this test, not silently passes it. Must match _DEFAULTS.
_DEFAULT_LITERALS: dict[str, str | int] = {
    "correlation_id": "test-correlation-id",
    "causation_id": "test-causation-id",
    "message_id": "test-message-id",
    "received_at": "2026-07-28T00:00:00Z",
    "retry_count": 0,
}


# --- (T1/AC1) fake_context() returns a MessageContext ------------------------


def test_fake_context_returns_message_context() -> None:
    """(T1/AC1) fake_context() returns a MessageContext (isinstance AND exact)."""
    ctx = fake_context()

    # Exact type — NOT just an instance of a subclass / a duck-typed stand-in.
    assert type(ctx) is MessageContext
    assert isinstance(ctx, MessageContext)


# --- (T2/AC1) default fields equal the pinned literals ----------------------


def test_fake_context_defaults_equal_pinned_literals() -> None:
    """(T2/AC1) default fields equal the pinned literals hardcoded above."""
    ctx = fake_context()

    assert ctx.correlation_id == _DEFAULT_LITERALS["correlation_id"]
    assert ctx.causation_id == _DEFAULT_LITERALS["causation_id"]
    assert ctx.message_id == _DEFAULT_LITERALS["message_id"]
    assert ctx.received_at == _DEFAULT_LITERALS["received_at"]
    assert ctx.retry_count == _DEFAULT_LITERALS["retry_count"]


# --- (T3/AC1) each field independently overridable; others stay at defaults --


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("correlation_id", "corr-override"),
        ("causation_id", "cause-override"),
        ("message_id", "mid-override"),
        ("received_at", "2026-01-02T03:04:05Z"),
        ("retry_count", 7),
    ],
)
def test_fake_context_field_independently_overridable(field: str, value: object) -> None:
    """(T3/AC1) one field overrides; the other four stay at their pinned defaults."""
    ctx = fake_context(**{field: value})  # type: ignore[arg-type]

    # The overridden field took the new value.
    assert getattr(ctx, field) == value

    # Every OTHER field is still at its default literal.
    for other_field, default in _DEFAULT_LITERALS.items():
        if other_field != field:
            assert getattr(ctx, other_field) == default


# --- (T4/AC1) positional call raises TypeError (keyword-only) ---------------


def test_fake_context_positional_call_raises_type_error() -> None:
    """(T4/AC1) a positional call raises TypeError (the signature is keyword-only)."""
    with pytest.raises(TypeError, match="positional"):
        fake_context("corr-override")  # type: ignore[misc]


# --- (T5/AC1) all 5 fields overridable at once ------------------------------


def test_fake_context_all_fields_overridable_at_once() -> None:
    """(T5/AC1) all five fields can be overridden in a single call."""
    ctx = fake_context(
        correlation_id="corr-all",
        causation_id="cause-all",
        message_id="mid-all",
        received_at="2026-02-03T04:05:06Z",
        retry_count=3,
    )

    assert ctx.correlation_id == "corr-all"
    assert ctx.causation_id == "cause-all"
    assert ctx.message_id == "mid-all"
    assert ctx.received_at == "2026-02-03T04:05:06Z"
    assert ctx.retry_count == 3


# --- (T6/AC1) two default calls pairwise equal (anti-drift) -----------------


def test_fake_context_two_default_calls_pairwise_equal() -> None:
    """(T6/AC1) two no-arg calls are equal across all 5 fields (determinism).

    The whole point of pinned literals (no time/uuid): two default contexts are
    value-equal, field by field, so tests that don't pass overrides are stable.
    """
    a = fake_context()
    b = fake_context()

    # Pydantic value-equality (compares all model fields).
    assert a == b
    # And field-by-field, explicitly.
    for field in _DEFAULT_LITERALS:
        assert getattr(a, field) == getattr(b, field)


# --- (T7/AC3) message_context fixture yields a usable default context --------


def test_message_context_fixture_yields_default_context(
    message_context: MessageContext,  # noqa: F811  (shadows imported fixture; name must match for injection)
) -> None:
    """(T7/AC3) the message_context fixture (requested by name) yields a valid default.

    The fixture is import-based (no pytest plugin): pytest resolves it from this
    module's namespace after ``from sfp_testing.fixtures import message_context``.
    """
    ctx = message_context

    assert type(ctx) is MessageContext
    # It is exactly fake_context() with no overrides.
    assert ctx == fake_context()
    for field, default in _DEFAULT_LITERALS.items():
        assert getattr(ctx, field) == default


# --- (T8/AC2) assigning to a field raises ValidationError (frozen) ----------


def test_assigning_field_raises_validation_error() -> None:
    """(T8/AC2) MessageContext is frozen — field assignment raises ValidationError."""
    ctx = fake_context()

    with pytest.raises(ValidationError):
        ctx.correlation_id = "mutated"  # type: ignore[misc]


# --- (T9/AC2) no AWS/SNS/SQS/boto3/moto/aiobotocore importable from fixtures -


def test_no_aws_imports_from_fixtures_module() -> None:
    """(T9/AC2) NO AWS/SNS/SQS/boto3/moto/aiobotocore importable from sfp_testing.fixtures.

    The fixtures module's only imports are ``sfp-messaging`` (which itself imports
    no transport SDK) and ``pytest``. Asserting the forbidden AWS modules are
    absent from ``sys.modules`` after import proves the factory stays
    vendor-clean — tests build contexts without AWS/SNS/SQS/LocalStack (AC2).
    Mirrors ``test_fake_bus.py``'s T9 over ``sfp_testing.bus``.
    """
    import sfp_testing.fixtures as fixtures_mod  # noqa: F401  (import side-effect is the check)

    forbidden = {"boto3", "botocore", "moto", "aiobotocore"}
    present = forbidden & set(sys.modules)
    assert not present, f"fixtures transitively imported AWS libraries: {sorted(present)}"
    # The fixtures module must not expose any AWS attribute either.
    leaked_attrs = {name for name in forbidden if hasattr(fixtures_mod, name)}
    assert not leaked_attrs, f"fixtures exposes AWS attributes: {sorted(leaked_attrs)}"


# --- (T10/AC3) re-export identity + __all__ membership ----------------------


def test_fake_context_reexported_identity_and_all() -> None:
    """(T10/AC3) sfp_testing.fake_context IS sfp_testing.fixtures.fake_context.

    The package-level name is a true re-export (same function object, identity),
    not a redefinition or wrapper; and it is a member of ``sfp_testing.__all__``.
    """
    import sfp_testing
    import sfp_testing.fixtures as fixtures_mod

    assert sfp_testing.fake_context is fixtures_mod.fake_context
    assert "fake_context" in sfp_testing.__all__


# --- (T11/AC1) overrides do not mutate the module-level default constants ----


def test_fake_context_does_not_mutate_module_defaults() -> None:
    """(T11/AC1) calling fake_context (with/without overrides) never mutates _DEFAULTS.

    fake_context builds a FRESH merged dict per call (``{**_DEFAULTS,
    **overrides}``); the module-level ``_DEFAULTS`` template is read-only by
    contract. Snapshot it before, drive both a no-arg and a fully-overridden
    call, then assert the template is byte-for-byte unchanged.
    """
    import sfp_testing.fixtures as fixtures_mod

    snapshot = copy.deepcopy(fixtures_mod._DEFAULTS)

    # A no-arg call reads _DEFAULTS but must not mutate it.
    fake_context()
    # A fully-overridden call merges every key but must still not mutate it.
    fake_context(
        correlation_id="corr-x",
        causation_id="cause-x",
        message_id="mid-x",
        received_at="2026-03-04T05:06:07Z",
        retry_count=9,
    )

    assert fixtures_mod._DEFAULTS == snapshot
    # And a fresh no-arg call still yields the untouched defaults.
    ctx = fake_context()
    for field, default in _DEFAULT_LITERALS.items():
        assert getattr(ctx, field) == default


# --- (T-E1/AC2) strict-passthrough negatives --------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry_count", "0"),  # str -> rejected by strict int
        ("retry_count", 1.5),  # float -> rejected by strict int
        ("received_at", datetime.datetime.now(datetime.UTC)),  # datetime -> not a str
    ],
)
def test_fake_context_strict_validation_rejects_bad_values(field: str, value: object) -> None:
    """(T-E1/AC2) bad-typed values raise ValidationError (strict passthrough).

    fake_context forwards its fields to the PUBLIC MessageContext constructor
    unchanged, so pydantic's strict ``retry_count`` (``int``, no coercion of
    string/float) and ``received_at: str`` (rejects a ``datetime``) apply
    directly — the factory never loosens validation.
    """
    with pytest.raises(ValidationError):
        fake_context(**{field: value})  # type: ignore[arg-type]


def test_fake_context_unknown_kwarg_rejected() -> None:
    """(T-E1/AC2) an unknown kwarg is rejected (closed keyword-only signature).

    fake_context's signature is exactly the five MessageContext fields
    (keyword-only, no ``**kwargs``), so an unknown keyword is rejected at the
    function boundary with ``TypeError`` — before pydantic ever sees it. This is
    consistent with (and stricter than) MessageContext's ``extra='forbid'``: the
    factory cannot forward an extra it does not accept.
    """
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        fake_context(not_a_field="x")  # type: ignore[call-arg]
