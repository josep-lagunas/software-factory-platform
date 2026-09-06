"""Tests for the in-loop enrichment agent (SFP-243): ``workflow/enrichment``.

These tests pin the controller loop against the PRSpec's acceptance criteria,
with a fake :class:`~sfp_agent_runtime.interfaces.AgentRuntime` driven entirely
by fixtures (deterministic; no network — MAS §12.7).

Load-bearing points proven here:
- (AC1) stub ticket + REAL gate-ambiguity fixture -> all 8 sections non-empty
  (dry-run green) and every ambiguity addressed;
- (AC2) a description failing the dry-run is NEVER published — it is rejected
  and retried within the loop bound;
- (AC3) 2 failed enrichments -> MANUAL_REQUIRED; exactly 2 runtime runs (no
  third silent retry);
- (AC4) no invented grounding — a description citing a symbol absent from the
  grounded surface is rejected (spot-check contract);
- (AC5) decorated headings are rejected (via the dry-run — pinned separately
  in ``test_description_dry_run.py``, exercised here end-to-end);
- (AC6) secret material is rejected before any publish;
- the gate's structured output is rendered VERBATIM into the prompt context
  (SFP-236) and the ticket_id is never taken from the model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
import workspace_worker.workflow.enrichment as enrichment_mod
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.agents.readiness import ParsedTicket, ReadinessOutput, ReadinessVerdict
from workspace_worker.workflow.enrichment import (
    ENRICHMENT_DECISION,
    EnrichmentStatus,
    enrich_ticket,
)

# Independent oracle: the eight mandatory ID-070 headers <-> fields (mirrors
# test_jira_parser.py / test_description_dry_run.py — never imported from the
# implementation under test).
SECTION_HEADERS: tuple[tuple[str, str], ...] = (
    ("Context", "context"),
    ("Requirements", "requirements"),
    ("Files to create/modify", "files_to_create_modify"),
    ("Implementation notes", "implementation_notes"),
    ("References", "references"),
    ("Context outputs / required inputs", "context_outputs_required_inputs"),
    ("Acceptance criteria", "acceptance_criteria"),
    ("Dependencies", "dependencies"),
)

_TICKET_ID = "SFP-243"

#: The grounded repo surface (read-verified fixture): symbols that exist on
#: landed main. An enriched description may cite ONLY these.
GROUNDED_SYMBOLS: frozenset[str] = frozenset(
    {
        "workspace_worker.workflow.readiness_gate",
        "workspace_worker.repo.jira.parser.adf_to_parsed_ticket",
        "sfp_contracts.agents.readiness.ReadinessOutput",
    }
)


# --- fixtures ----------------------------------------------------------------


def _stub_ticket() -> ParsedTicket:
    """A stub ticket: thin sections, one missing — the enrichment input."""
    return ParsedTicket(
        context="Wire the enrichment loop into the pipeline.",
        requirements=None,  # absent — the gate flagged it
        files_to_create_modify="workspace_worker/workflow/enrichment.py",
        implementation_notes="Follow the readiness gate placement.",
        references="workspace_worker.workflow.readiness_gate",
        context_outputs_required_inputs=None,
        acceptance_criteria="Dry-run green on all fixtures.",
        dependencies=None,
    )


def _gate_fixture() -> ReadinessOutput:
    """A REAL gate structured-abort output (SFP-232/236 shape, real schema)."""
    return ReadinessOutput(
        ticket_id=_TICKET_ID,
        verdict=ReadinessVerdict.NEEDS_CLARIFICATION,
        blocking_ambiguities=[
            "Requirements section is absent — cannot plan against it",
            "unclear whether the enrichment output is written to Jira or only returned",
        ],
        missing_inputs=["per-ticket-type template inputs"],
        rubric_results={field: field not in ("requirements",) for _, field in SECTION_HEADERS},
    )


def _grounded_description() -> str:
    """A well-formed enriched description: 8 literal headers, bodies, citations."""
    bodies = {
        "context": (
            "The readiness gate blocks this ticket; the enrichment loop closes "
            "that gap in-pipeline, grounded on "
            "`workspace_worker.workflow.readiness_gate`."
        ),
        "requirements": (
            "Produce an enriched description that addresses every blocking "
            "ambiguity, then write it via the Jira seam. Decision table —\n"
            "- gate verdict READY: proceed to planning; reason: no blockers\n"
            "- gate verdict NEEDS_CLARIFICATION: enrich and re-gate; reason: "
            "structured blockers present\n"
            "- gate verdict MANUAL_REQUIRED: surface to a human; reason: "
            "irreconcilable contradiction"
        ),
        "files_to_create_modify": (
            "- workspace_worker/workflow/enrichment.py\n"
            "- workspace_worker/workflow/description_dry_run.py"
        ),
        "implementation_notes": (
            "Host the agent on the AgentRuntime seam; publish only after the "
            "dry-run over `workspace_worker.repo.jira.parser.adf_to_parsed_ticket` "
            "is green."
        ),
        "references": (
            "- `sfp_contracts.agents.readiness.ReadinessOutput`\n"
            "- `workspace_worker.workflow.readiness_gate`"
        ),
        "context_outputs_required_inputs": (
            "Outputs: enriched description, relaunch decision. Inputs: gate "
            "structured output, grounded repo surface."
        ),
        "acceptance_criteria": (
            "- all eight sections non-empty after dry-run\n- every blocking ambiguity addressed"
        ),
        "dependencies": ("- LANDED: readiness gate structured abort output\n- None otherwise."),
    }
    lines: list[str] = []
    for header, name in SECTION_HEADERS:
        lines.append(f"# {header}")
        lines.append(bodies[name])
        lines.append("")
    return "\n".join(lines)


@dataclass
class _FakeGrounding:
    """A fixture GroundingContext over a fixed landed-symbol set."""

    symbols: frozenset[str] = field(default_factory=frozenset)

    def symbol_exists(self, symbol: str) -> bool:
        return symbol in self.symbols


@dataclass
class _FakeRuntime:
    """A stub AgentRuntime returning scripted results per run (fixtures drive it).

    ``results`` is consumed one entry per ``run`` call; a raised entry (an
    exception instance) propagates from ``run``; ``None`` means "no scripted
    result — record and fail". Every request is captured for assertions.
    """

    results: list[AgentRunResult | BaseException | None] = field(default_factory=list)
    captured: list[AgentRunRequest] = field(default_factory=list)

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.captured.append(request)
        if not self.results:
            return AgentRunResult(
                agent=request.agent, ticket_id=request.ticket_id, success=False, error="no script"
            )
        nxt = self.results.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        if nxt is None:
            return AgentRunResult(
                agent=request.agent, ticket_id=request.ticket_id, success=False, error="unscripted"
            )
        return nxt


@dataclass
class _RecordingPublisher:
    """A recording DescriptionPublisher — proves what was (never) written."""

    calls: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, ticket_id: str, description: str) -> None:
        self.calls.append((ticket_id, description))


def _ok(description: str) -> AgentRunResult:
    """A successful run result carrying the enrichment output contract."""
    return AgentRunResult(
        agent="enrichment", ticket_id=_TICKET_ID, success=True, output={"description": description}
    )


def _run(**kwargs: Any) -> tuple[Any, _FakeRuntime, _RecordingPublisher]:
    """Invoke enrich_ticket with the standard fixture inputs + overrides."""
    runtime = kwargs.pop("runtime")
    publisher = kwargs.pop("publisher", _RecordingPublisher())
    outcome = enrich_ticket(
        _stub_ticket(),
        _gate_fixture(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
        grounding=_FakeGrounding(GROUNDED_SYMBOLS),
        publisher=publisher,
        **kwargs,
    )
    return outcome, runtime, publisher


# --- (AC1) happy path: green description published --------------------------


def test_green_description_published_with_all_sections() -> None:
    """(AC1) Stub ticket + real gate fixture -> PUBLISHED, 8 sections non-empty."""
    outcome, runtime, publisher = _run(runtime=_FakeRuntime(results=[_ok(_grounded_description())]))

    assert outcome.status == EnrichmentStatus.PUBLISHED
    assert outcome.decision == ENRICHMENT_DECISION.RELAUNCH
    assert outcome.iterations == 1
    assert outcome.enriched_description == _grounded_description()
    assert outcome.failures == ()
    # Published exactly once, with the ticket id and the green description.
    assert publisher.calls == [(_TICKET_ID, _grounded_description())]
    # Deterministic single run.
    assert len(runtime.captured) == 1


def test_every_gate_ambiguity_addressed_in_published_description() -> None:
    """(AC1) Every blocking ambiguity/missing input/rubric failure is addressed."""
    description = _grounded_description()
    # requirements was the failed rubric section -> its section is non-empty.
    assert "Requirements" in description
    outcome, _, _ = _run(runtime=_FakeRuntime(results=[_ok(description)]))
    assert outcome.status == EnrichmentStatus.PUBLISHED

    # Independent oracle: re-parse the published description and check the
    # gate's failed rubric section came back non-empty.
    from workspace_worker.workflow.description_dry_run import dry_run_markdown

    result = dry_run_markdown(description)
    assert result.ok
    gate = _gate_fixture()
    for section, passed in gate.rubric_results.items():
        if not passed:
            value = getattr(result.parsed, section)
            assert value and value.strip(), f"failed rubric section {section} still empty"


# --- prompt-context wiring (verbatim gate output; ticket_id authoritative) --


def test_gate_output_rendered_verbatim_into_context() -> None:
    """(SFP-236) The gate's structured output reaches the prompt context verbatim."""
    outcome_unused, runtime, _ = _run(runtime=_FakeRuntime(results=[_ok(_grounded_description())]))
    assert outcome_unused.status == EnrichmentStatus.PUBLISHED

    req = runtime.captured[0]
    assert req.agent == "enrichment"
    assert req.ticket_id == _TICKET_ID
    ctx: Mapping[str, Any] = req.context
    gate = _gate_fixture()
    assert ctx["gate"]["blocking_ambiguities"] == gate.blocking_ambiguities
    assert ctx["gate"]["missing_inputs"] == gate.missing_inputs
    assert ctx["gate"]["rubric_failed"] == ["requirements"]
    assert ctx["ticket"] == _stub_ticket().model_dump()
    assert ctx["grounding_symbols"] == sorted(GROUNDED_SYMBOLS)
    # No retry happened -> no prior-failure key.
    assert "prior_iteration_failures" not in ctx


def test_default_prompt_flows_from_shipped_fragments() -> None:
    """(ID-059) The default prompt is built from the shipped fragment files."""
    _, runtime, _ = _run(runtime=_FakeRuntime(results=[_ok(_grounded_description())]))
    expected = PromptBuilder(enrichment_mod._DEFAULT_PROMPT_DIR).get_prompt("enrichment", "enrich")
    assert runtime.captured[0].prompt == expected
    assert "eight literal" in expected  # fragments carry the hard rules


@dataclass
class _FakePromptProvider:
    """A stub PromptProvider returning a fixed prompt and capturing lookups."""

    prompt: str = "ENRICH-TEST-PROMPT"
    looked_up: list[tuple[str, str]] = field(default_factory=list)

    def get_prompt(self, agent: str, task: str) -> str:
        self.looked_up.append((agent, task))
        return self.prompt


def test_injected_prompt_provider_replaces_default() -> None:
    """An injected PromptProvider wins over the default fragment build."""
    provider = _FakePromptProvider()
    _, runtime, _ = _run(
        runtime=_FakeRuntime(results=[_ok(_grounded_description())]),
        prompt_provider=provider,
    )
    assert provider.looked_up == [("enrichment", "enrich")]
    assert runtime.captured[0].prompt == "ENRICH-TEST-PROMPT"


def test_template_inputs_forwarded() -> None:
    """Per-ticket-type template inputs reach the prompt context unchanged."""
    template: Mapping[str, Any] = {"ticket_type": "emitter", "clone_shape": "sqs-to-batch"}
    _, runtime, _ = _run(
        runtime=_FakeRuntime(results=[_ok(_grounded_description())]), template=template
    )
    assert runtime.captured[0].context["template"] == dict(template)


# --- (AC2) failing description never published; retried ---------------------


def _description_with_section_empty(section_header: str) -> str:
    """The grounded description with one section's body removed entirely.

    The target section keeps ONLY its header — the following body lines and
    blank separators are dropped until the next ``# `` header, so the section
    is present-but-empty (the dry-run's ``section empty`` case).
    """
    lines = _grounded_description().split("\n")
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("# "):
            skipping = line.strip() == f"# {section_header}"
            out.append(line)
            continue
        if skipping:
            continue  # drop this body line
        out.append(line)
    return "\n".join(out)


def test_failing_description_rejected_then_retried_then_published() -> None:
    """(AC2) Iteration 1 fails the dry-run -> NOT published; iteration 2 green."""
    bad = _description_with_section_empty("Dependencies")
    runtime = _FakeRuntime(results=[_ok(bad), _ok(_grounded_description())])
    publisher = _RecordingPublisher()
    outcome = enrich_ticket(
        _stub_ticket(),
        _gate_fixture(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
        grounding=_FakeGrounding(GROUNDED_SYMBOLS),
        publisher=publisher,
    )

    assert outcome.status == EnrichmentStatus.PUBLISHED
    assert outcome.iterations == 2
    # ONLY the green description was published — never the failing one.
    assert publisher.calls == [(_TICKET_ID, _grounded_description())]
    assert all(bad != call[1] for call in publisher.calls)
    # The iteration-1 failure is recorded, prefixed with its iteration.
    assert any("iteration 1" in line and "dependencies" in line for line in outcome.failures)


def test_retry_context_carries_prior_failures() -> None:
    """Iteration 2's context names iteration 1's exact failure lines."""
    bad = _description_with_section_empty("References")
    runtime = _FakeRuntime(results=[_ok(bad), _ok(_grounded_description())])
    enrich_ticket(
        _stub_ticket(),
        _gate_fixture(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
        grounding=_FakeGrounding(GROUNDED_SYMBOLS),
    )
    assert len(runtime.captured) == 2
    retry_ctx = runtime.captured[1].context
    assert "prior_iteration_failures" in retry_ctx
    assert any("references" in line for line in retry_ctx["prior_iteration_failures"])


def test_decorated_headers_rejected_end_to_end() -> None:
    """(AC5) A decorated-header description is rejected and retried."""
    decorated = _grounded_description().replace("# Context", "## 1. Context", 1)
    runtime = _FakeRuntime(results=[_ok(decorated), _ok(_grounded_description())])
    publisher = _RecordingPublisher()
    outcome = enrich_ticket(
        _stub_ticket(),
        _gate_fixture(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
        grounding=_FakeGrounding(GROUNDED_SYMBOLS),
        publisher=publisher,
    )
    assert outcome.status == EnrichmentStatus.PUBLISHED
    assert outcome.iterations == 2
    assert all(decorated != call[1] for call in publisher.calls)


# --- (AC3) loop bound: 2 failures -> MANUAL_REQUIRED ------------------------


def test_two_failed_iterations_surface_manual_required() -> None:
    """(AC3) 2 failed enrichments -> MANUAL_REQUIRED; exactly 2 runs, no third."""
    bad = _description_with_section_empty("Requirements")
    runtime = _FakeRuntime(results=[_ok(bad), _ok(bad)])
    publisher = _RecordingPublisher()
    outcome = enrich_ticket(
        _stub_ticket(),
        _gate_fixture(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
        grounding=_FakeGrounding(GROUNDED_SYMBOLS),
        publisher=publisher,
    )

    assert outcome.status == EnrichmentStatus.MANUAL_REQUIRED
    assert outcome.decision == ENRICHMENT_DECISION.ABORT
    assert outcome.iterations == 2
    assert len(runtime.captured) == 2  # the bound — no third silent retry
    assert publisher.calls == []  # nothing was ever written
    assert outcome.enriched_description is None  # failing text never surfaced
    assert len(outcome.failures) >= 2
    assert all(line.startswith("[iteration ") for line in outcome.failures)


@pytest.mark.parametrize(
    "script",
    [
        # run raised
        [RuntimeError("provider boom"), RuntimeError("provider boom")],
        # success=False
        [
            AgentRunResult(
                agent="enrichment", ticket_id=_TICKET_ID, success=False, error="timeout"
            ),
            AgentRunResult(
                agent="enrichment", ticket_id=_TICKET_ID, success=False, error="timeout"
            ),
        ],
        # output None
        [
            AgentRunResult(agent="enrichment", ticket_id=_TICKET_ID, success=True, output=None),
            AgentRunResult(agent="enrichment", ticket_id=_TICKET_ID, success=True, output=None),
        ],
        # invalid output shape
        [
            AgentRunResult(
                agent="enrichment", ticket_id=_TICKET_ID, success=True, output={"nope": 1}
            ),
            AgentRunResult(
                agent="enrichment", ticket_id=_TICKET_ID, success=True, output={"nope": 1}
            ),
        ],
        # output not a mapping at all
        [
            AgentRunResult(
                agent="enrichment", ticket_id=_TICKET_ID, success=True, output=["description"]
            ),
            AgentRunResult(
                agent="enrichment", ticket_id=_TICKET_ID, success=True, output=["description"]
            ),
        ],
        # description empty/whitespace
        [
            AgentRunResult(
                agent="enrichment", ticket_id=_TICKET_ID, success=True, output={"description": "  "}
            ),
            AgentRunResult(
                agent="enrichment", ticket_id=_TICKET_ID, success=True, output={"description": "  "}
            ),
        ],
        # description not a string
        [
            AgentRunResult(
                agent="enrichment",
                ticket_id=_TICKET_ID,
                success=True,
                output={"description": 42},
            ),
            AgentRunResult(
                agent="enrichment",
                ticket_id=_TICKET_ID,
                success=True,
                output={"description": 42},
            ),
        ],
    ],
    ids=[
        "raised",
        "success-false",
        "output-none",
        "invalid-shape",
        "non-mapping",
        "non-string",
        "empty-description",
    ],
)
def test_model_failure_modes_count_against_bound(script: list[Any]) -> None:
    """(ID-067) Every model failure mode consumes an iteration; 2 -> MANUAL."""
    runtime = _FakeRuntime(results=script)
    publisher = _RecordingPublisher()
    outcome = enrich_ticket(
        _stub_ticket(),
        _gate_fixture(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
        grounding=_FakeGrounding(GROUNDED_SYMBOLS),
        publisher=publisher,
    )
    assert outcome.status == EnrichmentStatus.MANUAL_REQUIRED
    assert len(runtime.captured) == 2
    assert publisher.calls == []
    assert len(outcome.failures) == 2


# --- (AC4) grounding spot-check ----------------------------------------------


def test_ungrounded_symbol_rejected() -> None:
    """(AC4) A cited symbol absent from the grounded surface -> rejected."""
    hallucinated = _grounded_description().replace(
        "`workspace_worker.workflow.readiness_gate`",
        "`workspace_worker.workflow.not_landed_yet`",
    )
    runtime = _FakeRuntime(results=[_ok(hallucinated), _ok(_grounded_description())])
    publisher = _RecordingPublisher()
    outcome = enrich_ticket(
        _stub_ticket(),
        _gate_fixture(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
        grounding=_FakeGrounding(GROUNDED_SYMBOLS),
        publisher=publisher,
    )
    assert any(
        "ungrounded symbol: workspace_worker.workflow.not_landed_yet" in line
        for line in outcome.failures
    )
    assert outcome.status == EnrichmentStatus.PUBLISHED  # iteration 2 recovered
    assert all(hallucinated != call[1] for call in publisher.calls)


def test_ungrounded_symbol_twice_surfaces_manual_required() -> None:
    """Grounding failures alone can exhaust the bound -> MANUAL_REQUIRED."""
    hallucinated = _grounded_description().replace("readiness_gate", "not_landed_yet")
    runtime = _FakeRuntime(results=[_ok(hallucinated), _ok(hallucinated)])
    outcome, _, publisher = _run(runtime=runtime)
    assert outcome.status == EnrichmentStatus.MANUAL_REQUIRED
    assert publisher.calls == []


def test_no_grounding_seam_fail_closed() -> None:
    """No injected grounding -> every citation ungrounded -> fail-closed.

    MAS §12.9: the controller never invents a landed surface. A symbol-citing
    description cannot publish without a grounding seam.
    """
    runtime = _FakeRuntime(results=[_ok(_grounded_description()), _ok(_grounded_description())])
    publisher = _RecordingPublisher()
    outcome = enrich_ticket(
        _stub_ticket(),
        _gate_fixture(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
        publisher=publisher,  # NO grounding injected
    )
    assert outcome.status == EnrichmentStatus.MANUAL_REQUIRED
    assert publisher.calls == []
    assert any("ungrounded symbol" in line for line in outcome.failures)


# --- (AC6) no secret material -------------------------------------------------


@pytest.mark.parametrize(
    "leak",
    [
        "token: ATATT3xFfPRM_super_secret_token_value_here",
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "api_key = sk-proj-abcdefghijklmnop1234567890",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
    ids=["atlassian", "github-pat", "sk-token", "private-key"],
)
def test_secret_material_rejected(leak: str) -> None:
    """(AC6) Secret-shaped material -> rejected before any publish."""
    leaked = _grounded_description() + f"\n# stray\n{leak}\n"
    runtime = _FakeRuntime(results=[_ok(leaked), _ok(_grounded_description())])
    publisher = _RecordingPublisher()
    outcome = enrich_ticket(
        _stub_ticket(),
        _gate_fixture(),
        runtime=runtime,
        ticket_id=_TICKET_ID,
        grounding=_FakeGrounding(GROUNDED_SYMBOLS),
        publisher=publisher,
    )
    assert any("secret material present" in line for line in outcome.failures)
    assert all(leaked != call[1] for call in publisher.calls)
    assert outcome.status == EnrichmentStatus.PUBLISHED


def test_secret_only_failure_exhausts_bound() -> None:
    """Secret findings alone exhaust the bound -> MANUAL_REQUIRED, no publish."""
    leaked = _grounded_description() + "\ntoken: ATATT3xFfPRM_super_secret_value\n"
    runtime = _FakeRuntime(results=[_ok(leaked), _ok(leaked)])
    outcome, _, publisher = _run(runtime=runtime)
    assert outcome.status == EnrichmentStatus.MANUAL_REQUIRED
    assert publisher.calls == []
    # Both iterations recorded the secret finding (one per matching pattern —
    # the value matches two of the high-precision patterns).
    assert sum("secret material present" in line for line in outcome.failures) >= 2
    assert any("[iteration 1]" in line for line in outcome.failures)
    assert any("[iteration 2]" in line for line in outcome.failures)


# --- publisher seam + determinism ---------------------------------------------


def test_no_publisher_still_reports_published() -> None:
    """Publisher=None: outcome still PUBLISHED; the caller owns persistence."""
    outcome, _, _ = _run(
        runtime=_FakeRuntime(results=[_ok(_grounded_description())]), publisher=None
    )
    assert outcome.status == EnrichmentStatus.PUBLISHED
    assert outcome.enriched_description is not None


def test_determinism_equal_inputs_equal_outcomes() -> None:
    """(MAS §12.7) Equal inputs -> equal outcomes (fixtures drive the runtime)."""
    outcomes = []
    for _ in range(2):
        outcome, _, _ = _run(runtime=_FakeRuntime(results=[_ok(_grounded_description())]))
        outcomes.append(outcome)
    assert outcomes[0] == outcomes[1]


def test_ticket_id_always_the_controller_argument() -> None:
    """The model's echoed ticket_id never overrides the controller argument."""
    hostile = AgentRunResult(
        agent="enrichment",
        ticket_id="EVIL",
        success=True,
        output={"description": _grounded_description()},
    )
    outcome, runtime, _ = _run(runtime=_FakeRuntime(results=[hostile]))
    assert outcome.status == EnrichmentStatus.PUBLISHED
    assert outcome.ticket_id == _TICKET_ID
    assert all(req.ticket_id == _TICKET_ID for req in runtime.captured)
