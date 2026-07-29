"""Tests for :func:`code` (SFP-72; DOC SFP-55; ID-022 / ID-066 / ID-067).

Mirrors ``test_test_designer_agent.py`` (SFP-71) — the Coder is the sibling
evaluator over the same runtime seam, operating on a ``PrSpec`` and returning a
``CoderOutput``. The runtime/prompt Protocols are stubbed; tests are pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import workspace_worker.agents.coder as coder_mod
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.agents.coder import CoderOutput
from sfp_contracts.agents.planner import PrSpec
from sfp_contracts.context.bindings import ResolvedContext
from sfp_contracts.validation.profiles import ValidationProfile
from workspace_worker.agents.coder import CoderError, code

_TICKET_ID = "SFP-72"
_AGENT = "coder"
_TASK = "implement"


def _resolved(missing: list[str] | None = None) -> ResolvedContext:
    return ResolvedContext(ticket_id=_TICKET_ID, resolved=[], missing=missing or [])


#: A fully-populated PrSpec (opaque input). Field set follows ID-021 / SFP-14.
_VALID_PRSPEC: dict[str, Any] = {
    "id": "PR-1",
    "title": "Add coder agent",
    "goal": "Implement one PR-spec and return CoderOutput.",
    "scope": ["Create agents/coder.py"],
    "out_of_scope": ["Reviewer agent"],
    "acceptance_criteria": ["code() returns a CoderOutput"],
    "dependencies": [],
    "validation_profile": ValidationProfile.LEVEL_1_INTERNAL.value,
    "validation_profile_reason": "Pure workflow code.",
    "required_gates": ["ci", "unit"],
    "likely_files_or_modules": ["services/workspace-worker/src/workspace_worker/agents/coder.py"],
    "risks": ["Prompt may drift from the ID-066 field list."],
    "implementation_notes": "Mirror test_designer.py.",
}


def _pr_spec() -> PrSpec:
    return PrSpec(**deepcopy(_VALID_PRSPEC))


def _coder_output() -> dict[str, Any]:
    return {
        "pr_spec_id": "PR-1",
        "branch_name": "sfp-72-coder-agent",
        "pull_request_url": "https://github.com/example/repo/pull/1",
        "files_changed": ["services/workspace-worker/src/workspace_worker/agents/coder.py"],
        "tests_added_or_updated": ["services/workspace-worker/tests/test_coder_agent.py"],
        "validation_status": "PASSED",
        "validation_evidence": ["uv run pytest -q -> 9 passed, 100% coverage"],
        "known_limitations": [],
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
    text: str = "<injected coder prompt>"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get_prompt(self, agent: str, task: str) -> str:
        self.calls.append((agent, task))
        return self.text


# --- happy path + request capture -----------------------------------------


def test_happy_path_returns_coder_output_and_captures_request() -> None:
    runtime = _FakeRuntime(result=_success(_coder_output()))
    provider = _FakePromptProvider()

    result = code(
        _pr_spec(), _resolved(), runtime=runtime, prompt_provider=provider, ticket_id=_TICKET_ID
    )

    assert isinstance(result, CoderOutput)
    assert result.pr_spec_id == "PR-1"
    assert result.branch_name == "sfp-72-coder-agent"
    assert result.validation_status.value == "PASSED"
    assert result.files_changed == [
        "services/workspace-worker/src/workspace_worker/agents/coder.py"
    ]

    assert len(runtime.captured) == 1
    request = runtime.captured[0]
    assert request.agent == _AGENT
    assert request.ticket_id == _TICKET_ID
    assert request.prompt == "<injected coder prompt>"
    assert request.context["ticket_id"] == _TICKET_ID
    assert request.context["pr_spec"] == _pr_spec().model_dump()
    assert provider.calls == [(_AGENT, _TASK)]


# --- fail-closed (ID-067) --------------------------------------------------


def test_non_conformant_output_raises() -> None:
    runtime = _FakeRuntime(result=_success({"pr_spec_id": "PR-1"}))  # missing required fields
    with pytest.raises(CoderError, match="output invalid"):
        code(_pr_spec(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)
    assert len(runtime.captured) == 1


def test_extra_field_output_raises() -> None:
    out = _coder_output()
    out["unknown"] = "x"
    runtime = _FakeRuntime(result=_success(out))
    with pytest.raises(CoderError, match="output invalid"):
        code(_pr_spec(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_success_false_raises() -> None:
    runtime = _FakeRuntime(result=_failure("build failed"))
    with pytest.raises(CoderError, match="run failed: build failed"):
        code(_pr_spec(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_none_output_raises() -> None:
    runtime = _FakeRuntime(result=_success(None))
    with pytest.raises(CoderError, match="no output"):
        code(_pr_spec(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


def test_runtime_raises_is_wrapped() -> None:
    runtime = _FakeRuntime(exc=RuntimeError("transport down"))
    with pytest.raises(CoderError, match="run raised"):
        code(_pr_spec(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)


# --- prompt resolution + anti-spoof ---------------------------------------


def test_default_prompt_branch_uses_prompt_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "shared.md").write_text("# Shared\n")
    (prompts / "coder.md").write_text("# Coder\n")
    (prompts / "coder").mkdir()
    (prompts / "coder" / "implement.md").write_text("# Implement one PR-spec\n")
    monkeypatch.setattr(coder_mod, "_DEFAULT_PROMPT_DIR", prompts)

    runtime = _FakeRuntime(result=_success(_coder_output()))
    result = code(_pr_spec(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert isinstance(result, CoderOutput)
    expected_prompt = PromptBuilder(prompts).get_prompt(_AGENT, _TASK)
    assert runtime.captured[0].prompt == expected_prompt


def test_ticket_id_echoed_not_spoofed() -> None:
    runtime = _FakeRuntime(result=_success(_coder_output()))
    code(_pr_spec(), _resolved(), runtime=runtime, ticket_id="gate-arg-9")
    assert runtime.captured[0].ticket_id == "gate-arg-9"
