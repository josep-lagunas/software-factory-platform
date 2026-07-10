"""Tests for the :class:`AgentOutput` envelope and :class:`AgentStatus` enum.

Covers the SFP-13 / SFP-30 acceptance criteria:
- (a) well-formed payload round-trips through ``to_json``/``from_json``;
- (b) extra/unknown fields rejected on construction AND ``from_json``;
- (c) every missing required field raises ``ValidationError`` (parametrized);
- (d) all 5 ``AgentStatus`` members are accepted (parametrized);
- (e) status string values equal the enum member names exactly;
- (f) ISO 8601 timestamp round-trips as ISO 8601;
- malformed JSON and an invalid status string are rejected.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sfp_contracts.agents.envelope import AgentOutput
from sfp_contracts.agents.status import AgentStatus

VALID_KWARGS: dict[str, object] = {
    "schema_version": "1.0",
    "agent": "planner",
    "ticket_id": "SFP-30",
    "timestamp": datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC),
    "status": AgentStatus.SUCCESS,
    "payload": {"k": "v"},
    "human_readable_summary": "planner produced a PRSpec",
}

REQUIRED_FIELDS = list(VALID_KWARGS.keys())


def make_output(**overrides: object) -> AgentOutput:
    kwargs = dict(VALID_KWARGS)
    kwargs.update(overrides)
    return AgentOutput(**kwargs)


def test_round_trip_preserves_every_field() -> None:
    """(a) A well-formed envelope round-trips through JSON losslessly."""
    original = make_output()
    restored = AgentOutput.from_json(original.to_json())

    assert restored == original
    assert restored.schema_version == "1.0"
    assert restored.agent == "planner"
    assert restored.ticket_id == "SFP-30"
    assert restored.timestamp == datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
    assert restored.status is AgentStatus.SUCCESS
    assert restored.payload == {"k": "v"}
    assert restored.human_readable_summary == "planner produced a PRSpec"


@pytest.mark.parametrize("extra", [{"unexpected": "x"}, {"schema_version_x": "2"}])
def test_extra_fields_rejected_on_construction(extra: dict[str, str]) -> None:
    """(b) Unknown fields are rejected at construction (extra='forbid')."""
    with pytest.raises(ValidationError):
        make_output(**extra)


def test_extra_fields_rejected_on_from_json() -> None:
    """(b) Unknown fields are rejected when deserializing (extra='forbid')."""
    import json

    payload = json.loads(make_output().to_json())
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        AgentOutput.from_json(json.dumps(payload))


@pytest.mark.parametrize("missing_field", REQUIRED_FIELDS)
def test_missing_required_field_raises(missing_field: str) -> None:
    """(c) Dropping any required field raises ValidationError."""
    kwargs = {k: v for k, v in VALID_KWARGS.items() if k != missing_field}
    with pytest.raises(ValidationError):
        AgentOutput(**kwargs)


@pytest.mark.parametrize("status", list(AgentStatus))
def test_all_status_values_accepted(status: AgentStatus) -> None:
    """(d) Every AgentStatus member is a valid envelope status."""
    output = make_output(status=status)
    assert output.status is status
    assert AgentOutput.from_json(output.to_json()).status is status


@pytest.mark.parametrize("status", list(AgentStatus))
def test_status_string_values_match_names(status: AgentStatus) -> None:
    """(e) The string value of each status equals its member name."""
    assert status.value == status.name


def test_timestamp_round_trips_as_iso_8601() -> None:
    """(f) An ISO 8601 timestamp deserializes and re-serializes as ISO 8601."""
    iso = "2026-07-10T12:00:00Z"
    output = make_output(timestamp=iso)  # type: ignore[arg-type]
    assert isinstance(output.timestamp, datetime)
    assert output.timestamp == datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
    # The JSON form must keep ISO 8601 shape.
    assert '"2026-07-10T12:00:00Z"' in output.to_json()


def test_malformed_json_rejected() -> None:
    """Malformed JSON raises ValidationError via model_validate_json."""
    with pytest.raises(ValidationError):
        AgentOutput.from_json("{not valid json")


def test_invalid_status_string_rejected() -> None:
    """A status string outside the enum is rejected."""
    import json

    payload = json.loads(make_output().to_json())
    payload["status"] = "COMPLETED"
    with pytest.raises(ValidationError):
        AgentOutput.from_json(json.dumps(payload))
