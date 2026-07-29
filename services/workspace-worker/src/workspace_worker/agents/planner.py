"""The Planner agent (SFP-70; DOC SFP-53; ID-021 / ID-066 / ID-067).

This module is the Planner evaluator. It composes a parsed ready ticket and its
resolved context with a model run produced through the vendor-neutral
:class:`~sfp_agent_runtime.interfaces.AgentRuntime` seam, and returns a single
typed :class:`~sfp_contracts.agents.planner.PlannerOutput` — a deterministic,
PR-sized decomposition of the ticket into ``pr_specs[]``.

Grounded in:
- ID-021 — Planner output is deterministic JSON composed of small, self-contained
  pull-request tasks (the "what to build" front-loading).
- ID-066 — the ``planner-output`` payload is ``pr_specs[]``; every agent emits a
  strict JSON contract, and unknown fields are rejected (``extra='forbid'``).
- ID-067 — each PR-spec carries a ``validation_profile`` (the
  :class:`~sfp_contracts.validation.profiles.ValidationProfile` enum) that
  selects its risk-tiered gate set; when unsure, the Planner picks the higher
  level.
- ID-059 — no prompt text is inlined in source; the default prompt is built by
  the landed :class:`~sfp_agent_runtime.prompt_builder.PromptBuilder` against the
  module-level :data:`_DEFAULT_PROMPT_DIR`, which points at the ``prompts/`` dir
  shipped with this ticket (``shared.md`` landed in SFP-68; ``planner.md`` and
  ``planner/plan.md`` land here).

Fail-closed (ID-067): any failure mode of the model run raises a dedicated
:class:`PlannerError` carrying the cause. The Planner never silently returns
malformed output — unlike the readiness gate (which has a NEEDS_CLARIFICATION
fallback), there is no sensible "partial" PR decomposition, so failure surfaces
as an exception rather than a degenerate :class:`PlannerOutput`.

Structural precedent: this module mirrors the landed readiness gate
(:mod:`workspace_worker.workflow.readiness_gate`, SFP-68) — same runtime seam,
same prompt-resolution contract, same fail-closed posture, differing only in the
output contract and in raising rather than returning a degenerate payload.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sfp_agent_runtime.interfaces import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntime,
    PromptProvider,
)
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.agents.planner import PlannerOutput
from sfp_contracts.agents.readiness import ParsedTicket
from sfp_contracts.context.bindings import ResolvedContext

__all__ = ["PlannerError", "plan"]

#: The agent role and task names used to resolve the planner prompt. These
#: select ``prompts/planner.md`` and ``prompts/planner/plan.md`` via the
#: :class:`PromptBuilder` fragment layout (shared -> role -> task; ID-059).
_AGENT = "planner"
_TASK = "plan"

#: Directory holding the default planner prompt fragments, colocated with the
#: sibling ``prompts/`` dir (``../prompts`` relative to this module — the same
#: dir the readiness gate resolves against). Exposed as a module attribute so
#: tests may redirect it to a temp dir without seeding real files.
_DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class PlannerError(Exception):
    """Raised when the Planner model run fails or yields a non-conformant output.

    Fail-closed sentinel (ID-067). The Planner returns a strict
    :class:`~sfp_contracts.agents.planner.PlannerOutput`; every failure mode of
    the model run — the run raised, returned ``success=False``, returned a
    ``None`` output, or produced output that fails
    :meth:`~sfp_contracts.agents.planner.PlannerOutput.model_validate` —
    surfaces as this exception carrying the cause. The Planner never silently
    returns a degenerate payload: unlike the readiness gate (which has a
    NEEDS_CLARIFICATION fallback), there is no sensible "partial" PR
    decomposition, so failure surfaces as an exception rather than a malformed
    :class:`PlannerOutput`.
    """


def _run_model(runtime: AgentRuntime, request: AgentRunRequest) -> PlannerOutput:
    """Run the planner model and validate its output (fail-closed, ID-067).

    Returns the validated :class:`PlannerOutput` on success. On any of the four
    failure modes — the run raised, returned ``success=False``, returned a
    ``None`` output, or produced output that fails ``model_validate`` — raises
    :class:`PlannerError` carrying a descriptive cause.

    Args:
        runtime: The vendor-neutral agent runtime used to run the model.
        request: The resolved run request.

    Returns:
        The validated :class:`PlannerOutput`.

    Raises:
        PlannerError: On any failure mode of the model run (ID-067).
    """
    try:
        result: AgentRunResult = runtime.run(request)
    except Exception as exc:  # noqa: BLE001 - fail-closed: catch broadly (ID-067)
        raise PlannerError(f"Planner model run raised: {type(exc).__name__}: {exc}") from exc

    if not result.success:
        err = result.error if result.error is not None else "unknown"
        raise PlannerError(f"Planner model run failed: {err}")

    if result.output is None:
        raise PlannerError("Planner model returned no output")

    try:
        return PlannerOutput.model_validate(result.output)
    except ValidationError as exc:
        raise PlannerError(f"Planner model output invalid: {exc}") from exc


def plan(
    ticket: ParsedTicket,
    resolved: ResolvedContext,
    *,
    runtime: AgentRuntime,
    prompt_provider: PromptProvider | None = None,
    ticket_id: str,
) -> PlannerOutput:
    """Plan a ready ticket into one or more PR-specs.

    Pipeline:
    1. Resolve the planner prompt: from ``prompt_provider`` if given, else via
       :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR`.
    2. Build an opaque context mapping and call ``runtime.run``.
    3. Validate the run output into a :class:`PlannerOutput` (fail-closed on any
       failure mode — :class:`PlannerError`).

    The ``ticket_id`` argument is ALWAYS echoed into the run request and never
    taken from the model output (the :class:`PlannerOutput` contract carries no
    ``ticket_id`` field, so there is nothing to spoof — but the run request is
    still tagged with the gate argument, not anything the model supplies).

    Args:
        ticket: The parsed ready ticket to decompose.
        resolved: The ticket's resolved context.
        runtime: The vendor-neutral agent runtime used to run the model.
        prompt_provider: Optional prompt provider. If ``None``, the default
            :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR` is used.
        ticket_id: The ticket identifier — always echoed into the run request.

    Returns:
        The validated :class:`PlannerOutput` (one or more PR-specs).

    Raises:
        PlannerError: On any failure mode of the model run (ID-067).
    """
    # (1) Resolve the planner prompt (ID-059: prompts live on disk, not here).
    if prompt_provider is not None:
        prompt = prompt_provider.get_prompt(_AGENT, _TASK)
    else:
        prompt = PromptBuilder(_DEFAULT_PROMPT_DIR).get_prompt(_AGENT, _TASK)

    # (2) Build the opaque context mapping and call the model.
    context: Mapping[str, Any] = {
        "ticket_id": ticket_id,
        "ticket": ticket.model_dump(),
        "resolved": resolved.model_dump(),
    }
    request = AgentRunRequest(
        agent=_AGENT,
        ticket_id=ticket_id,
        prompt=prompt,
        context=context,
    )

    # (3) Run + validate, fail-closed.
    return _run_model(runtime, request)
