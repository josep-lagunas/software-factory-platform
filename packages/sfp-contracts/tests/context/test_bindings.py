"""Tests for ContextBinding / ResolvedContext (SFP-66; ID-071/ID-016).

Covers the acceptance criteria:
- (a) a conformant ContextBinding round-trips through JSON losslessly;
- (b) ContextBinding / ResolvedContext reject extra fields on construction AND
  ``model_validate_json`` (``extra='forbid'``);
- (c) ``ContextBinding.value`` accepts both ``str`` and ``None``;
- (d) a ``SECRET_REF`` binding carries the reference string, never the secret
  value itself (ID-016); a ``STR`` binding carries the ordinary string;
- (e) the exact ``model_fields`` sets on both models;
- (f) ``ResolvedContext`` defaults ``resolved``/``missing`` to empty lists;
- (g) a populated ``ResolvedContext`` round-trips losslessly.
- Parametrized over :data:`DEFAULT_CATALOGUE` entries.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.context.bindings import ContextBinding, ResolvedContext
from sfp_contracts.context.types import (
    DEFAULT_CATALOGUE,
    ContextType,
    ContextTypeKind,
)

VALID_BINDING_KWARGS: dict[str, Any] = {
    "name": "service_repo",
    "context_type": ContextType(name="repo_url", kind=ContextTypeKind.STR),
    "value": "https://github.com/org/repo",
    "source_ticket": "SFP-90",
}


def make_binding(**overrides: Any) -> ContextBinding:
    kwargs = dict(VALID_BINDING_KWARGS)
    kwargs.update(overrides)
    return ContextBinding(**kwargs)


# --- ContextBinding: field set / round-trip / extra='forbid' ---


def test_binding_field_set_exact() -> None:
    """(e) ContextBinding exposes exactly {name, context_type, value, source_ticket}."""
    assert set(ContextBinding.model_fields.keys()) == {
        "name",
        "context_type",
        "value",
        "source_ticket",
    }


def test_binding_round_trip_preserves_every_field() -> None:
    """(a) A conformant binding round-trips through JSON losslessly."""
    original = make_binding()
    restored = ContextBinding.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.name == "service_repo"
    assert restored.value == "https://github.com/org/repo"
    assert restored.source_ticket == "SFP-90"
    assert restored.context_type == ContextType(name="repo_url", kind=ContextTypeKind.STR)


@pytest.mark.parametrize("extra", [{"unexpected": "x"}, {"also_extra": 1}])
def test_binding_extra_fields_rejected_on_construction(extra: dict[str, Any]) -> None:
    """(b) Unknown fields are rejected at binding construction."""
    with pytest.raises(ValidationError):
        make_binding(**extra)


def test_binding_extra_fields_rejected_on_validate_json() -> None:
    """(b) Extra fields are rejected when deserializing a binding."""
    payload = json.loads(make_binding().model_dump_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        ContextBinding.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "field",
    ["name", "context_type", "value", "source_ticket"],
)
def test_binding_missing_required_field_raises(field: str) -> None:
    """Dropping any required field on ContextBinding raises ValidationError."""
    kwargs: dict[str, Any] = dict(VALID_BINDING_KWARGS)
    kwargs.pop(field)
    with pytest.raises(ValidationError):
        ContextBinding(**kwargs)


# --- value: str | None ---


@pytest.mark.parametrize(
    "value",
    ["a-string-value", "arn:aws:secretsmanager:us-east-1:123:secret:xyz", ""],
)
def test_binding_value_accepts_str(value: str) -> None:
    """(c) value accepts any string."""
    binding = make_binding(value=value)
    assert binding.value == value


def test_binding_value_accepts_none() -> None:
    """(c) value accepts None (a not-yet-materialised binding)."""
    binding = make_binding(value=None)
    assert binding.value is None


def test_binding_value_none_round_trips() -> None:
    """(c) A None value survives a JSON round-trip as null."""
    binding = make_binding(value=None)
    restored = ContextBinding.model_validate_json(binding.model_dump_json())
    assert restored.value is None
    assert restored == binding


# --- Parametrized over DEFAULT_CATALOGUE entries ---


@pytest.mark.parametrize(
    "entry",
    DEFAULT_CATALOGUE.entries,
    ids=[e.name for e in DEFAULT_CATALOGUE.entries],
)
def test_binding_accepts_every_catalogue_entry(entry: ContextType) -> None:
    """A ContextBinding can instantiate any DEFAULT_CATALOGUE entry as context_type."""
    binding = ContextBinding(
        name=f"binding_for_{entry.name}",
        context_type=entry,
        value="some-ref" if entry.kind is ContextTypeKind.SECRET_REF else "some-value",
        source_ticket="SFP-1",
    )
    assert binding.context_type == entry
    assert binding.context_type.kind is entry.kind


# --- ID-016: SECRET_REF carries ref, never the secret ---


def test_secret_ref_binding_carries_ref_not_secret() -> None:
    """(d) A SECRET_REF binding carries the reference string, never the secret (ID-016)."""
    ref = "arn:aws:secretsmanager:us-east-1:123:secret:db-abc"
    binding = ContextBinding(
        name="db_secret",
        context_type=ContextType(name="db_secret_arn", kind=ContextTypeKind.SECRET_REF),
        value=ref,
        source_ticket="SFP-91",
    )
    assert binding.context_type.is_secret
    assert binding.value == ref
    # The serialized form carries the ref string (the locator), as intended.
    serialized = binding.model_dump_json()
    assert ref in serialized


def test_str_binding_carries_plain_value() -> None:
    """(d) A STR binding carries the ordinary string value."""
    binding = ContextBinding(
        name="repo",
        context_type=ContextType(name="repo_url", kind=ContextTypeKind.STR),
        value="https://github.com/org/repo",
        source_ticket="SFP-92",
    )
    assert not binding.context_type.is_secret
    assert binding.value == "https://github.com/org/repo"


# --- ResolvedContext ---


def test_resolved_field_set_exact() -> None:
    """(e) ResolvedContext exposes exactly {ticket_id, resolved, missing}."""
    assert set(ResolvedContext.model_fields.keys()) == {"ticket_id", "resolved", "missing"}


def test_resolved_defaults_empty_lists() -> None:
    """(f) resolved/missing default to empty lists (distinct instances)."""
    ctx = ResolvedContext(ticket_id="SFP-1")
    assert ctx.resolved == []
    assert ctx.missing == []
    assert ctx.resolved is not ctx.missing


def test_resolved_ticket_id_required() -> None:
    """Omitting ticket_id raises ValidationError."""
    with pytest.raises(ValidationError):
        ResolvedContext()  # type: ignore[call-arg]


def test_resolved_extra_fields_rejected_on_construction() -> None:
    """(b) Unknown fields are rejected at ResolvedContext construction."""
    with pytest.raises(ValidationError):
        ResolvedContext(ticket_id="SFP-1", unexpected="x")  # type: ignore[call-arg]


def test_resolved_extra_fields_rejected_on_validate_json() -> None:
    """(b) Extra fields are rejected when deserializing a ResolvedContext."""
    ctx = ResolvedContext(ticket_id="SFP-1")
    payload = json.loads(ctx.model_dump_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        ResolvedContext.model_validate_json(json.dumps(payload))


def test_resolved_round_trip_empty() -> None:
    """(a) An empty ResolvedContext round-trips losslessly."""
    ctx = ResolvedContext(ticket_id="SFP-1")
    restored = ResolvedContext.model_validate_json(ctx.model_dump_json())

    assert restored == ctx
    assert restored.ticket_id == "SFP-1"
    assert restored.resolved == []
    assert restored.missing == []


def test_resolved_round_trip_populated() -> None:
    """(g) A populated ResolvedContext round-trips losslessly."""
    bindings = [
        make_binding(),
        ContextBinding(
            name="acct",
            context_type=ContextType(name="aws_account_id", kind=ContextTypeKind.STR),
            value="123456789012",
            source_ticket="SFP-93",
        ),
    ]
    ctx = ResolvedContext(ticket_id="SFP-7", resolved=bindings, missing=["db_dsn"])
    restored = ResolvedContext.model_validate_json(ctx.model_dump_json())

    assert restored == ctx
    assert restored.ticket_id == "SFP-7"
    assert [b.name for b in restored.resolved] == ["service_repo", "acct"]
    assert restored.missing == ["db_dsn"]
