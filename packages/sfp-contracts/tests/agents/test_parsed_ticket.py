"""Tests for the :class:`ParsedTicket` schema (SFP-67).

Covers the acceptance criteria:
- (a) the field set is exactly the eight ID-070 sections;
- (b) every field is ``str | None`` and defaults to ``None``;
- (c) construction with all eight populated round-trips losslessly;
- (d) any single section may be ``None``;
- (e) ``extra='forbid'`` rejects unknown fields on construction AND deserialization;
- (f) re-export smoke: importable from ``sfp_contracts.agents.readiness``.
"""

import typing
from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.agents.readiness import ParsedTicket

#: The eight ID-070 section names — encoded independently here so a field-name
#: drift in the model surfaces as a test failure (the names double as the
#: rubric's fixed ``rubric_results`` keys, so a silent rename is the key risk).
EXPECTED_FIELDS: set[str] = {
    "context",
    "requirements",
    "files_to_create_modify",
    "implementation_notes",
    "references",
    "context_outputs_required_inputs",
    "acceptance_criteria",
    "dependencies",
}

#: A non-empty value for every section, used as the all-populated baseline.
ALL_POPULATED: dict[str, str] = {name: f"<text for {name}>" for name in EXPECTED_FIELDS}

SORTED_FIELDS: list[str] = sorted(EXPECTED_FIELDS)


def test_field_set_is_exactly_the_eight_id070_sections() -> None:
    """(a) The model exposes exactly the eight ID-070 section names."""
    assert set(ParsedTicket.model_fields) == EXPECTED_FIELDS
    assert len(ParsedTicket.model_fields) == 8


@pytest.mark.parametrize("name", SORTED_FIELDS)
def test_each_field_annotation_is_str_or_none(name: str) -> None:
    """(b) Every field's annotation is exactly ``str | None``."""
    annotation = ParsedTicket.model_fields[name].annotation
    assert set(typing.get_args(annotation)) == {str, type(None)}


@pytest.mark.parametrize("name", SORTED_FIELDS)
def test_each_field_defaults_to_none(name: str) -> None:
    """(b) Every field defaults to ``None`` (sections are optional at the schema)."""
    assert getattr(ParsedTicket(), name) is None


def test_construction_all_populated_round_trips() -> None:
    """(c) A fully-populated ticket round-trips through JSON losslessly."""
    original = ParsedTicket(**ALL_POPULATED)
    for name, value in ALL_POPULATED.items():
        assert getattr(original, name) == value
    restored = ParsedTicket.model_validate_json(original.model_dump_json())
    assert restored == original


@pytest.mark.parametrize("name", SORTED_FIELDS)
def test_none_section_accepted(name: str) -> None:
    """(d) Any single section may be ``None`` while the rest are populated."""
    kwargs: dict[str, Any] = dict(ALL_POPULATED)
    kwargs[name] = None
    ticket = ParsedTicket(**kwargs)
    assert getattr(ticket, name) is None


@pytest.mark.parametrize("name", SORTED_FIELDS)
def test_non_string_non_none_rejected(name: str) -> None:
    """(b) A value that is neither ``str`` nor ``None`` is rejected.

    Pydantic v2 lax mode coerces some scalars, so a list — unambiguously not a
    string — is used to confirm the field enforces ``str | None``.
    """
    kwargs: dict[str, Any] = dict(ALL_POPULATED)
    kwargs[name] = ["not", "a", "string"]
    with pytest.raises(ValidationError):
        ParsedTicket(**kwargs)


def test_extra_field_rejected_on_construction() -> None:
    """(e) An unknown field is rejected at construction (extra='forbid')."""
    kwargs: dict[str, Any] = dict(ALL_POPULATED)
    kwargs["surprise"] = "x"
    with pytest.raises(ValidationError):
        ParsedTicket(**kwargs)


def test_extra_field_rejected_on_from_json() -> None:
    """(e) An unknown field is rejected when deserializing (extra='forbid')."""
    import json

    payload = json.loads(ParsedTicket(**ALL_POPULATED).model_dump_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        ParsedTicket.model_validate_json(json.dumps(payload))


def test_empty_construction_yields_all_none() -> None:
    """A bare ``ParsedTicket()`` has every section as ``None``."""
    ticket = ParsedTicket()
    assert ticket.model_dump() == {name: None for name in EXPECTED_FIELDS}


def test_importable_from_readiness_module() -> None:
    """(f) Smoke: ParsedTicket is importable from the readiness module path."""
    import importlib

    module = importlib.import_module("sfp_contracts.agents.readiness")
    assert module.ParsedTicket is ParsedTicket
