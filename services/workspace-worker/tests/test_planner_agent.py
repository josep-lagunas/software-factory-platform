"""Tests for :func:`plan` (SFP-70; DOC SFP-53; ID-021 / ID-066 / ID-067).

These tests pin the prompt-resolution contract, the fail-closed policy (ID-067),
and the request-shape contract. The ``AgentRuntime`` and ``PromptProvider``
Protocols (SFP-51) are stubbed with in-file fakes so the tests are pure and
deterministic. ``ParsedTicket`` / ``ResolvedContext`` fixtures are built locally
(sfp-testing ships no helpers for these — only FakeBus / fake_context).

The structural shape mirrors ``test_readiness_gate.py`` (SFP-68) — the Planner is
the readiness gate's sibling evaluator over the same runtime seam, differing
only in its output contract and in raising rather than returning a degenerate
payload on failure.

Test-case IDs (TC##) map to the ratified test plan.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest
import workspace_worker.agents.planner as planner_mod
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.agents.planner import PlannerOutput
from sfp_contracts.agents.readiness import ParsedTicket
from sfp_contracts.context.bindings import ResolvedContext
from sfp_contracts.validation.profiles import ValidationProfile
from workspace_worker.agents.planner import PlannerError, plan

_TICKET_ID = "SFP-70"
_AGENT = "planner"
_TASK = "plan"

#: Independent oracle: the eight mandatory ID-070 section names (mirrors
#: test_readiness_gate.py). Encoded here WITHOUT consulting the implementation.
SECTIONS: tuple[str, ...] = (
    "context",
    "requirements",
    "files_to_create_modify",
    "implementation_notes",
    "references",
    "context_outputs_required_inputs",
    "acceptance_criteria",
    "dependencies",
)


# --- fixtures --------------------------------------------------------------


def _full_ticket() -> ParsedTicket:
    """A ticket with every ID-070 section non-empty (the READY baseline)."""
    return ParsedTicket(**{name: f"<{name} content>" for name in SECTIONS})


def _resolved(missing: list[str] | None = None) -> ResolvedContext:
    """A minimal ResolvedContext (no bindings) with the given ``missing`` names."""
    return ResolvedContext(ticket_id=_TICKET_ID, resolved=[], missing=missing or [])


#: A fully-populated PrSpec dict (opaque model output). Field set and semantics
#: follow ID-021 / ID-066 / SFP-14 — encoded here WITHOUT consulting the
#: implementation under test, as an independent oracle.
_VALID_PRSPEC: dict[str, Any] = {
    "id": "PR-1",
    "title": "Add planner agent",
    "goal": "Decompose a ready ticket into PR-specs.",
    "scope": ["Create agents/planner.py"],
    "out_of_scope": ["Coder/Reviewer agents"],
    "acceptance_criteria": ["plan() returns a PlannerOutput"],
    "dependencies": [],
    "satisfies_tickets": ["SFP-70"],
    "validation_profile": ValidationProfile.LEVEL_1_INTERNAL.value,
    "validation_profile_reason": "Pure workflow code, no runtime impact.",
    "required_gates": ["ci", "unit"],
    "likely_files_or_modules": ["services/workspace-worker/src/workspace_worker/agents/planner.py"],
    "risks": ["Prompt may drift from the ID-066 field list."],
    "implementation_notes": "Mirror readiness_gate.py.",
}


def _planner_output(pr_specs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build an opaque model-output dict shaped as a PlannerOutput JSON."""
    return {"pr_specs": pr_specs if pr_specs is not None else [deepcopy(_VALID_PRSPEC)]}


def _success(output: Mapping[str, Any] | None) -> AgentRunResult:
    return AgentRunResult(agent=_AGENT, ticket_id=_TICKET_ID, success=True, output=output)


def _failure(error: str = "model boom") -> AgentRunResult:
    return AgentRunResult(agent=_AGENT, ticket_id=_TICKET_ID, success=False, error=error)


@dataclass
class _FakeRuntime:
    """A stub :class:`AgentRuntime` that records each request and returns/raises."""

    result: AgentRunResult | None = None
    exc: BaseException | None = None
    captured: list[AgentRunRequest] = field(default_factory=list)

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.captured.append(request)
        if self.exc is not None:
            raise self.exc
        return self.result if self.result is not None else _success(None)


@dataclass
class _FakePromptProvider:
    """A stub :class:`PromptProvider` returning fixed text and recording calls."""

    text: str = "<injected planner prompt>"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get_prompt(self, agent: str, task: str) -> str:
        self.calls.append((agent, task))
        return self.text


# --- happy path (TC01) + request capture -----------------------------------


def test_happy_path_returns_planner_output_and_captures_request() -> None:
    """(TC01) Valid output -> PlannerOutput; capture a well-formed request."""
    runtime = _FakeRuntime(result=_success(_planner_output()))

    result = plan(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert isinstance(result, PlannerOutput)
    assert len(result.pr_specs) == 1
    spec = result.pr_specs[0]
    assert spec.id == "PR-1"
    assert spec.validation_profile is ValidationProfile.LEVEL_1_INTERNAL

    # The request captured by the runtime is well-formed.
    assert len(runtime.captured) == 1
    req = runtime.captured[0]
    assert req.agent == _AGENT
    assert req.ticket_id == _TICKET_ID
    assert isinstance(req.context, Mapping)
    assert req.context["ticket_id"] == _TICKET_ID
    assert "ticket" in req.context and "resolved" in req.context
    # Prompt provenance: the default-shipped planner prompt flowed through.
    expected_prompt = PromptBuilder(planner_mod._DEFAULT_PROMPT_DIR).get_prompt(_AGENT, _TASK)
    assert req.prompt == expected_prompt
    assert req.prompt  # non-empty


def test_multiple_pr_specs_accepted() -> None:
    """A multi-spec output is preserved as a multi-element pr_specs list."""
    specs = [
        deepcopy(_VALID_PRSPEC),
        {**deepcopy(_VALID_PRSPEC), "id": "PR-2", "title": "Second PR"},
    ]
    runtime = _FakeRuntime(result=_success(_planner_output(specs)))

    result = plan(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert [s.id for s in result.pr_specs] == ["PR-1", "PR-2"]


# --- Fail-closed (TC06 / output-None / TC13 / E7) --------------------------


def test_fail_closed_on_run_success_false() -> None:
    """(TC06) success=False -> PlannerError carrying the cause."""
    runtime = _FakeRuntime(result=_failure("provider timeout"))

    with pytest.raises(PlannerError, match="run failed") as ei:
        plan(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)
    assert "provider timeout" in str(ei.value)


def test_fail_closed_on_output_none() -> None:
    """success=True but output=None -> PlannerError."""
    runtime = _FakeRuntime(result=_success(None))

    with pytest.raises(PlannerError, match="no output"):
        plan(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


@pytest.mark.parametrize(
    "bad_output",
    [
        pytest.param(
            {"pr_specs": [{**deepcopy(_VALID_PRSPEC), "unexpected_field": "boom"}]},
            id="extra-field-on-prspec",
        ),
        pytest.param(
            {"pr_specs": [{k: v for k, v in _VALID_PRSPEC.items() if k != "goal"}]},
            id="missing-required-field",
        ),
        pytest.param(
            {"pr_specs": [{**deepcopy(_VALID_PRSPEC), "validation_profile": "BOGUS"}]},
            id="out-of-enum-validation-profile",
        ),
        pytest.param(
            {"pr_specs": [{**deepcopy(_VALID_PRSPEC), "scope": "not-a-list"}]},
            id="wrong-type-on-field",
        ),
        pytest.param({"pr_specs": []}, id="empty-pr-specs"),
        pytest.param(
            {**_planner_output(), "unexpected_top_level": "boom"},
            id="extra-top-level-field",
        ),
    ],
)
def test_fail_closed_on_invalid_output(bad_output: Mapping[str, Any]) -> None:
    """(TC13) A non-conformant model output -> PlannerError ('output invalid')."""
    runtime = _FakeRuntime(result=_success(bad_output))

    with pytest.raises(PlannerError, match="output invalid"):
        plan(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_fail_closed_on_runtime_raising() -> None:
    """(E7) runtime.run raising -> PlannerError carrying the cause, never a payload."""
    runtime = _FakeRuntime(exc=RuntimeError("kaboom"))

    with pytest.raises(PlannerError, match="run raised") as ei:
        plan(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)
    assert "RuntimeError" in str(ei.value) and "kaboom" in str(ei.value)


# --- Prompt-provider override (TC07) ---------------------------------------


def test_prompt_provider_override_flows_into_request() -> None:
    """(TC07) An injected provider's text flows into the captured request.prompt."""
    provider = _FakePromptProvider(text="MY INJECTED PROMPT")
    runtime = _FakeRuntime(result=_success(_planner_output()))

    plan(
        _full_ticket(),
        _resolved(),
        runtime=runtime,
        prompt_provider=provider,
        ticket_id=_TICKET_ID,
    )

    assert provider.calls == [(_AGENT, _TASK)]
    assert runtime.captured[0].prompt == "MY INJECTED PROMPT"


# --- Default-prompt branch via redirected _DEFAULT_PROMPT_DIR (TC14) -------


def test_default_prompt_branch_uses_redirected_dir(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(TC14) prompt_provider=None builds the prompt via PromptBuilder(dir)."""
    # Seed the three fragments the PromptBuilder layout composes.
    (tmp_path / "shared.md").write_text("SHARED", encoding="utf-8")
    (tmp_path / f"{_AGENT}.md").write_text("ROLE", encoding="utf-8")
    (tmp_path / _AGENT / f"{_TASK}.md").parent.mkdir(parents=True)
    (tmp_path / _AGENT / f"{_TASK}.md").write_text("TASK", encoding="utf-8")

    monkeypatch.setattr(planner_mod, "_DEFAULT_PROMPT_DIR", tmp_path)
    runtime = _FakeRuntime(result=_success(_planner_output()))

    plan(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    expected = PromptBuilder(tmp_path).get_prompt(_AGENT, _TASK)
    assert runtime.captured[0].prompt == expected


# --- ticket_id echoed + agent role (TC12) ----------------------------------


def test_ticket_id_and_agent_echoed_into_request() -> None:
    """The gate's ticket_id + 'planner' role are echoed into the request."""
    runtime = _FakeRuntime(result=_success(_planner_output()))

    plan(
        _full_ticket(),
        _resolved(),
        runtime=runtime,
        ticket_id="SFP-999-OTHER",
    )

    assert len(runtime.captured) == 1
    req = runtime.captured[0]
    assert req.agent == _AGENT
    assert req.ticket_id == "SFP-999-OTHER"
    assert req.context["ticket_id"] == "SFP-999-OTHER"


# --- Determinism (TC16) ----------------------------------------------------


def test_determinism_equal_inputs_equal_outputs() -> None:
    """(TC16) Equal inputs -> equal outputs incl. to_json()."""
    out = _planner_output()
    runtime_a = _FakeRuntime(result=_success(deepcopy(out)))
    runtime_b = _FakeRuntime(result=_success(deepcopy(out)))

    a = plan(_full_ticket(), _resolved(), runtime=runtime_a, ticket_id=_TICKET_ID)
    b = plan(_full_ticket(), _resolved(), runtime=runtime_b, ticket_id=_TICKET_ID)

    assert a == b
    assert a.to_json() == b.to_json()
    assert isinstance(a, PlannerOutput)
