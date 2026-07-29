"""Tests for :func:`design_tests` (SFP-71; DOC SFP-54; ID-066 / ID-022 / ID-067).

Mirrors ``test_planner_agent.py`` (SFP-70) — the Test Designer is the Planner's
sibling evaluator over the same runtime seam, differing only in its output
contract (a :class:`TestDesignerOutput` test plan). The ``AgentRuntime`` and
``PromptProvider`` Protocols are stubbed with in-file fakes; the tests are pure
and deterministic.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import workspace_worker.agents.test_designer as td_mod
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.agents.readiness import ParsedTicket
from sfp_contracts.agents.test_designer import TestDesignerOutput
from sfp_contracts.context.bindings import ResolvedContext
from workspace_worker.agents.test_designer import TestDesignerError, design_tests

_TICKET_ID = "SFP-71"
_AGENT = "test_designer"
_TASK = "design"

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
    return ParsedTicket(**{name: f"<{name} content>" for name in SECTIONS})


def _resolved(missing: list[str] | None = None) -> ResolvedContext:
    return ResolvedContext(ticket_id=_TICKET_ID, resolved=[], missing=missing or [])


#: A fully-populated TestPlan (seven list[str] buckets) — opaque model output,
#: encoded here as an independent oracle (ID-066).
_VALID_TEST_PLAN: dict[str, Any] = {
    "unit_tests": ["plan() returns a TestDesignerOutput"],
    "integration_tests": ["runtime seam forwards the prompt"],
    "e2e_or_smoke_tests": [],
    "negative_tests": ["non-conformant output raises TestDesignerError"],
    "edge_cases": ["empty test_plan buckets are valid"],
    "regression_risks": ["prompt may drift from the ID-066 buckets"],
    "required_validation_commands": ["uv run pytest -q --cov-fail-under=90"],
}


def _test_designer_output(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "pr_spec_id": "PR-1",
        "test_plan": plan if plan is not None else deepcopy(_VALID_TEST_PLAN),
    }


def _success(output: Mapping[str, Any] | None) -> AgentRunResult:
    return AgentRunResult(agent=_AGENT, ticket_id=_TICKET_ID, success=True, output=output)


def _failure(error: str = "model boom") -> AgentRunResult:
    return AgentRunResult(agent=_AGENT, ticket_id=_TICKET_ID, success=False, error=error)


@dataclass
class _FakeRuntime:
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
    text: str = "<injected test_designer prompt>"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get_prompt(self, agent: str, task: str) -> str:
        self.calls.append((agent, task))
        return self.text


# --- happy path + request capture -----------------------------------------


def test_happy_path_returns_test_designer_output_and_captures_request() -> None:
    runtime = _FakeRuntime(result=_success(_test_designer_output()))
    provider = _FakePromptProvider()

    result = design_tests(
        _full_ticket(), _resolved(), runtime=runtime, prompt_provider=provider, ticket_id=_TICKET_ID
    )

    assert isinstance(result, TestDesignerOutput)
    assert result.pr_spec_id == "PR-1"
    assert result.test_plan.unit_tests == ["plan() returns a TestDesignerOutput"]
    assert result.test_plan.required_validation_commands == ["uv run pytest -q --cov-fail-under=90"]

    assert len(runtime.captured) == 1
    request = runtime.captured[0]
    assert request.agent == _AGENT
    assert request.ticket_id == _TICKET_ID
    assert request.prompt == "<injected test_designer prompt>"
    assert request.context["ticket_id"] == _TICKET_ID
    assert request.context["ticket"] == _full_ticket().model_dump()
    assert provider.calls == [(_AGENT, _TASK)]


# --- fail-closed (ID-067) --------------------------------------------------


def test_non_conformant_output_raises() -> None:
    """Missing required field (test_plan) -> TestDesignerError; runtime called once."""
    runtime = _FakeRuntime(result=_success({"pr_spec_id": "PR-1"}))  # no test_plan
    with pytest.raises(TestDesignerError, match="output invalid"):
        design_tests(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)
    assert len(runtime.captured) == 1


def test_extra_field_output_raises() -> None:
    """extra='forbid' -> TestDesignerError."""
    out = _test_designer_output()
    out["unknown"] = "x"
    runtime = _FakeRuntime(result=_success(out))
    with pytest.raises(TestDesignerError, match="output invalid"):
        design_tests(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_success_false_raises() -> None:
    runtime = _FakeRuntime(result=_failure("rate limited"))
    with pytest.raises(TestDesignerError, match="run failed: rate limited"):
        design_tests(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_none_output_raises() -> None:
    runtime = _FakeRuntime(result=_success(None))
    with pytest.raises(TestDesignerError, match="no output"):
        design_tests(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_runtime_raises_is_wrapped() -> None:
    runtime = _FakeRuntime(exc=RuntimeError("transport down"))
    with pytest.raises(TestDesignerError, match="run raised"):
        design_tests(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


# --- prompt resolution -----------------------------------------------------


def test_default_prompt_branch_uses_prompt_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prompt_provider=None builds via PromptBuilder against _DEFAULT_PROMPT_DIR."""
    prompts = tmp_path / "prompts"
    (prompts).mkdir()
    (prompts / "shared.md").write_text("# Shared\n")
    (prompts / "test_designer.md").write_text("# Test Designer\n")
    (prompts / "test_designer").mkdir()
    (prompts / "test_designer" / "design.md").write_text("# Design the test plan\n")
    monkeypatch.setattr(td_mod, "_DEFAULT_PROMPT_DIR", prompts)

    runtime = _FakeRuntime(result=_success(_test_designer_output()))

    result = design_tests(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert isinstance(result, TestDesignerOutput)
    expected_prompt = PromptBuilder(prompts).get_prompt(_AGENT, _TASK)
    assert runtime.captured[0].prompt == expected_prompt


# --- determinism + anti-spoof ---------------------------------------------


def test_ticket_id_echoed_not_spoofed() -> None:
    """The gate arg wins; the model output carries no ticket_id to spoof."""
    out = _test_designer_output()
    runtime = _FakeRuntime(result=_success(out))
    design_tests(_full_ticket(), _resolved(), runtime=runtime, ticket_id="gate-arg-1")
    assert runtime.captured[0].ticket_id == "gate-arg-1"


def test_equal_inputs_yield_equal_outputs() -> None:
    out = _test_designer_output()
    r1 = design_tests(
        _full_ticket(),
        _resolved(),
        runtime=_FakeRuntime(result=_success(deepcopy(out))),
        ticket_id=_TICKET_ID,
    )
    r2 = design_tests(
        _full_ticket(),
        _resolved(),
        runtime=_FakeRuntime(result=_success(deepcopy(out))),
        ticket_id=_TICKET_ID,
    )
    assert r1 == r2
