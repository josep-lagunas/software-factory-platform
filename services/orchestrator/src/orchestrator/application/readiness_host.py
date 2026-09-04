"""The orchestrator-side hosting of the Readiness Gate (SFP-149).

This module is **hosting glue only** — the ReadinessGateHost wires the landed
layer-2 model evaluation (SFP-51/SFP-232 in the workspace-worker) into the
Orchestrator's pre-planning step (ID-064: the gate runs *before* the Planner,
SFP-150). It restates none of the gate's rubric/model logic (MAS §12.9) and
invents no policy of its own: it builds one :class:`AgentRunRequest`, executes
it through the injected :class:`AgentRuntime` seam, validates the result into
the shared :class:`~sfp_contracts.agents.readiness.ReadinessOutput` contract,
and routes **exhaustively** on the verdict.

Grounded in:
- ID-064 (amended) — the Readiness evaluator runs as a *direct model call
  inside the Orchestrator* (not via the Workspace Worker's agentic runtime);
  readiness is a reasoning-over-text task needing no tool-use, repo access, or
  sandbox. This host is that placement, realized through the vendor-neutral
  AgentRuntime seam so the platform still names no vendor SDK (AP-010 /
  MAS §9.6).
- ID-065 — ``verdict`` drives routing: ``READY`` proceeds to the Planner,
  ``NEEDS_CLARIFICATION`` routes back to the user for disambiguation,
  ``MANUAL_REQUIRED`` escalates to a human. The routing below is an exhaustive
  match over :class:`ReadinessVerdict` — no verdict is swallowed and no branch
  falls through.
- ID-067 — fail-closed applies to the *gate's own* composition in the
  workspace-worker. This host deliberately does NOT import that composition:
  its binding failure semantics are **raise, never swallow** (see the vendoring
  note below), so a runtime failure here propagates to the caller rather than
  being converted into a verdict the caller would act on.
- ID-072 — the Orchestrator *decides*; every seam (runtime, request builder,
  identity source) is constructor-injected by the composition root — no
  service-locator lookups, no globals, no direct endpoint or ``httpx`` call
  (out of scope by the PRSpec).
- SFP-236 — on ``NEEDS_CLARIFICATION`` the blocking ambiguities and missing
  inputs are surfaced **verbatim**: untransformed, in order, byte-identical.
  The enrichment-retry decision belongs to the caller (the scheduler/host
  loop), not this host.

Import decision (binding; recorded in the PR description per the PRSpec):

  The thin layer-2 composition is **vendored** here rather than imported from
  the workspace-worker. The deterministic procedure the PRSpec prescribed —
  "reuse if ``workspace_worker.evaluate_readiness`` is importable under the
  repo's layering rules, else vendor" — resolves to **vendor**, for two
  independent reasons that each rule the reuse branch out:

  1. *Layering:* ``orchestrator`` does not declare ``workspace-worker`` as a
     dependency (see ``services/orchestrator/pyproject.toml``); the services
     are peers under MAS §9.6, and the repo's only existing
     orchestrator→workspace-worker references are lazy, test-only imports
     (``tests/domain/workflow/policies/``). A src-level import would be an
     undeclared cross-service dependency, so under the repo's layering rules
     the reuse branch is not available.
  2. *Failure semantics:* the workspace-worker's ``evaluate_readiness`` is
     fail-closed (ID-067) — it converts a runtime failure into
     ``NEEDS_CLARIFICATION``. This host's binding contract is the opposite:
     a runtime failure must **raise** so no verdict is invented or swallowed
     (PRSpec acceptance criterion). Reusing that composition would silently
     violate the ticket's failure semantics.

  Vendored source (cited, not forked-then-drifted): the layer-2 model half of
  ``services/workspace-worker/src/workspace_worker/workflow/readiness_gate.py``
  (SFP-68: ``_run_model`` + the request construction in
  ``evaluate_readiness`` steps 2–4). The layer-1 rubric, the prompt-fragment
  layout, and the binding combination rule are NOT vendored — they are out of
  scope here (the rubric/model logic stays with SFP-51/SFP-232, per the
  PRSpec's out_of_scope), and this host's caller supplies the
  fully-constructed request through the injected request-builder seam.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult, AgentRuntime
from sfp_contracts.agents.readiness import (
    ParsedTicket,
    ReadinessOutput,
    ReadinessVerdict,
)

if TYPE_CHECKING:
    #: A caller-supplied identity source for envelope/checkpoint correlation.
    #: The host never invents correlation or checkpoint identity — it asks the
    #: injected source, which may derive it from persistent state, the caller's
    #: frame, or a fixed value in tests. Deterministic by contract.
    IdentitySource = Callable[[str], str]

    #: Builds the readiness :class:`AgentRunRequest` for one ticket. The
    #: default construction mirrors the workspace-worker gate's request shape
    #: (agent role ``"readiness"``, the resolved prompt, and the opaque ticket
    #: context); a caller may inject a custom builder to add routing metadata
    #: without changing this module.
    RequestBuilder = Callable[[str, ParsedTicket], AgentRunRequest]

    #: The PromptProvider-equivalent seam: resolves the prompt text for the
    #: readiness agent's evaluate task (ID-059 — the prompt itself lives on
    #: disk behind this seam, never inlined in this source).
    PromptResolver = Callable[[str, str], str]

__all__ = ["ReadinessGateHost"]

#: The agent role and task names identifying the readiness model run. These
#: mirror the workspace-worker gate's constants (cited source) so both hosts
#: address the same prompt fragments and model routing (ID-063).
_AGENT = "readiness"
_TASK = "evaluate"


def _ticket_identity(ticket_id: str) -> str:
    """A no-op identity source: echo the ticket id unchanged.

    Correlation for a single-ticket readiness run is well-defined as the
    ticket id itself; a caller with richer envelope/checkpoint semantics
    injects its own source.
    """
    return ticket_id


def _default_request_builder(
    ticket_id: str, parsed: ParsedTicket, *, prompt_resolver: PromptResolver
) -> AgentRunRequest:
    """Build the readiness request, mirroring the vendored source's shape.

    Reproduces the request construction of the cited workspace-worker gate
    (``evaluate_readiness`` step 3) exactly in structure: agent role
    ``"readiness"``, the ticket id (always the gate argument — never taken
    from the model), the resolved prompt, and the ticket payload as opaque
    context. No routing/identity fields are invented here.
    """
    return AgentRunRequest(
        agent=_AGENT,
        ticket_id=ticket_id,
        prompt=prompt_resolver(_AGENT, _TASK),
        context={"ticket_id": ticket_id, "ticket": parsed.model_dump()},
    )


def _validate_result(output: object) -> ReadinessOutput:
    """Validate the run's opaque output into the shared contract.

    A payload that does not validate raises :class:`ValueError` naming the
    failure — it is never coerced, defaulted, or dropped: contract drift
    surfaces immediately rather than silently changing semantics (the
    PRSpec's drift risk).
    """
    if not isinstance(output, Mapping):
        raise ValueError(f"Readiness runtime output must be a mapping, got {type(output).__name__}")
    return ReadinessOutput.model_validate(output)


class ReadinessGateHost:
    """Host the Readiness Gate as the Orchestrator's pre-planning step (SFP-149).

    Runs the layer-2 readiness model evaluation through the injected
    :class:`~sfp_agent_runtime.interfaces.AgentRuntime` seam and routes
    exhaustively on the resulting verdict (ID-065). The host performs no
    scheduling, no persistence, and no I/O beyond the runtime seam; it holds no
    mutable state, so the same inputs always yield the same output (MAS §12.7
    — the model call itself sits behind the injected seam).

    Constructor-injected seams (ID-072; no service-locator, no globals):

    - ``runtime`` — the vendor-neutral ``AgentRuntime`` used to execute the
      model run. The only I/O seam this module touches.
    - ``request_builder`` — builds the readiness ``AgentRunRequest`` from
      ``(ticket_id, parsed)``. When omitted, the default builder is used with
      the injected ``prompt_resolver``; when supplied it replaces the default
      entirely (single path — the host never mixes the two).
    - ``prompt_resolver`` — the ``PromptProvider``-equivalent callable
      (``get_prompt``-shaped) resolving the readiness prompt text (ID-059).
    - ``identity_source`` — supplies the identity string used for
      envelope/checkpoint correlation. The host never invents identity; the
      default echoes the ticket id.

    Failure semantics (binding): any runtime failure propagates out of
    :meth:`run_for_ticket` — nothing is swallowed, no default verdict is
    invented, and there is no retry; each call performs exactly one runtime run
    and each verdict route returns exactly one output object. The in-band
    failure modes (``success=False``, a ``None`` output, an unvalidatable
    payload) raise :class:`ReadinessGateRuntimeError`; if ``runtime.run`` itself
    raises, the provider's own exception crosses this module **untouched** — the
    caller sees the original error, never a synthesized verdict.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        request_builder: RequestBuilder | None = None,
        prompt_resolver: PromptResolver | None = None,
        identity_source: IdentitySource | None = None,
    ) -> None:
        self._runtime = runtime
        self._request_builder = request_builder
        self._prompt_resolver = prompt_resolver
        self._identity_source = identity_source if identity_source is not None else _ticket_identity

    async def run_for_ticket(self, ticket_id: str, parsed: ParsedTicket) -> ReadinessOutput:
        """Run the readiness gate for one ticket and route on its verdict.

        Flow: (1) build the request via the injected/default builder; (2)
        execute exactly one runtime run (``await``-friendly: the run itself is
        synchronous behind the seam); (3) fail-propagate on any runtime failure
        mode; (4) validate into :class:`ReadinessOutput`; (5) route
        exhaustively on the verdict.

        Routing (exhaustive over :class:`ReadinessVerdict`; ID-065):

        - ``READY`` — return the validated output unchanged; the caller
          proceeds to the Planner host (SFP-150, out of scope here).
        - ``NEEDS_CLARIFICATION`` — return the output with
          ``blocking_ambiguities`` and ``missing_inputs`` surfaced verbatim,
          untransformed (SFP-236). Whether to enrich and retry is the caller's
          decision; this host never retries.
        - ``MANUAL_REQUIRED`` — return the output flagged by its verdict; the
          caller surfaces it to a human. The host never auto-proceeds on this
          verdict (no Planner call is even possible from here).

        Args:
            ticket_id: The ticket being assessed. Always echoed into the
                result and used verbatim in the request — never overridden by
                the model output.
            parsed: The parsed ticket (ID-070 sections) carried into the
                request context.

        Returns:
            The validated :class:`ReadinessOutput` — exactly one object per
            route.

        Raises:
            ReadinessGateRuntimeError: The runtime run failed (``success
                =False``), returned no output, or returned an unvalidatable
                payload. Never swallowed, never defaulted.
            Exception: whatever ``runtime.run`` itself raises, propagated
                untouched (not wrapped, not replaced) so the caller sees the
                provider's own error.
        """
        request = self._build_request(ticket_id, parsed)
        identity = self._identity_source(ticket_id)
        result = self._run_once(request)

        if not result.success:
            error = result.error if result.error is not None else "unknown error"
            raise ReadinessGateRuntimeError(
                f"Readiness runtime run failed for {ticket_id} (identity {identity!r}): {error}"
            )
        if result.output is None:
            raise ReadinessGateRuntimeError(
                f"Readiness runtime returned no output for {ticket_id} (identity {identity!r})"
            )
        try:
            output = _validate_result(result.output)
        except ValueError as exc:  # ValidationError is a ValueError subclass
            raise ReadinessGateRuntimeError(
                f"Readiness runtime output invalid for {ticket_id} (identity {identity!r}): {exc}"
            ) from exc

        # The gate argument is authoritative (vendored-source rule): the
        # returned ticket id is always the argument, never the model's echo.
        return self._route(ticket_id, output.model_copy(update={"ticket_id": ticket_id}))

    def _build_request(self, ticket_id: str, parsed: ParsedTicket) -> AgentRunRequest:
        """Build the request through the injected builder, or the default one."""
        if self._request_builder is not None:
            return self._request_builder(ticket_id, parsed)
        resolver = self._prompt_resolver
        if resolver is None:
            raise ReadinessGateRuntimeError(
                "ReadinessGateHost requires either request_builder or prompt_resolver "
                "(no default prompt source is available in the orchestrator; "
                "ID-059 keeps prompt text out of source)"
            )
        return _default_request_builder(ticket_id, parsed, prompt_resolver=resolver)

    def _run_once(self, request: AgentRunRequest) -> AgentRunResult:
        """Execute exactly one runtime run, letting exceptions propagate.

        No retry, no swallow: if ``runtime.run`` raises, the exception crosses
        this frame untouched (the caller sees the provider's own error, not a
        synthesized verdict).
        """
        return self._runtime.run(request)

    def _route(self, ticket_id: str, output: ReadinessOutput) -> ReadinessOutput:
        """Route exhaustively on the verdict (ID-065).

        Every member of :class:`ReadinessVerdict` has exactly one arm that
        returns exactly one output object; the trailing ``assert`` is
        unreachable by construction and exists only to make exhaustiveness a
        runtime guarantee rather than a reviewer's promise.
        """
        verdict = output.verdict
        if verdict is ReadinessVerdict.READY:
            # READY: pass the validated output through unchanged.
            return output
        if verdict is ReadinessVerdict.NEEDS_CLARIFICATION:
            # NEEDS_CLARIFICATION: surface blocking_ambiguities / missing_inputs
            # verbatim (SFP-236) — same object, no transformation, no retry.
            return output
        if verdict is ReadinessVerdict.MANUAL_REQUIRED:
            # MANUAL_REQUIRED: return flagged by its verdict; the caller
            # surfaces it to a human. Never auto-proceed.
            return output
        raise AssertionError(  # pragma: no cover - unreachable: exhaustive match
            f"Unhandled readiness verdict {verdict!r} for {ticket_id}"
        )


class ReadinessGateRuntimeError(RuntimeError):
    """A readiness runtime run failed; no verdict was produced (SFP-149).

    Raised (never swallowed) when the injected runtime reports failure, no
    output, or an unvalidatable payload. Carries the ticket id in its message
    so the caller can correlate the failure without re-parsing.
    """
