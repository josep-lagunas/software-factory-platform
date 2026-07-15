"""Tests for the versioned context-types catalogue (SFP-36).

Covers the acceptance criteria:
- (a) catalogue round-trips through ``to_json``/``from_json``;
- (b) extra fields rejected on construction AND ``from_json`` (extra='forbid');
- (c) ``schema_version`` is present and preserved across a round-trip (ID-071);
- (d) ``secret_ref`` entries are marked as secrets and carry NO value field —
  the secret value is never serialized (ID-016);
- (e) the required example entries exist in the default catalogue with the
  correct kinds;
- (f) ``ContextTypeKind`` serializes to plain string values (ID-013);
- (g) malformed JSON is rejected.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.context.types import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_CATALOGUE,
    ContextCatalogue,
    ContextType,
    ContextTypeKind,
)

#: The canonical cross-ticket entries the default catalogue must expose, mapped
#: to their required kind markers (ID-016 / ID-071).
REQUIRED_ENTRIES: dict[str, ContextTypeKind] = {
    "repo_url": ContextTypeKind.STR,
    "db_endpoint": ContextTypeKind.STR,
    "db_secret_arn": ContextTypeKind.SECRET_REF,
    "aws_account_id": ContextTypeKind.STR,
    "llm_provider_secret_ref": ContextTypeKind.SECRET_REF,
}

VALID_CATALOGUE_KWARGS: dict[str, Any] = {
    "schema_version": CURRENT_SCHEMA_VERSION,
    "entries": [
        ContextType(name="repo_url", kind=ContextTypeKind.STR),
        ContextType(name="db_secret_arn", kind=ContextTypeKind.SECRET_REF),
    ],
}


def make_catalogue(**overrides: Any) -> ContextCatalogue:
    kwargs = dict(VALID_CATALOGUE_KWARGS)
    kwargs.update(overrides)
    return ContextCatalogue(**kwargs)


def test_catalogue_round_trip_preserves_every_field() -> None:
    """(a) A conformant catalogue round-trips through JSON losslessly."""
    original = make_catalogue()
    restored = ContextCatalogue.from_json(original.to_json())

    assert restored == original
    assert restored.schema_version == CURRENT_SCHEMA_VERSION
    assert [e.model_dump() for e in restored.entries] == [e.model_dump() for e in original.entries]


@pytest.mark.parametrize("extra", [{"unexpected": "x"}, {"entries_extra": []}])
def test_catalogue_extra_fields_rejected_on_construction(
    extra: dict[str, Any],
) -> None:
    """(b) Unknown fields are rejected at catalogue construction."""
    with pytest.raises(ValidationError):
        make_catalogue(**extra)


def test_catalogue_extra_fields_rejected_on_from_json() -> None:
    """(b) Extra fields are rejected when deserializing (extra='forbid')."""
    payload = json.loads(make_catalogue().to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        ContextCatalogue.from_json(json.dumps(payload))


@pytest.mark.parametrize("extra", [{"unexpected": "x"}, {"value": "hunter2"}])
def test_context_type_extra_fields_rejected(extra: dict[str, Any]) -> None:
    """(b) Unknown fields (including a value) are rejected on ContextType."""
    kwargs: dict[str, Any] = {"name": "repo_url", "kind": ContextTypeKind.STR}
    kwargs.update(extra)
    with pytest.raises(ValidationError):
        ContextType(**kwargs)


def test_schema_version_required() -> None:
    """(c) Omitting schema_version raises ValidationError (ID-071)."""
    with pytest.raises(ValidationError):
        ContextCatalogue(entries=[])  # type: ignore[call-arg]


def test_schema_version_present_and_preserved() -> None:
    """(c) schema_version is present and survives a round-trip (ID-071)."""
    catalogue = make_catalogue(schema_version="2")
    assert catalogue.schema_version == "2"
    restored = ContextCatalogue.from_json(catalogue.to_json())
    assert restored.schema_version == "2"


def test_entries_default_empty_when_omitted() -> None:
    """entries defaults to an empty list when omitted."""
    catalogue = ContextCatalogue(schema_version="1")
    assert catalogue.entries == []


def test_context_type_has_no_value_field() -> None:
    """(d) A ContextType exposes only name + kind — there is no value field."""
    assert set(ContextType.model_fields.keys()) == {"name", "kind"}


def test_secret_ref_entry_never_serializes_a_value() -> None:
    """(d) A secret_ref entry carries no value; the value never serializes (ID-016)."""
    secret_entry = ContextType(name="db_secret_arn", kind=ContextTypeKind.SECRET_REF)

    # A secret value cannot be supplied (extra='forbid').
    with pytest.raises(ValidationError):
        ContextType(  # type: ignore[call-arg]
            name="db_secret_arn", kind=ContextTypeKind.SECRET_REF, value="hunter2"
        )

    # The serialized JSON carries the marker, never an actual secret value.
    serialized = secret_entry.model_dump_json()
    assert "secret_ref" in serialized
    assert "hunter2" not in serialized
    assert "value" not in serialized


def test_secret_value_never_in_catalogue_serialization() -> None:
    """(d) A catalogue carrying a secret_ref entry serializes no secret value."""
    json_str = make_catalogue().to_json()
    assert "secret_ref" in json_str
    assert "hunter2" not in json_str
    assert '"value"' not in json_str


@pytest.mark.parametrize(
    "name,kind",
    [
        ("db_secret_arn", ContextTypeKind.SECRET_REF),
        ("repo_url", ContextTypeKind.STR),
    ],
)
def test_is_secret_reflects_kind(name: str, kind: ContextTypeKind) -> None:
    """(d) is_secret is True only for secret_ref entries (ID-016)."""
    entry = ContextType(name=name, kind=kind)
    assert entry.is_secret is (kind is ContextTypeKind.SECRET_REF)


def test_default_catalogue_contains_required_entries() -> None:
    """(e) The default catalogue exposes the required example entries."""
    by_name = {e.name: e for e in DEFAULT_CATALOGUE.entries}
    for name, kind in REQUIRED_ENTRIES.items():
        assert name in by_name, f"missing required entry: {name}"
        assert by_name[name].kind is kind


def test_default_catalogue_is_versioned() -> None:
    """(e) The default catalogue carries a non-empty schema_version (ID-071)."""
    assert DEFAULT_CATALOGUE.schema_version == CURRENT_SCHEMA_VERSION
    assert DEFAULT_CATALOGUE.schema_version


def test_default_catalogue_round_trips() -> None:
    """(e) The default catalogue round-trips and stays equal."""
    restored = ContextCatalogue.from_json(DEFAULT_CATALOGUE.to_json())
    assert restored == DEFAULT_CATALOGUE


def test_kind_values_are_plain_strings() -> None:
    """(f) ContextTypeKind serializes to plain lowercase string values (ID-013)."""
    assert {k.value for k in ContextTypeKind} == {"str", "secret_ref"}


@pytest.mark.parametrize("kind", list(ContextTypeKind))
def test_kind_round_trips_as_enum(kind: ContextTypeKind) -> None:
    """(f) A kind survives a catalogue round-trip as the same enum member."""
    catalogue = make_catalogue(
        entries=[ContextType(name="x", kind=kind)],
    )
    restored = ContextCatalogue.from_json(catalogue.to_json())
    assert restored.entries[0].kind is kind


def test_kind_accepts_string_value() -> None:
    """A kind may be supplied as its plain string value."""
    assert ContextType(name="db_secret_arn", kind="secret_ref").kind is ContextTypeKind.SECRET_REF


def test_invalid_kind_string_rejected() -> None:
    """A kind string outside the enum is rejected."""
    with pytest.raises(ValidationError):
        ContextType.model_validate(json.dumps({"name": "x", "kind": "password"}))


def test_malformed_json_rejected() -> None:
    """(g) Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        ContextCatalogue.from_json("{not valid json")
