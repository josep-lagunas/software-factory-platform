"""Tests for the orchestrator-side Readiness Gate host (SFP-149; ID-064/065).

Covers, per the PRSpec acceptance criteria:

- all three verdict routes with a fake ``AgentRuntime``: ``READY`` passes the
  validated :class:`ReadinessOutput` through; ``NEEDS_CLARIFICATION`` returns
  ``blocking_ambiguities`` / ``missing_inputs`` verbatim (asserted
  field-by-field and element-wise, order preserved); ``MANUAL_REQUIRED``
  returns flagged by its verdict and never auto-proceeds (the host calls the
  runtime exactly once and returns exactly one output object);
- failure semantics: a fake runtime that raises propagates out of
  ``run_for_ticket`` (no default verdict invented, nothing swallowed), and the
  in-band failure modes (``success=False`` without an error string, a ``None``
  output, an unvalidatable payload) each raise
  :class:`ReadinessGateRuntimeError`;
- the request-construction seams: the default builder mirrors the vendored
  workspace-worker shape (agent role, ticket id verbatim, resolved prompt,
  opaque ticket context) and a caller-injected ``request_builder`` replaces it
  entirely (single path, no mixing);
- misconfiguration (neither ``request_builder`` nor ``prompt_resolver``)
  raises rather than inventing a prompt;
- the identity-source seam (default echoes the ticket id; an injected source
  is used verbatim in failure correlation);
- no-silent-retry: the runtime is called exactly once per
  ``run_for_ticket`` on **every** route, success and failure alike (counted by
  the fake), and there is no retry loop anywhere in the module (asserted
  structurally via AST);
- purity/determinism guards: no forbidden runtime deps (``time``,
  ``datetime``, ``random``, ``asyncio``, networking/httpx, vendor SDKs) and no
  direct ``httpx``/endpoint or cross-service ``workspace_worker`` import;
- contract-drift guard: unknown verdict values cannot pass validation (the
  ``ReadinessVerdict`` enum is the only routing surface).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

import pytest
from orchestrator.application import ReadinessGateHost
from orchestrator.application import readiness_host as module
from orchestrator.application.readiness_host import ReadinessGateRuntimeError
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_contracts.agents.readiness import ParsedTicket, ReadinessVerdict

MODULE_PATH = (
    module.__file__ if module.__file__ is not None else "orchestrator/application/readiness_host.py"
)

_TICKET_ID = "SFP-999"

_PARSED = ParsedTicket(
    context="Some context",
    requirements="Some requirements",
    files_to_create_modify="src/a.py",
    implementation_notes="notes",
    references="ID-064",
    context_outputs_required_inputs="inputs",
    acceptance_criteria="criteria",
    dependencies="none",
)


def _payload(verdict: ReadinessVerdict) -> dict[str, Any]:
    """A minimal valid readiness payload for ``verdict``."""
    return {
        "ticket_id": _TICKET_ID,
        "verdict": verdict.value,
        "blocking_ambiguities": [],
        "missing_inputs": [],
        "rubric_results": {"context": True, "requirements": True},
    }


class FakeRuntime:
    """A fake AgentRuntime recording every call (no silent retry escapes it).

    ``behaviour`` maps nothing — it IS the run: a callable receiving the
    request and returning an :class:`AgentRunResult` or raising. The call
    count is the retry oracle: any second call means a retry happened.
    """

    def __init__(self, behaviour: Callable[[AgentRunRequest], AgentRunResult]) -> None:
        self._behaviour = behaviour
        self.calls: list[AgentRunRequest] = []

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.calls.append(request)
        return self._behaviour(request)


def _ok(payload: dict[str, Any]) -> Callable[[AgentRunRequest], AgentRunResult]:
    def behaviour(_request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(agent="readiness", ticket_id=_TICKET_ID, success=True, output=payload)

    return behaviour


@pytest.fixture
def default_builder_host() -> Callable[[FakeRuntime], ReadinessGateHost]:
    """Host factory using the default builder + a fixed prompt resolver."""

    def make(runtime: FakeRuntime) -> ReadinessGateHost:
        return ReadinessGateHost(
            runtime, prompt_resolver=lambda agent, task: f"prompt:{agent}:{task}"
        )

    return make


# ---------------------------------------------------------------------------
# The three verdict routes (acceptance criterion 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verdict",
    [
        ReadinessVerdict.READY,
        ReadinessVerdict.NEEDS_CLARIFICATION,
        ReadinessVerdict.MANUAL_REQUIRED,
    ],
)
async def test_each_verdict_route_returns_exactly_one_validated_output(
    verdict: ReadinessVerdict, default_builder_host: Callable[[FakeRuntime], ReadinessGateHost]
) -> None:
    payload = _payload(verdict)
    runtime = FakeRuntime(_ok(payload))
    host = default_builder_host(runtime)

    output = await host.run_for_ticket(_TICKET_ID, _PARSED)

    # Exactly one output object; validated into the shared contract; the
    # verdict routes through unchanged.
    assert output.verdict is verdict
    assert output.ticket_id == _TICKET_ID
    assert output.rubric_results == {"context": True, "requirements": True}
    # Exactly one runtime call — one route, one run, no retry.
    assert len(runtime.calls) == 1


async def test_ready_route_passes_output_through_unchanged(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    payload = _payload(ReadinessVerdict.READY)
    runtime = FakeRuntime(_ok(payload))
    host = default_builder_host(runtime)

    output = await host.run_for_ticket(_TICKET_ID, _PARSED)

    assert output.verdict is ReadinessVerdict.READY
    assert output.blocking_ambiguities == []
    assert output.missing_inputs == []


async def test_needs_clarification_returns_ambiguities_and_missing_inputs_verbatim(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    """SFP-236: field-by-field verbatim passthrough, order preserved."""
    ambiguities = ["Ambiguity one", "Ambiguity two", "  spaced & ünïcode  ", "dup", "dup"]
    missing = ["Missing input A", "Missing input B", "", "Missing input A"]
    payload = _payload(ReadinessVerdict.NEEDS_CLARIFICATION)
    payload["blocking_ambiguities"] = ambiguities
    payload["missing_inputs"] = missing
    runtime = FakeRuntime(_ok(payload))
    host = default_builder_host(runtime)

    output = await host.run_for_ticket(_TICKET_ID, _PARSED)

    assert output.verdict is ReadinessVerdict.NEEDS_CLARIFICATION
    # Field-by-field, byte-identical, order preserved — no transformation,
    # dedup, trimming, or reordering anywhere.
    assert output.blocking_ambiguities == ambiguities
    assert output.missing_inputs == missing
    assert len(output.blocking_ambiguities) == len(ambiguities)
    assert len(output.missing_inputs) == len(missing)


async def test_manual_required_returns_flagged_and_never_auto_proceeds(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    payload = _payload(ReadinessVerdict.MANUAL_REQUIRED)
    payload["blocking_ambiguities"] = ["Needs a human"]
    runtime = FakeRuntime(_ok(payload))
    host = default_builder_host(runtime)

    output = await host.run_for_ticket(_TICKET_ID, _PARSED)

    # Flagged by its own verdict; nothing downstream is invoked by the host
    # (the only callable it holds is the runtime — called exactly once, and
    # the run is the *gate run*, not a proceed-run).
    assert output.verdict is ReadinessVerdict.MANUAL_REQUIRED
    assert output.blocking_ambiguities == ["Needs a human"]
    assert len(runtime.calls) == 1


# ---------------------------------------------------------------------------
# Failure semantics (acceptance criterion 2)
# ---------------------------------------------------------------------------


async def test_runtime_raise_propagates_no_default_invented(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    class Boom(RuntimeError):
        pass

    def behaviour(_request: AgentRunRequest) -> AgentRunResult:
        raise Boom("provider exploded")

    runtime = FakeRuntime(behaviour)
    host = default_builder_host(runtime)

    with pytest.raises(Boom, match="provider exploded"):
        await host.run_for_ticket(_TICKET_ID, _PARSED)
    # One call, then the failure crossed the frame — no retry happened.
    assert len(runtime.calls) == 1


async def test_runtime_failure_flag_raises(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    def behaviour(_request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(
            agent="readiness", ticket_id=_TICKET_ID, success=False, error="quota exhausted"
        )

    runtime = FakeRuntime(behaviour)
    host = default_builder_host(runtime)

    with pytest.raises(ReadinessGateRuntimeError, match="quota exhausted"):
        await host.run_for_ticket(_TICKET_ID, _PARSED)
    assert len(runtime.calls) == 1


async def test_runtime_failure_without_error_string_raises_with_placeholder(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    def behaviour(_request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(agent="readiness", ticket_id=_TICKET_ID, success=False)

    runtime = FakeRuntime(behaviour)
    host = default_builder_host(runtime)

    with pytest.raises(ReadinessGateRuntimeError, match="unknown error"):
        await host.run_for_ticket(_TICKET_ID, _PARSED)


async def test_runtime_none_output_raises(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    def behaviour(_request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(agent="readiness", ticket_id=_TICKET_ID, success=True)

    runtime = FakeRuntime(behaviour)
    host = default_builder_host(runtime)

    with pytest.raises(ReadinessGateRuntimeError, match="no output"):
        await host.run_for_ticket(_TICKET_ID, _PARSED)


async def test_runtime_invalid_payload_raises(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    """Contract drift surfaces immediately — never coerced or dropped."""
    # A payload a *previous* gate version might have emitted: an unknown
    # verdict value must not validate, and the host must not proceed.
    payload = _payload(ReadinessVerdict.READY)
    payload["verdict"] = "SUPER_READY"

    runtime = FakeRuntime(_ok(payload))
    host = default_builder_host(runtime)

    with pytest.raises(ReadinessGateRuntimeError, match="invalid"):
        await host.run_for_ticket(_TICKET_ID, _PARSED)


async def test_runtime_non_mapping_output_raises(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    def behaviour(_request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(
            agent="readiness",
            ticket_id=_TICKET_ID,
            success=True,
            output=42,  # type: ignore[arg-type]
        )

    runtime = FakeRuntime(behaviour)
    host = default_builder_host(runtime)

    with pytest.raises(ReadinessGateRuntimeError, match="mapping"):
        await host.run_for_ticket(_TICKET_ID, _PARSED)


async def test_failure_message_carries_ticket_and_identity(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    def behaviour(_request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(agent="readiness", ticket_id=_TICKET_ID, success=False, error="boom")

    runtime = FakeRuntime(behaviour)
    host = default_builder_host(runtime)

    with pytest.raises(ReadinessGateRuntimeError, match=_TICKET_ID):
        await host.run_for_ticket(_TICKET_ID, _PARSED)


# ---------------------------------------------------------------------------
# No silent retry (acceptance criterion 3) — structural
# ---------------------------------------------------------------------------


def test_no_retry_loop_exists_in_module() -> None:
    """No ``while`` loop and no retry-named construct anywhere in the module."""
    with open(MODULE_PATH, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.While):
            offenders.append(f"while loop at line {node.lineno}")
        if isinstance(node, ast.Name) and "retry" in node.id.lower():
            offenders.append(f"retry-named identifier {node.id!r} at line {node.lineno}")
        if isinstance(node, ast.Attribute) and "retry" in node.attr.lower():
            offenders.append(f"retry-named attribute {node.attr!r} at line {node.lineno}")
        # A bare except-and-continue pattern: except handler that swallows.
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            offenders.append(f"bare except at line {node.lineno}")

    assert not offenders, f"retry/swallow constructs found: {offenders}"


def test_run_for_ticket_routes_through_single_runtime_call_site() -> None:
    """``self._runtime.run`` appears exactly once in the module source.

    Any second call site would be a second (silent) run path.
    """
    with open(MODULE_PATH, encoding="utf-8") as handle:
        source = handle.read()

    occurrences = source.count("self._runtime.run(")
    assert occurrences == 1, f"expected exactly one runtime.run call site, found {occurrences}"


# ---------------------------------------------------------------------------
# Request construction and seams
# ---------------------------------------------------------------------------


async def test_default_builder_mirrors_vendored_request_shape(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    runtime = FakeRuntime(_ok(_payload(ReadinessVerdict.READY)))
    host = default_builder_host(runtime)

    await host.run_for_ticket(_TICKET_ID, _PARSED)

    request = runtime.calls[0]
    assert request.agent == "readiness"
    assert request.ticket_id == _TICKET_ID
    assert request.prompt == "prompt:readiness:evaluate"
    assert request.context["ticket_id"] == _TICKET_ID
    assert request.context["ticket"] == _PARSED.model_dump()


async def test_injected_request_builder_replaces_default_entirely() -> None:
    built: list[AgentRunRequest] = []
    custom = AgentRunRequest(
        agent="readiness",
        ticket_id=_TICKET_ID,
        prompt="CUSTOM PROMPT",
        context={"routing": "custom"},
    )

    def builder(ticket_id: str, parsed: ParsedTicket) -> AgentRunRequest:
        assert parsed is _PARSED
        built.append(custom)
        return custom

    runtime = FakeRuntime(_ok(_payload(ReadinessVerdict.READY)))
    host = ReadinessGateHost(runtime, request_builder=builder)

    output = await host.run_for_ticket(_TICKET_ID, _PARSED)

    assert output.verdict is ReadinessVerdict.READY
    assert runtime.calls[0] is custom
    assert built == [custom]


async def test_missing_both_prompt_seams_raises_no_invented_prompt() -> None:
    runtime = FakeRuntime(_ok(_payload(ReadinessVerdict.READY)))
    host = ReadinessGateHost(runtime)  # neither builder nor resolver injected

    with pytest.raises(ReadinessGateRuntimeError, match="prompt_resolver"):
        await host.run_for_ticket(_TICKET_ID, _PARSED)
    # Nothing ran — no invented prompt, no fabricated request.
    assert runtime.calls == []


def test_identity_source_defaults_to_ticket_id(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    host = default_builder_host(FakeRuntime(_ok(_payload(ReadinessVerdict.READY))))
    assert host._identity_source(_TICKET_ID) == _TICKET_ID


async def test_injected_identity_source_used_in_failure_correlation() -> None:
    def behaviour(_request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(agent="readiness", ticket_id=_TICKET_ID, success=False, error="boom")

    runtime = FakeRuntime(behaviour)
    host = ReadinessGateHost(
        runtime,
        prompt_resolver=lambda agent, task: "p",
        identity_source=lambda ticket_id: f"checkpoint:{ticket_id}:v3",
    )

    with pytest.raises(ReadinessGateRuntimeError, match=r"checkpoint:SFP-999:v3"):
        await host.run_for_ticket(_TICKET_ID, _PARSED)


async def test_ticket_id_is_always_the_argument_never_model_output(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    """The model may echo a different ticket id; the gate argument wins."""
    payload = _payload(ReadinessVerdict.READY)
    payload["ticket_id"] = "SOME-OTHER-TICKET"
    runtime = FakeRuntime(_ok(payload))
    host = default_builder_host(runtime)

    output = await host.run_for_ticket(_TICKET_ID, _PARSED)

    assert output.ticket_id == _TICKET_ID
    assert runtime.calls[0].ticket_id == _TICKET_ID


# ---------------------------------------------------------------------------
# Determinism / purity guards
# ---------------------------------------------------------------------------


def test_module_has_no_forbidden_runtime_dependencies() -> None:
    """No clock, randomness, async-runtime, networking, or vendor imports."""
    with open(MODULE_PATH, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    forbidden_roots = {
        "time",
        "random",
        "datetime",
        "asyncio",
        "httpx",
        "requests",
        "urllib",
        "socket",
        "anthropic",
        "openai",
        "workspace_worker",
        "os",
    }
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in forbidden_roots:
                    offenders.append(f"import {alias.name} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in forbidden_roots:
                offenders.append(f"from {node.module} import (line {node.lineno})")

    assert not offenders, f"forbidden imports found: {offenders}"


async def test_same_inputs_yield_same_output(
    default_builder_host: Callable[[FakeRuntime], ReadinessGateHost],
) -> None:
    """MAS §12.7: identical inputs → identical outputs across host instances."""
    payload = _payload(ReadinessVerdict.NEEDS_CLARIFICATION)
    payload["blocking_ambiguities"] = ["same", "each", "time"]
    outputs = []
    for _ in range(3):
        runtime = FakeRuntime(_ok(payload))
        host = default_builder_host(runtime)
        outputs.append((await host.run_for_ticket(_TICKET_ID, _PARSED)).model_dump_json())

    assert outputs[0] == outputs[1] == outputs[2]
