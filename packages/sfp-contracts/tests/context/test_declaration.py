"""Tests for the :class:`TicketContextDeclaration` schema (SFP-37).

Covers the acceptance criteria:
- (a) a conformant declaration round-trips through ``to_json``/``from_json``;
- (b) all 5 catalogue names are accepted as ``ContextOutput.type`` (parametrized,
  derived from :data:`DEFAULT_CATALOGUE` AND a literal set);
- (c) an unknown output ``type`` is rejected on construction AND ``from_json``
  (TC-04 — unknown type injected straight into the JSON);
- (d) extra fields are rejected on all three models on construction AND on
  deserialization (``extra='forbid'``);
- (e) dropping any required field on each submodel raises ``ValidationError``;
- (f) empty ``outputs`` / ``required_inputs`` lists are accepted;
- (g) ``required_inputs`` ``name`` / ``source_ticket`` are free-form (no catalogue
  check);
- (h) ``model_fields`` sets are exact on all three models;
- (i) malformed JSON is rejected.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sfp_contracts.context.declaration import (
    ContextInput,
    ContextOutput,
    TicketContextDeclaration,
)
from sfp_contracts.context.types import DEFAULT_CATALOGUE

#: The five catalogue names as a literal — used to parametrize the accepted-type
#: tests and to guard against the derived set drifting away from the spec.
CATALOGUE_NAMES_LITERAL: tuple[str, ...] = (
    "repo_url",
    "db_endpoint",
    "db_secret_arn",
    "aws_account_id",
    "llm_provider_secret_ref",
)

VALID_KWARGS: dict[str, Any] = {
    "outputs": [ContextOutput(name="service_repo", type="repo_url")],
    "required_inputs": [ContextInput(name="db_dsn", source_ticket="SFP-99")],
}


def make_declaration(**overrides: Any) -> TicketContextDeclaration:
    kwargs = dict(VALID_KWARGS)
    kwargs.update(overrides)
    return TicketContextDeclaration(**kwargs)


def test_round_trip_preserves_every_field() -> None:
    """(a) A conformant declaration round-trips through JSON losslessly."""
    original = make_declaration()
    restored = TicketContextDeclaration.from_json(original.to_json())

    assert restored == original
    assert restored.outputs[0].name == "service_repo"
    assert restored.outputs[0].type == "repo_url"
    assert restored.required_inputs[0].name == "db_dsn"
    assert restored.required_inputs[0].source_ticket == "SFP-99"


def test_round_trip_bytes() -> None:
    """(a) ``from_json`` accepts bytes as well as str (str | bytes contract)."""
    original = make_declaration()
    restored = TicketContextDeclaration.from_json(original.to_json().encode())

    assert restored == original


def test_catalogue_name_set_matches_literal() -> None:
    """(b) The DEFAULT_CATALOGUE entry names equal the literal set from the spec."""
    derived = {entry.name for entry in DEFAULT_CATALOGUE.entries}
    assert derived == set(CATALOGUE_NAMES_LITERAL)


@pytest.mark.parametrize("ctx_type", CATALOGUE_NAMES_LITERAL)
def test_every_catalogue_type_accepted_on_construction(ctx_type: str) -> None:
    """(b) Each catalogue name is accepted as a ContextOutput.type."""
    declaration = TicketContextDeclaration(
        outputs=[ContextOutput(name="binding", type=ctx_type)],
    )
    assert declaration.outputs[0].type == ctx_type


@pytest.mark.parametrize("ctx_type", CATALOGUE_NAMES_LITERAL)
def test_every_catalogue_type_accepted_on_from_json(ctx_type: str) -> None:
    """(b) Each catalogue name survives a JSON round-trip as the same value."""
    declaration = TicketContextDeclaration.from_json(
        json.dumps({"outputs": [{"name": "binding", "type": ctx_type}], "required_inputs": []})
    )
    assert declaration.outputs[0].type == ctx_type


def test_unknown_type_rejected_on_construction() -> None:
    """(c) An output type not in the catalogue is rejected at construction."""
    with pytest.raises(ValidationError):
        TicketContextDeclaration(
            outputs=[ContextOutput(name="binding", type="not_a_real_type")],
        )


def test_unknown_type_rejected_on_from_json() -> None:
    """(c) TC-04 — an unknown type injected into the JSON is rejected by from_json."""
    payload = json.loads(make_declaration().to_json())
    payload["outputs"][0]["type"] = "not_a_real_type"
    with pytest.raises(ValidationError):
        TicketContextDeclaration.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("model_cls", "valid_kwargs", "extra"),
    [
        (ContextOutput, {"name": "x", "type": "repo_url"}, {"unexpected": "y"}),
        (ContextInput, {"name": "x", "source_ticket": "SFP-1"}, {"unexpected": "y"}),
        (TicketContextDeclaration, VALID_KWARGS, {"unexpected": "y"}),
    ],
)
def test_extra_fields_rejected_on_construction(
    model_cls: type,
    valid_kwargs: dict[str, Any],
    extra: dict[str, Any],
) -> None:
    """(d) Unknown fields are rejected at construction on all three models."""
    kwargs: dict[str, Any] = dict(valid_kwargs)
    kwargs.update(extra)
    with pytest.raises(ValidationError):
        model_cls(**kwargs)  # type: ignore[arg-type]


def test_declaration_extra_fields_rejected_on_from_json() -> None:
    """(d) Extra fields are rejected when deserializing a declaration."""
    payload = json.loads(make_declaration().to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        TicketContextDeclaration.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("model_cls", "json_str"),
    [
        (ContextOutput, json.dumps({"name": "x", "type": "repo_url", "unexpected": "y"})),
        (ContextInput, json.dumps({"name": "x", "source_ticket": "SFP-1", "unexpected": "y"})),
    ],
)
def test_submodel_extra_fields_rejected_on_validate_json(
    model_cls: type,
    json_str: str,
) -> None:
    """(d) Extra fields are rejected when validating submodels from JSON."""
    with pytest.raises(ValidationError):
        model_cls.model_validate_json(json_str)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (ContextOutput, {"type": "repo_url"}),
        (ContextOutput, {"name": "x"}),
        (ContextInput, {"source_ticket": "SFP-1"}),
        (ContextInput, {"name": "x"}),
    ],
)
def test_missing_required_field_on_submodel_raises(
    model_cls: type,
    kwargs: dict[str, Any],
) -> None:
    """(e) Dropping any required field on a submodel raises ValidationError."""
    with pytest.raises(ValidationError):
        model_cls(**kwargs)  # type: ignore[call-arg]


def test_empty_lists_accepted() -> None:
    """(f) A declaration with empty (default) lists is valid."""
    declaration = TicketContextDeclaration()
    assert declaration.outputs == []
    assert declaration.required_inputs == []

    restored = TicketContextDeclaration.from_json(declaration.to_json())
    assert restored == declaration


def test_required_inputs_are_free_form() -> None:
    """(g) required_inputs name / source_ticket carry no catalogue check."""
    declaration = TicketContextDeclaration(
        required_inputs=[
            ContextInput(name="some_arbitrary_name", source_ticket="WHATEVER-42"),
        ],
    )
    assert declaration.required_inputs[0].name == "some_arbitrary_name"
    assert declaration.required_inputs[0].source_ticket == "WHATEVER-42"

    restored = TicketContextDeclaration.from_json(declaration.to_json())
    assert restored.required_inputs[0].source_ticket == "WHATEVER-42"


def test_output_name_is_free_form() -> None:
    """(g) ContextOutput.name is a free-form binding name (not catalogue-checked)."""
    declaration = TicketContextDeclaration(
        outputs=[ContextOutput(name="any_binding_name", type="repo_url")],
    )
    assert declaration.outputs[0].name == "any_binding_name"


def test_model_field_sets_exact() -> None:
    """(h) Each model exposes exactly its specified field set."""
    assert set(ContextOutput.model_fields.keys()) == {"name", "type"}
    assert set(ContextInput.model_fields.keys()) == {"name", "source_ticket"}
    assert set(TicketContextDeclaration.model_fields.keys()) == {
        "outputs",
        "required_inputs",
    }


def test_malformed_json_rejected() -> None:
    """(i) Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        TicketContextDeclaration.from_json("{not valid json")


def test_full_declaration_with_all_types_round_trips() -> None:
    """A declaration using every catalogue type as outputs round-trips intact."""
    declaration = TicketContextDeclaration(
        outputs=[
            ContextOutput(name=f"out_{i}", type=t) for i, t in enumerate(CATALOGUE_NAMES_LITERAL)
        ],
    )
    restored = TicketContextDeclaration.from_json(declaration.to_json())
    assert restored == declaration
    assert [o.type for o in restored.outputs] == list(CATALOGUE_NAMES_LITERAL)
