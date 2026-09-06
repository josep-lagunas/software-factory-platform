"""The in-loop ticket enrichment agent (SFP-243) — gate-driven, parse-verified,
loop-bounded.

This module closes the readiness-gate enrichment loop natively inside the
pipeline. Given the gate's structured blocking output
(:class:`~sfp_contracts.agents.readiness.ReadinessOutput`) plus the landed repo
surface, it drives an enrichment model run through the vendor-neutral
:class:`~sfp_agent_runtime.interfaces.AgentRuntime` seam to produce an enriched
ID-070 ticket description, then — deterministically, outside the model —
dry-runs that description through the landed parser
(:func:`workspace_worker.repo.jira.parser.adf_to_parsed_ticket`) and refuses to
publish anything that does not carry all eight literal section headers with
non-empty bodies. At most ``2`` iterations are attempted before
``MANUAL_REQUIRED`` is surfaced; there is no third silent retry.

Grounded in:
- SFP-243 (Jira) — the implementation ticket (this module, its prompt
  fragments under ``prompts/enrichment/``, and the reusable dry-run helper
  :mod:`workspace_worker.workflow.description_dry_run`).
- ID-070 — the eight mandatory ticket sections; the enriched description must
  carry them as **exact literal standalone headers** (no decorated headings —
  the dry-run rejects those because the parser's header match is literal).
- ID-019 / ID-063 — manual-core agent hosted via the AgentRuntime seam,
  following the readiness placement precedent (see the placement note below):
  no credentials of its own, no sandbox, a reasoning-over-text task.
- SFP-232/236 — the gate's structured abort output
  (``blocking_ambiguities`` / ``missing_inputs`` / ``rubric_failed``) is the
  driver; it is pinned against the landed :class:`ReadinessOutput` schema by
  import, not by restating its fields.
- MAS §12.9 — no invented grounding: every symbol the description references
  must exist on main (read-verified through the injected ``grounding`` seam);
  an unverifiable symbol is rejected, not published.
- ID-067 — fail-closed: every model failure mode (raised / ``success=False`` /
  ``None`` output / invalid output) counts against the loop bound as a failed
  iteration; nothing malformed is ever written as final.

Placement note (binding; recorded per the PRSpec's soft-dependency fallback):
the PRSpec's SOFT dependency names the ``readiness_host`` placement precedent
(SFP-149, ``orchestrator/application/readiness_host.py``) with the instruction
"adapt module placement to wherever readiness landed … no placeholder imports,
no unlanded hard dependency". That precedent is NOT reusable here for two
independent, read-verified reasons: (1) the orchestrator's readiness_host
explicitly vendors the gate rather than importing the workspace-worker
because the services are peers with no declared cross-dependency — importing
``workspace_worker.repo.jira.parser`` from the orchestrator would re-create
exactly the undeclared dependency that host's binding decision exists to
avoid; (2) the PRSpec itself pins the dry-run helper as "importing
workspace_worker.repo.jira.parser", which fixes the helper (and by
colocation, the agent) in the workspace-worker. The module therefore lands in
``workspace_worker.workflow`` alongside the gate whose structured output it
consumes — the same layer that owns ``readiness_gate.py`` / ``readiness_rubric.py``.

Determinism (MAS §12.7): the controller loop is a pure function of its inputs
— fixtures drive the fake runtime in tests, the per-iteration prompt is built
from the (deterministic) accumulated failure list, and no clock / network /
randomness enters the loop itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sfp_agent_runtime.interfaces import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntime,
    PromptProvider,
)
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.agents.readiness import ParsedTicket, ReadinessOutput

from workspace_worker.workflow.description_dry_run import dry_run_markdown

__all__ = [
    "DescriptionPublisher",
    "EnrichmentDecision",
    "EnrichmentOutcome",
    "EnrichmentStatus",
    "enrich_ticket",
]

#: The agent role and task names used to resolve the enrichment prompt. These
#: select ``prompts/enrichment.md`` and ``prompts/enrichment/enrich.md`` via
#: the :class:`PromptBuilder` fragment layout (shared -> role -> task; ID-059).
_AGENT = "enrichment"
_TASK = "enrich"

#: Directory holding the default enrichment prompt fragments, colocated with
#: this package (``prompts/`` — the same dir the readiness gate resolves
#: against). Exposed as a module attribute so tests may redirect it to a temp
#: dir without seeding real files.
_DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

#: The binding loop bound (SFP-243): at most two enrichment iterations — each
#: iteration is one model generate plus one parser dry-run — before
#: ``MANUAL_REQUIRED`` is surfaced. A third retry is never attempted.
MAX_ITERATIONS: int = 2

#: Secret-scan oracle for the "no secret material" hard rule. A description
#: whose text contains any of these patterns is rejected before the dry-run
#: can bless it — fail-closed on the highest-stakes failure mode of an
#: enrichment loop that writes to Jira. Patterns are deliberately
#: high-precision (token-shaped literals + labelled assignments), not a
#: generic word list: enrichment text legitimately discusses configuration
#: *keys*, so only value-shaped material is rejected.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),  # GitHub PAT families
    re.compile(r"ATATT[a-zA-Z0-9_\-]{20,}"),  # Atlassian API token
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),  # generic sk- bearer tokens
    re.compile(r"(?i)\b(token|password|api[_-]?key|secret)\b\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class EnrichmentStatus:
    """Namespace of the enrichment outcome statuses (string constants).

    Deliberately plain ``str`` constants rather than an enum: the outcome is
    an internal pipeline signal consumed by the ticket pipeline's abort path,
    not a cross-agent contract payload — the two statuses map onto the
    relaunch decision the PRSpec scopes ("emitting the relaunch decision /
    MANUAL_REQUIRED signal"), and ``MANUAL_REQUIRED`` reuses the gate's exact
    verdict spelling so logs read consistently.
    """

    PUBLISHED: str = "PUBLISHED"
    MANUAL_REQUIRED: str = "MANUAL_REQUIRED"


@dataclass(frozen=True, slots=True)
class EnrichmentDecision:
    """The relaunch decision the enrichment outcome carries (SFP-243 scope).

    The agent does NOT orchestrate the Jira relaunch (out of scope); it emits
    the decision for the caller — ``RELAUNCH`` when an enriched description
    was published (the pipeline may re-run the gate against it), ``ABORT`` when
    ``MANUAL_REQUIRED`` was surfaced (a human must intervene).
    """

    RELAUNCH: str = "RELAUNCH"
    ABORT: str = "ABORT"


#: Module-level singleton of the decision constants (frozen dataclass — the
#: enum-like surface without a runtime enum dependency at the call sites).
ENRICHMENT_DECISION = EnrichmentDecision()


@dataclass(frozen=True, slots=True)
class EnrichmentOutcome:
    """The result of one :func:`enrich_ticket` invocation.

    Attributes:
        ticket_id: The ticket that was enriched (always the controller
            argument — never anything the model echoed).
        status: ``PUBLISHED`` when the enriched description passed the dry-run
            and was written via the Jira seam; ``MANUAL_REQUIRED`` when the
            loop bound was exhausted without a green dry-run.
        decision: The relaunch decision for the caller — ``RELAUNCH`` iff
            published, else ``ABORT`` (see :class:`EnrichmentDecision`).
        enriched_description: The published Markdown description (``None`` on
            ``MANUAL_REQUIRED`` — a failing description is never surfaced as
            final).
        iterations: How many model iterations were consumed (bounded by
            :data:`MAX_ITERATIONS`).
        failures: The accumulated per-iteration failure lines, in iteration
            order — the exact reasons each candidate was rejected (dry-run
            section failures, secret-scan hits, invalid-output messages,
            run-failure messages). Deterministic; intended for the
            ``MANUAL_REQUIRED`` report a human reads.
    """

    ticket_id: str
    status: str
    decision: str
    enriched_description: str | None = None
    iterations: int = 0
    failures: tuple[str, ...] = field(default=())


class GroundingContext(Protocol):
    """Read-only repo-surface seam the enrichment prompt grounds against.

    The controller never reads the repo itself (it stays pure); the caller
    injects a grounded view — gathered by READ operations against **main
    only** — exposing the symbols that exist on the landed surface. The
    production realization resolves names against the repo checkout; tests
    inject a fixture view. ``symbol_exists`` is the spot-check contract:
    ``False`` means "not on main as far as the landed surface shows", which
    makes a description that cites the symbol ungrounded and unpublishable.
    """

    def symbol_exists(self, symbol: str) -> bool:
        """Return ``True`` iff ``symbol`` exists on landed main (read-verified)."""
        ...


@dataclass
class _StaticGrounding:
    """A :class:`GroundingContext` over a fixed symbol set (fixtures / tests).

    Also the fallback the controller uses when no grounding seam is injected:
    every symbol is reported absent, so any symbol-referencing description is
    rejected — fail-closed grounding (no invented grounding, MAS §12.9).
    """

    symbols: frozenset[str] = field(default_factory=frozenset)

    def symbol_exists(self, symbol: str) -> bool:
        return symbol in self.symbols


def _secret_findings(markdown: str) -> list[str]:
    """Return the secret-material findings in ``markdown`` (fail-closed scan).

    High-precision token-shaped patterns only (see :data:`_SECRET_PATTERNS`):
    a hit means the description carries value-shaped secret material and must
    be rejected outright — it never reaches the dry-run's blessing, let alone
    the Jira write. Each finding names the matched pattern's first token so
    the retry prompt can say what to strip, without echoing the secret.
    """
    findings: list[str] = []
    for pattern in _SECRET_PATTERNS:
        match = pattern.search(markdown)
        if match is not None:
            head = match.group(0)[:8]
            findings.append(f"secret material present (match starts {head!r})")
    return findings


def _grounding_findings(markdown: str, grounding: GroundingContext) -> list[str]:
    """Return ungrounded-symbol findings for backtick-quoted spans in ``markdown``.

    The prompt directs the model to cite landed symbols inside backticks
    (``workspace_worker.workflow.readiness_gate``); this check extracts those
    spans and asks the injected :class:`GroundingContext` whether each exists
    on main. An absent symbol is an invented-grounding finding — the
    description is rejected. This is the deterministic enforcement of the
    "grounding only against code landed on main (read-verified)" hard rule.
    """
    findings: list[str] = []
    for span in sorted(set(re.findall(r"`([^`\n]+)`", markdown))):
        candidate = span.strip()
        if candidate and not grounding.symbol_exists(candidate):
            findings.append(f"ungrounded symbol: {candidate}")
    return findings


def _validate_enriched_output(raw: object) -> str:
    """Validate the model run's output into the enriched Markdown description.

    The model's structured output is ``{"description": "<markdown>"}`` — a
    single string field, kept minimal so schema drift has no room to hide.
    Anything else (non-mapping, missing field, non-string value, empty string)
    raises :class:`ValueError` naming the failure; the caller counts the
    iteration as failed.

    Raises:
        ValueError: the output is not a mapping carrying a non-empty string
            ``description``.
    """
    if not isinstance(raw, Mapping):
        raise ValueError(f"enrichment output must be a mapping, got {type(raw).__name__}")
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("enrichment output field 'description' must be a non-empty string")
    return description


def _iteration_context(
    *,
    ticket_id: str,
    ticket: ParsedTicket,
    gate: ReadinessOutput,
    template: Mapping[str, Any],
    grounding_symbols: Sequence[str],
    prior_failures: Sequence[str],
) -> Mapping[str, Any]:
    """Build the opaque per-iteration context mapping for the run request.

    Everything the model needs, verbatim: the ticket's current sections, the
    gate's structured blocking output rendered as-is (SFP-236: untransformed),
    the per-ticket-type template, the read-verified landed-symbol list, and —
    from iteration 2 on — the previous iteration's exact failure lines so the
    retry targets them.
    """
    context: dict[str, Any] = {
        "ticket_id": ticket_id,
        "ticket": ticket.model_dump(),
        "gate": {
            "blocking_ambiguities": list(gate.blocking_ambiguities),
            "missing_inputs": list(gate.missing_inputs),
            "rubric_failed": [name for name, passed in gate.rubric_results.items() if not passed],
        },
        "template": dict(template),
        "grounding_symbols": list(grounding_symbols),
    }
    if prior_failures:
        context["prior_iteration_failures"] = list(prior_failures)
    return context


def _run_one_iteration(
    runtime: AgentRuntime,
    request: AgentRunRequest,
) -> tuple[str | None, str | None]:
    """Run one enrichment model iteration (fail-closed, ID-067).

    Returns a ``(description, failure)`` pair. On success ``description`` is
    the validated enriched Markdown and ``failure`` is ``None``. On any
    failure mode — the run raised, returned ``success=False``, returned a
    ``None`` output, or produced output that fails
    :func:`_validate_enriched_output` — ``description`` is ``None`` and
    ``failure`` is the descriptive line that names the mode.
    """
    try:
        result: AgentRunResult = runtime.run(request)
    except Exception as exc:  # noqa: BLE001 - fail-closed: catch broadly (ID-067)
        return None, f"enrichment model run raised: {type(exc).__name__}: {exc}"

    if not result.success:
        err = result.error if result.error is not None else "unknown"
        return None, f"enrichment model run failed: {err}"

    if result.output is None:
        return None, "enrichment model returned no output"

    try:
        return _validate_enriched_output(result.output), None
    except ValueError as exc:
        return None, f"enrichment model output invalid: {exc}"


def enrich_ticket(
    ticket: ParsedTicket,
    gate: ReadinessOutput,
    *,
    runtime: AgentRuntime,
    ticket_id: str,
    template: Mapping[str, Any] | None = None,
    grounding: GroundingContext | None = None,
    prompt_provider: PromptProvider | None = None,
    publisher: DescriptionPublisher | None = None,
) -> EnrichmentOutcome:
    """Enrich a blocked ticket's description until the parser dry-run is green.

    The controller loop (binding, SFP-243): at most :data:`MAX_ITERATIONS`
    iterations, each iteration = one model generate + one deterministic
    post-model check. An iteration's candidate is published **only** when it
    clears ALL of the outside-the-model gates, checked in fail-closed order:

    1. **Secret scan** — no value-shaped secret material (:func:`_secret_findings`).
    2. **Grounding spot-check** — every backtick-cited symbol exists on main
       (:func:`_grounding_findings` against the injected seam).
    3. **Parser dry-run** — ``dry_run_markdown`` reports all eight ID-070
       sections present and non-empty (the sole publish authority; decorated
       headers are rejected here because the parser's header match is
       literal).

    Only a candidate that clears all three is handed to the injected
    ``publisher`` (the Jira client seam). Every failure — a rejected candidate
    or a model failure mode — counts against the loop bound; exhausting the
    bound surfaces ``MANUAL_REQUIRED`` with the accumulated failure lines.
    There is no third silent retry.

    Args:
        ticket: The parsed ticket as it stands (the sections to enrich).
        gate: The gate's structured blocking output (SFP-232/236) — rendered
            verbatim into every iteration's prompt context. Pinned against
            the landed :class:`ReadinessOutput` by import.
        runtime: The vendor-neutral agent runtime used to run the model.
        ticket_id: The ticket identifier — always echoed into the run requests
            and the outcome; never taken from the model.
        template: Per-ticket-type template inputs (emitter/transition/hosting
            clones, per the 2026-08-28 ORCH batch). Opaque to the controller;
            passed through into the prompt context.
        grounding: The read-verified repo-surface seam. When ``None``, a
            static empty view is used — fail-closed grounding: any cited
            symbol is ungrounded and the candidate is rejected (MAS §12.9 —
            the controller never invents a landed surface).
        prompt_provider: Optional prompt provider. If ``None``, the default
            :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR` is used.
        publisher: The Jira-client-seam description writer. When ``None``, no
            write is performed — the outcome still reports ``PUBLISHED`` with
            the description, and the caller owns persistence (composition-root
            flexibility; the tests use this to prove a failing description is
            never even offered to a publisher).

    Returns:
        The :class:`EnrichmentOutcome`. ``status`` is ``PUBLISHED`` with the
        description set, or ``MANUAL_REQUIRED`` with ``enriched_description``
        ``None`` and the accumulated failures.
    """
    grounding_view: GroundingContext = grounding if grounding is not None else _StaticGrounding()
    grounding_symbols: Sequence[str] = sorted(getattr(grounding_view, "symbols", frozenset()) or ())

    if prompt_provider is not None:
        prompt = prompt_provider.get_prompt(_AGENT, _TASK)
    else:
        prompt = PromptBuilder(_DEFAULT_PROMPT_DIR).get_prompt(_AGENT, _TASK)

    failures: list[str] = []
    for iteration in range(1, MAX_ITERATIONS + 1):
        context = _iteration_context(
            ticket_id=ticket_id,
            ticket=ticket,
            gate=gate,
            template=template if template is not None else {},
            grounding_symbols=grounding_symbols,
            prior_failures=failures,
        )
        request = AgentRunRequest(
            agent=_AGENT,
            ticket_id=ticket_id,
            prompt=prompt,
            context=context,
        )
        description, failure = _run_one_iteration(runtime, request)
        if description is not None:
            rejects: list[str] = []
            rejects.extend(_secret_findings(description))
            rejects.extend(_grounding_findings(description, grounding_view))
            rejects.extend(dry_run_markdown(description).failures)
            if not rejects:
                if publisher is not None:
                    publisher(ticket_id, description)
                return EnrichmentOutcome(
                    ticket_id=ticket_id,
                    status=EnrichmentStatus.PUBLISHED,
                    decision=ENRICHMENT_DECISION.RELAUNCH,
                    enriched_description=description,
                    iterations=iteration,
                    failures=tuple(failures),
                )
            failures.extend(f"[iteration {iteration}] {line}" for line in rejects)
        else:
            assert failure is not None  # the failure branch always names itself
            failures.append(f"[iteration {iteration}] {failure}")

    return EnrichmentOutcome(
        ticket_id=ticket_id,
        status=EnrichmentStatus.MANUAL_REQUIRED,
        decision=ENRICHMENT_DECISION.ABORT,
        enriched_description=None,
        iterations=MAX_ITERATIONS,
        failures=tuple(failures),
    )


#: The Jira-client-seam description writer: ``(ticket_id, description) -> None``.
#: The production realization adapts :class:`~workspace_worker.repo.jira.client.JiraClient`
#: (a thin closure over ``PUT /rest/api/3/issue/{key}`` carrying the converted
#: ADF); the enrichment controller knows it only as this callable so the write
#: path stays a seam, not a dependency.
DescriptionPublisher = Callable[[str, str], None]
