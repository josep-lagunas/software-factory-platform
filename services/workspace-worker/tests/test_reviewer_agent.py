"""Tests for :func:`review` (SFP-73; DOC SFP-56; ID-022 / ID-066 / ID-067).

Mirrors ``test_coder_agent.py`` (SFP-72) — the Reviewer is the judgment-only
sibling evaluator over the same runtime seam, taking a PrSpec + the Coder's
output and returning a ``ReviewerOutput``. Runtime/prompt stubbed; tests pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import workspace_worker.agents.reviewer as reviewer_mod
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.agents.coder import CoderOutput
from sfp_contracts.agents.planner import PrSpec
from sfp_contracts.agents.reviewer import ReviewerOutput
from sfp_contracts.context.bindings import ResolvedContext
from sfp_contracts.validation.profiles import ValidationProfile
from workspace_worker.agents.reviewer import ReviewerError, review

_TICKET_ID = "SFP-73"
_AGENT = "reviewer"
_TASK = "review"


def _resolved(missing: list[str] | None = None) -> ResolvedContext:
    return ResolvedContext(ticket_id=_TICKET_ID, resolved=[], missing=missing or [])


_VALID_PRSPEC: dict[str, Any] = {
    "id": "PR-1",
    "title": "Add reviewer agent",
    "goal": "Judge a PR-spec implementation.",
    "scope": ["Create agents/reviewer.py"],
    "out_of_scope": ["Other agents"],
    "acceptance_criteria": ["review() returns a ReviewerOutput"],
    "dependencies": [],
    "satisfies_tickets": ["SFP-73"],
    "validation_profile": ValidationProfile.LEVEL_1_INTERNAL.value,
    "validation_profile_reason": "Pure workflow code.",
    "required_gates": ["ci", "unit"],
    "likely_files_or_modules": [
        "services/workspace-worker/src/workspace_worker/agents/reviewer.py"
    ],
    "risks": ["Prompt may drift from the ID-066 field list."],
    "implementation_notes": "Mirror coder.py.",
}


def _pr_spec() -> PrSpec:
    return PrSpec(**deepcopy(_VALID_PRSPEC))


def _coder_output() -> CoderOutput:
    return CoderOutput(
        pr_spec_id="PR-1",
        branch_name="sfp-73-reviewer-agent",
        pull_request_url="https://github.com/example/repo/pull/2",
        files_changed=["services/workspace-worker/src/workspace_worker/agents/reviewer.py"],
        tests_added_or_updated=["services/workspace-worker/tests/test_reviewer_agent.py"],
        validation_status="PASSED",  # type: ignore[arg-type]
        validation_evidence=["uv run pytest -q -> 8 passed, 100% coverage"],
        known_limitations=[],
    )


def _reviewer_output(
    status: str = "APPROVED", rationale: str = "All gates pass."
) -> dict[str, Any]:
    return {
        "pr_spec_id": "PR-1",
        "review_status": status,
        "quality_gates": {
            "blueprint_compliance": True,
            "acceptance_criteria_satisfied": True,
            "test_plan_satisfied": True,
            "no_unrelated_changes": True,
            "maintainability_acceptable": True,
            "security_acceptable": True,
        },
        "rationale": rationale,
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
    text: str = "<injected reviewer prompt>"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get_prompt(self, agent: str, task: str) -> str:
        self.calls.append((agent, task))
        return self.text


# --- happy path + request capture -----------------------------------------


def test_happy_path_returns_reviewer_output_and_captures_request() -> None:
    runtime = _FakeRuntime(result=_success(_reviewer_output()))
    provider = _FakePromptProvider()

    result = review(
        _pr_spec(),
        _coder_output(),
        _resolved(),
        runtime=runtime,
        prompt_provider=provider,
        ticket_id=_TICKET_ID,
    )

    assert isinstance(result, ReviewerOutput)
    assert result.pr_spec_id == "PR-1"
    assert result.review_status.value == "APPROVED"
    assert result.quality_gates.security_acceptable is True

    assert len(runtime.captured) == 1
    request = runtime.captured[0]
    assert request.agent == _AGENT
    assert request.ticket_id == _TICKET_ID
    assert request.prompt == "<injected reviewer prompt>"
    assert request.context["ticket_id"] == _TICKET_ID
    assert request.context["pr_spec"] == _pr_spec().model_dump()
    assert request.context["coder_output"] == _coder_output().model_dump()
    assert provider.calls == [(_AGENT, _TASK)]


# --- fail-closed (ID-067) --------------------------------------------------


def test_non_conformant_output_raises() -> None:
    runtime = _FakeRuntime(result=_success({"pr_spec_id": "PR-1"}))  # missing fields
    with pytest.raises(ReviewerError, match="output invalid"):
        review(_pr_spec(), _coder_output(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)
    assert len(runtime.captured) == 1


def test_extra_field_output_raises() -> None:
    out = _reviewer_output()
    out["unknown"] = "x"
    runtime = _FakeRuntime(result=_success(out))
    with pytest.raises(ReviewerError, match="output invalid"):
        review(_pr_spec(), _coder_output(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_success_false_raises() -> None:
    runtime = _FakeRuntime(result=_failure("rate limited"))
    with pytest.raises(ReviewerError, match="run failed: rate limited"):
        review(_pr_spec(), _coder_output(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_none_output_raises() -> None:
    runtime = _FakeRuntime(result=_success(None))
    with pytest.raises(ReviewerError, match="no output"):
        review(_pr_spec(), _coder_output(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_runtime_raises_is_wrapped() -> None:
    runtime = _FakeRuntime(exc=RuntimeError("transport down"))
    with pytest.raises(ReviewerError, match="run raised"):
        review(_pr_spec(), _coder_output(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


# --- prompt resolution + anti-spoof ---------------------------------------


def test_default_prompt_branch_uses_prompt_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "shared.md").write_text("# Shared\n")
    (prompts / "reviewer.md").write_text("# Reviewer\n")
    (prompts / "reviewer").mkdir()
    (prompts / "reviewer" / "review.md").write_text("# Review a PR\n")
    monkeypatch.setattr(reviewer_mod, "_DEFAULT_PROMPT_DIR", prompts)

    runtime = _FakeRuntime(result=_success(_reviewer_output()))
    result = review(_pr_spec(), _coder_output(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert isinstance(result, ReviewerOutput)
    expected_prompt = PromptBuilder(prompts).get_prompt(_AGENT, _TASK)
    assert runtime.captured[0].prompt == expected_prompt


def test_ticket_id_echoed_not_spoofed() -> None:
    runtime = _FakeRuntime(result=_success(_reviewer_output()))
    review(_pr_spec(), _coder_output(), _resolved(), runtime=runtime, ticket_id="gate-arg-73")
    assert runtime.captured[0].ticket_id == "gate-arg-73"
