"""Tests for :func:`evaluate_readiness` (SFP-68; ID-064 layer 2).

These tests pin the binding combination rule, the fail-closed policy (ID-067),
and the prompt-resolution contract. The deterministic layer-1 rubric
(:func:`evaluate_readiness_rubric`, SFP-67) is exercised *through* the gate; its
own behaviour is pinned in ``test_readiness_rubric.py`` and is treated as a
trusted dependency here.

The ``AgentRuntime`` and ``PromptProvider`` Protocols (SFP-51) are stubbed with
in-file fakes so the tests are pure and deterministic. ``ParsedTicket`` /
``ResolvedContext`` fixtures are built locally (sfp-testing ships no helpers for
these — only FakeBus / fake_context).

Test-case IDs (TC##) map to the ratified test plan.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
import workspace_worker.workflow.readiness_gate as readiness_gate
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.agents.readiness import (
    ParsedTicket,
    ReadinessOutput,
    ReadinessVerdict,
)
from sfp_contracts.context.bindings import ResolvedContext
from workspace_worker.workflow.readiness_gate import evaluate_readiness

#: Independent oracle: the eight mandatory ID-070 section names. Encoded here
#: WITHOUT consulting the implementation, mirroring ``test_readiness_rubric.py``.
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

_TICKET_ID = "SFP-68"
_AGENT = "readiness"
_TASK = "evaluate"


# --- fixtures --------------------------------------------------------------


def _full_ticket() -> ParsedTicket:
    """A ticket with every ID-070 section non-empty (the READY baseline)."""
    return ParsedTicket(**{name: f"<{name} content>" for name in SECTIONS})


def _ticket_missing(section: str, value: str | None = None) -> ParsedTicket:
    """A ticket with one section overridden; all others non-empty."""
    kwargs: dict[str, str | None] = {name: f"<{name} content>" for name in SECTIONS}
    kwargs[section] = value
    return ParsedTicket(**kwargs)


def _resolved(missing: list[str] | None = None) -> ResolvedContext:
    """A minimal ResolvedContext (no bindings) with the given ``missing`` names."""
    return ResolvedContext(ticket_id=_TICKET_ID, resolved=[], missing=missing or [])


def _model_dict(
    *,
    verdict: ReadinessVerdict = ReadinessVerdict.READY,
    blocking: list[str] | None = None,
    missing: list[str] | None = None,
    ticket_id: str = _TICKET_ID,
    rubric_results: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build an opaque model-output dict shaped as a ReadinessOutput JSON."""
    return {
        "ticket_id": ticket_id,
        "verdict": verdict.value,
        "blocking_ambiguities": blocking if blocking is not None else [],
        "missing_inputs": missing if missing is not None else [],
        "rubric_results": rubric_results if rubric_results is not None else {},
    }


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

    text: str = "<injected readiness prompt>"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get_prompt(self, agent: str, task: str) -> str:
        self.calls.append((agent, task))
        return self.text


# --- READY happy path (TC01) + request capture -----------------------------


def test_ready_happy_path_and_request_capture() -> None:
    """(TC01) Full ticket, model READY, no gaps -> READY; capture AgentRunRequest."""
    runtime = _FakeRuntime(result=_success(_model_dict(verdict=ReadinessVerdict.READY)))

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.READY
    assert result.blocking_ambiguities == []
    assert result.missing_inputs == []
    assert result.ticket_id == _TICKET_ID
    assert set(result.rubric_results) == set(SECTIONS)
    assert all(result.rubric_results.values())

    # The request captured by the runtime is well-formed.
    assert len(runtime.captured) == 1
    req = runtime.captured[0]
    assert req.agent == _AGENT
    assert req.ticket_id == _TICKET_ID
    assert isinstance(req.context, Mapping)
    assert len(req.context) > 0  # opaque, non-empty
    # Prompt provenance: the default-shipped readiness prompt flowed through.
    expected_prompt = PromptBuilder(readiness_gate._DEFAULT_PROMPT_DIR).get_prompt(_AGENT, _TASK)
    assert req.prompt == expected_prompt
    assert req.prompt  # non-empty


# --- NEEDS_CLARIFICATION driven by a model gap (TC02) ---------------------


def test_model_gap_flows_to_blocking_and_missing() -> None:
    """(TC02) A model gap reaches BOTH blocking_ambiguities and missing_inputs."""
    runtime = _FakeRuntime(
        result=_success(
            _model_dict(
                verdict=ReadinessVerdict.NEEDS_CLARIFICATION,
                blocking=["requirement X is ambiguous"],
            )
        )
    )

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert "requirement X is ambiguous" in result.blocking_ambiguities
    assert "requirement X is ambiguous" in result.missing_inputs


# --- MANUAL_REQUIRED (TC03 / TC05 / TC15) ----------------------------------


def test_manual_required_from_model() -> None:
    """(TC03) The model's MANUAL_REQUIRED verdict is honored."""
    runtime = _FakeRuntime(
        result=_success(
            _model_dict(
                verdict=ReadinessVerdict.MANUAL_REQUIRED,
                blocking=["contradictory requirements"],
            )
        )
    )

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.MANUAL_REQUIRED
    assert "contradictory requirements" in result.blocking_ambiguities


def test_manual_required_precedence_over_failed_rubric() -> None:
    """(TC05) MANUAL wins even when the rubric failed a section."""
    runtime = _FakeRuntime(result=_success(_model_dict(verdict=ReadinessVerdict.MANUAL_REQUIRED)))

    result = evaluate_readiness(
        _ticket_missing("requirements"),  # rubric fails this section
        _resolved(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
    )

    assert result.verdict is ReadinessVerdict.MANUAL_REQUIRED
    # The layer-1 rubric still ran and its failure message is present.
    assert "Missing required section: requirements" in result.blocking_ambiguities


def test_manual_required_with_empty_gaps_still_manual() -> None:
    """(TC15) MANUAL_REQUIRED is honored even when the model lists no gaps."""
    runtime = _FakeRuntime(result=_success(_model_dict(verdict=ReadinessVerdict.MANUAL_REQUIRED)))

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.MANUAL_REQUIRED
    assert result.blocking_ambiguities == []
    assert result.missing_inputs == []


# --- Layer-1 authoritative (TC04) ------------------------------------------


@pytest.mark.parametrize("value", [None, "   \n\t  "], ids=["none", "whitespace"])
def test_failed_rubric_forces_needs_clarification_even_when_model_ready(
    value: str | None,
) -> None:
    """(TC04) A failed rubric section forces NC even when the model says READY."""
    runtime = _FakeRuntime(result=_success(_model_dict(verdict=ReadinessVerdict.READY)))

    result = evaluate_readiness(
        _ticket_missing("acceptance_criteria", value),
        _resolved(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
    )

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert result.blocking_ambiguities == ["Missing required section: acceptance_criteria"]
    assert result.rubric_results["acceptance_criteria"] is False


# --- Model's NEEDS_CLARIFICATION without evidence -> READY (TC08) -----------


def test_model_needs_clarification_without_evidence_is_ready() -> None:
    """(TC08) The model's non-MANUAL verdict is not trusted without evidence."""
    runtime = _FakeRuntime(
        result=_success(
            _model_dict(
                verdict=ReadinessVerdict.NEEDS_CLARIFICATION,
                blocking=[],  # no evidence
                missing=[],
            )
        )
    )

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.READY
    assert result.blocking_ambiguities == []
    assert result.missing_inputs == []


# --- Fail-closed (TC06 / output-None / TC13 / E7) --------------------------


def test_fail_closed_on_run_success_false() -> None:
    """(TC06) success=False -> NEEDS_CLARIFICATION with descriptive message."""
    runtime = _FakeRuntime(result=_failure("provider timeout"))

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert any(
        m.startswith("Readiness model run failed:") and "provider timeout" in m
        for m in result.blocking_ambiguities
    )
    # Layer-1 rubric still ran.
    assert set(result.rubric_results) == set(SECTIONS)


def test_fail_closed_on_output_none() -> None:
    """success=True but output=None -> NC with 'returned no output' message."""
    runtime = _FakeRuntime(result=_success(None))

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert any(m == "Readiness model returned no output" for m in result.blocking_ambiguities)


@pytest.mark.parametrize(
    "bad_output",
    [
        pytest.param(
            {**_model_dict(), "unexpected_field": "boom"},
            id="extra-field",
        ),
        pytest.param(
            _model_dict(verdict=ReadinessVerdict.READY) | {"verdict": "BOGUS"},
            id="out-of-enum-verdict",
        ),
    ],
)
def test_fail_closed_on_invalid_output(bad_output: Mapping[str, Any]) -> None:
    """(TC13 opt A) An invalid model output -> NC with 'output invalid' message."""
    runtime = _FakeRuntime(result=_success(bad_output))

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert any(m.startswith("Readiness model output invalid:") for m in result.blocking_ambiguities)


def test_fail_closed_on_runtime_raising() -> None:
    """(E7) runtime.run raising -> NC with 'run raised' message, never READY."""
    runtime = _FakeRuntime(exc=RuntimeError("kaboom"))

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    assert any(
        m.startswith("Readiness model run raised:") and "RuntimeError" in m and "kaboom" in m
        for m in result.blocking_ambiguities
    )


# --- Prompt-provider override (TC07) ---------------------------------------


def test_prompt_provider_override_flows_into_request() -> None:
    """(TC07) An injected provider's text flows into the captured request.prompt."""
    provider = _FakePromptProvider(text="MY INJECTED PROMPT")
    runtime = _FakeRuntime(result=_success(_model_dict()))

    evaluate_readiness(
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

    monkeypatch.setattr(readiness_gate, "_DEFAULT_PROMPT_DIR", tmp_path)
    runtime = _FakeRuntime(result=_success(_model_dict()))

    evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    expected = PromptBuilder(tmp_path).get_prompt(_AGENT, _TASK)
    assert runtime.captured[0].prompt == expected


# --- Union semantics (TC09 / TC10) -----------------------------------------


def test_union_semantics_missing_inputs_and_blocking() -> None:
    """(TC09/TC10) missing_inputs = resolved.missing ∪ gaps; blocking = msgs ∪ gaps."""
    runtime = _FakeRuntime(
        result=_success(
            _model_dict(
                verdict=ReadinessVerdict.NEEDS_CLARIFICATION,
                blocking=["gap-1"],  # model gap
                missing=["SHOULD_BE_IGNORED"],  # model's own missing_inputs NOT unioned
            )
        )
    )

    result = evaluate_readiness(
        _ticket_missing("references"),  # layer-1 fails -> one layer-1 message
        _resolved(missing=["alpha-input"]),  # resolver missing
        runtime=runtime,
        ticket_id=_TICKET_ID,
    )

    # missing_inputs == resolved.missing ∪ model gaps (model's own missing ignored).
    assert set(result.missing_inputs) == {"alpha-input", "gap-1"}
    assert "SHOULD_BE_IGNORED" not in result.missing_inputs
    # blocking_ambiguities == layer-1 msgs ∪ model gaps.
    assert set(result.blocking_ambiguities) == {
        "Missing required section: references",
        "gap-1",
    }
    assert result.verdict is ReadinessVerdict.NEEDS_CLARIFICATION


def test_union_dedup_when_model_gap_duplicates_deterministic_source() -> None:
    """Dedup: a model gap equal to a resolver missing name appears only once."""
    runtime = _FakeRuntime(
        result=_success(
            _model_dict(
                verdict=ReadinessVerdict.NEEDS_CLARIFICATION,
                blocking=["alpha"],  # duplicates resolved.missing entry
            )
        )
    )

    result = evaluate_readiness(
        _full_ticket(),
        _resolved(missing=["alpha"]),
        runtime=runtime,
        ticket_id=_TICKET_ID,
    )

    assert result.missing_inputs == ["alpha"]  # deduped, order-stable
    assert result.blocking_ambiguities == ["alpha"]


# --- rubric_results pass-through (TC11) + ticket_id not spoofable (TC12) ----


def test_rubric_results_pass_through_ignores_hostile_model_dict() -> None:
    """(TC11) rubric_results is pure layer-1 even when the model supplies a foreign dict."""
    runtime = _FakeRuntime(
        result=_success(
            _model_dict(
                verdict=ReadinessVerdict.READY,
                rubric_results={"hostile_section": True},  # valid type, foreign keys
            )
        )
    )

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert set(result.rubric_results) == set(SECTIONS)
    assert "hostile_section" not in result.rubric_results
    assert all(result.rubric_results.values())


def test_ticket_id_not_spoofable_by_model() -> None:
    """(TC12) The model's ticket_id is ignored; the gate arg always wins."""
    runtime = _FakeRuntime(
        result=_success(_model_dict(verdict=ReadinessVerdict.READY, ticket_id="EVIL"))
    )

    result = evaluate_readiness(_full_ticket(), _resolved(), runtime=runtime, ticket_id=_TICKET_ID)

    assert result.ticket_id == _TICKET_ID
    assert result.ticket_id != "EVIL"


# --- Determinism (TC16) ----------------------------------------------------


def test_determinism_equal_inputs_equal_outputs() -> None:
    """(TC16) Equal inputs -> equal outputs incl. order-stable lists + to_json()."""
    runtime_a = _FakeRuntime(
        result=_success(
            _model_dict(
                verdict=ReadinessVerdict.NEEDS_CLARIFICATION,
                blocking=["gap-b", "gap-a"],
            )
        )
    )
    runtime_b = _FakeRuntime(
        result=_success(
            _model_dict(
                verdict=ReadinessVerdict.NEEDS_CLARIFICATION,
                blocking=["gap-b", "gap-a"],
            )
        )
    )

    a = evaluate_readiness(
        _ticket_missing("context"),
        _resolved(missing=["m1"]),
        runtime=runtime_a,
        ticket_id=_TICKET_ID,
    )
    b = evaluate_readiness(
        _ticket_missing("context"),
        _resolved(missing=["m1"]),
        runtime=runtime_b,
        ticket_id=_TICKET_ID,
    )

    assert a == b
    assert a.blocking_ambiguities == b.blocking_ambiguities
    assert a.missing_inputs == b.missing_inputs
    assert a.to_json() == b.to_json()
    assert isinstance(a, ReadinessOutput)
