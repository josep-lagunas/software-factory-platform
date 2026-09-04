"""Tests for the :class:`PlannerHost` hosting glue (MAS §9.6, SFP-150).

Covers, per the PRSpec's scope and acceptance criteria: the valid-row happy
path (validated ``PlannerOutput`` returned AND ``persist_specs`` called exactly
once before the return, asserted through an ordered event log rather than a
bare call count); the invalid-pydantic row; the empty-``pr_specs`` row; the
missing-``validation_profile`` ID-067 row (profile and reason each); the
failed-run row; persist-not-called on every invalid path; persistence-failure
propagation (the raising callable's exception reaches the caller verbatim,
nothing is returned as persisted); and the export seam (package re-export is
the module class).

Fakes, not mocks: a ``_FakeRuntime`` returning canned
:class:`~sfp_agent_runtime.interfaces.AgentRunResult` rows and a spy
``persist_specs`` recording an ordered event sequence shared with the
return-path assertion.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from orchestrator.application import PlannerHost, PlannerOutputInvalid
from orchestrator.application.planner_host import PlannerHost as _Direct
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_contracts.agents.planner import PlannerOutput


# The package re-export and the module attribute are the same class: the
# PRSpec's export requirement is exercised, not assumed.
def test_package_export_is_the_module_class() -> None:
    assert PlannerHost is _Direct


_TICKET = "SFP-150"

# A structurally valid raw planner output (one PR-spec, ID-067 fields present).
_VALID_RAW: dict[str, Any] = {
    "pr_specs": [
        {
            "id": "SFP-150-1",
            "title": "SFP-150: PlannerHost",
            "goal": "Host the planner.",
            "scope": ["services/orchestrator/src/orchestrator/application/planner_host.py"],
            "out_of_scope": ["prompt design"],
            "acceptance_criteria": ["valid output returned"],
            "dependencies": ["sfp-contracts"],
            "satisfies_tickets": ["SFP-150"],
            "validation_profile": "LEVEL_1_INTERNAL",
            "validation_profile_reason": "Internal application module.",
            "required_gates": ["pytest"],
            "likely_files_or_modules": ["planner_host.py"],
            "risks": ["shape drift"],
            "implementation_notes": "Pure glue.",
        }
    ]
}


def _spec(**overrides: Any) -> dict[str, Any]:
    """A valid raw PR-spec dict with ``overrides`` applied on top."""
    base = dict(_VALID_RAW["pr_specs"][0])
    base.update(overrides)
    for key in [k for k, v in overrides.items() if v is _OMIT]:
        del base[key]
    return base


class _OMITSentinel:
    """Marker for "delete this key from the fixture"."""


_OMIT = _OMITSentinel()


class _FakeRuntime:
    """Minimal AgentRuntime double: returns one canned result per run."""

    def __init__(self, result: AgentRunResult) -> None:
        self.result = result
        self.requests: list[AgentRunRequest] = []

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.requests.append(request)
        return self.result


def _request_for(ticket_id: str) -> AgentRunRequest:
    """Request-builder seam: the resolved planner prompt for the ticket."""
    return AgentRunRequest(
        agent="planner",
        ticket_id=ticket_id,
        prompt=f"plan {ticket_id}",
        context={"ticket_id": ticket_id},
    )


class _SpyPersist:
    """persist_specs double recording an ordered event log it shares."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[str, PlannerOutput]] = []

    async def __call__(self, ticket_id: str, output: PlannerOutput) -> None:
        self.calls.append((ticket_id, output))
        self.events.append("persist")


class _RaisingPersist:
    """persist_specs double: always raises, recording nothing."""

    async def __call__(self, ticket_id: str, output: PlannerOutput) -> None:
        raise RuntimeError("storage unavailable")


def _make_host(
    raw: Mapping[str, Any] | None = None,
    *,
    success: bool = True,
    error: str | None = None,
    persist: Any = None,
    events: list[str] | None = None,
) -> tuple[PlannerHost, _FakeRuntime]:
    """Wire a host over canned seams; returns (host, fake runtime)."""
    result = AgentRunResult(
        agent="planner", ticket_id=_TICKET, success=success, output=raw, error=error
    )
    runtime = _FakeRuntime(result)
    if persist is None:
        persist = _SpyPersist(events if events is not None else [])
    host = PlannerHost(runtime=runtime, build_request=_request_for, persist_specs=persist)
    return host, runtime


# --- Valid row: returned AND persisted, persist before return -----------------


async def test_valid_row_returns_validated_output() -> None:
    host, _ = _make_host(_VALID_RAW)
    output = await host.run_for_ticket(_TICKET)

    assert isinstance(output, PlannerOutput)
    assert output.model_validate(_VALID_RAW) == output
    assert [s.id for s in output.pr_specs] == ["SFP-150-1"]


async def test_valid_row_persists_exactly_once_before_return() -> None:
    events: list[str] = []
    spy = _SpyPersist(events)
    host, _ = _make_host(_VALID_RAW, persist=spy, events=events)

    output = await host.run_for_ticket(_TICKET)
    events.append("return")

    # Order-asserted, not just call-counted: persist precedes the return.
    assert events == ["persist", "return"]
    assert len(spy.calls) == 1
    persisted_ticket, persisted_output = spy.calls[0]
    assert persisted_ticket == _TICKET
    assert persisted_output is output


async def test_valid_row_builds_request_and_runs_through_runtime() -> None:
    host, runtime = _make_host(_VALID_RAW)
    await host.run_for_ticket(_TICKET)

    assert len(runtime.requests) == 1
    assert runtime.requests[0].agent == "planner"
    assert runtime.requests[0].ticket_id == _TICKET


# --- Invalid rows: PlannerOutputInvalid, persist NOT called --------------------


async def test_invalid_pydantic_row_raises_and_never_persists() -> None:
    raw = {"pr_specs": [{"id": "SFP-150-1"}]}  # missing nearly every field
    spy = _SpyPersist([])
    host, _ = _make_host(raw, persist=spy)

    with pytest.raises(PlannerOutputInvalid) as excinfo:
        await host.run_for_ticket(_TICKET)

    assert spy.calls == []
    assert "failed validation" in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


async def test_unknown_field_row_raises() -> None:
    raw = {"pr_specs": [_spec(extra_field="nope")], "unexpected": True}
    spy = _SpyPersist([])
    host, _ = _make_host(raw, persist=spy)

    with pytest.raises(PlannerOutputInvalid):
        await host.run_for_ticket(_TICKET)

    assert spy.calls == []


async def test_empty_pr_specs_row_raises_and_never_persists() -> None:
    spy = _SpyPersist([])
    host, _ = _make_host({"pr_specs": []}, persist=spy)

    with pytest.raises(PlannerOutputInvalid) as excinfo:
        await host.run_for_ticket(_TICKET)

    assert spy.calls == []
    # The message names the empty-plan violation whichever layer caught it
    # (pydantic's min_length or the host's explicit post-condition).
    msg = str(excinfo.value)
    assert "failed validation" in msg or "zero pr_specs" in msg


async def test_none_output_row_raises_and_never_persists() -> None:
    # A "successful" run whose output is None is as invalid as a bad shape.
    spy = _SpyPersist([])
    host, _ = _make_host(None, persist=spy)

    with pytest.raises(PlannerOutputInvalid):
        await host.run_for_ticket(_TICKET)

    assert spy.calls == []


async def test_failed_run_row_raises_and_never_persists() -> None:
    spy = _SpyPersist([])
    host, _ = _make_host(_VALID_RAW, success=False, error="model unavailable", persist=spy)

    with pytest.raises(PlannerOutputInvalid) as excinfo:
        await host.run_for_ticket(_TICKET)

    assert spy.calls == []
    assert "model unavailable" in str(excinfo.value)


@pytest.mark.parametrize(
    ("field", "raw_spec"),
    [
        ("validation_profile", _spec(validation_profile=_OMIT)),
        ("validation_profile_reason", _spec(validation_profile_reason=_OMIT)),
        (
            "validation_profile",
            _spec(validation_profile="NOT_A_PROFILE"),
        ),
    ],
    ids=[
        "profile-missing",
        "reason-missing",
        "profile-not-a-member",
    ],
)
async def test_missing_id067_fields_row_raises_and_never_persists(
    field: str, raw_spec: dict[str, Any]
) -> None:
    spy = _SpyPersist([])
    host, _ = _make_host({"pr_specs": [raw_spec]}, persist=spy)

    with pytest.raises(PlannerOutputInvalid):
        await host.run_for_ticket(_TICKET)

    assert spy.calls == []


async def test_second_spec_missing_profile_raises_for_whole_output() -> None:
    # One valid spec + one spec missing its profile: the whole output is
    # invalid — the host never salvages the valid subset (no repair).
    raw = {"pr_specs": [_spec(), _spec(id="SFP-150-2", validation_profile=_OMIT)]}
    spy = _SpyPersist([])
    host, _ = _make_host(raw, persist=spy)

    with pytest.raises(PlannerOutputInvalid):
        await host.run_for_ticket(_TICKET)

    assert spy.calls == []


# --- Persistence-failure propagation ------------------------------------------


async def test_persistence_failure_propagates_uncaught() -> None:
    host, _ = _make_host(_VALID_RAW, persist=_RaisingPersist())

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await host.run_for_ticket(_TICKET)


# --- Determinism ---------------------------------------------------------------


async def test_same_seams_same_result() -> None:
    # MAS §12.7: identical inputs (same canned runtime row, same seams) yield
    # identical outputs across repeated runs.
    for _ in range(2):
        host, _ = _make_host(_VALID_RAW)
        output = await host.run_for_ticket(_TICKET)
        assert (
            output.model_dump_json() == PlannerOutput.model_validate(_VALID_RAW).model_dump_json()
        )
