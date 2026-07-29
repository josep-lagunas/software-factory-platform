"""The Coder agent (SFP-72; DOC SFP-55; ID-022 / ID-066 / ID-067).

This module is the Coder evaluator. It composes a PR-spec and its resolved
context with a model run produced through the vendor-neutral
:class:`~sfp_agent_runtime.interfaces.AgentRuntime` seam, and returns a single
typed :class:`~sfp_contracts.agents.coder.CoderOutput` — implementation
evidence *by reference* (branch, PR URL, files changed), never the code itself
(the "judgments + references, not artifacts" rule of ID-066).

Grounded in:
- ID-022 — the Coder implements one PR-spec and submits it for review; its
  typed output drives the Orchestrator's rework/merge decision.
- ID-066 — every agent emits a strict JSON contract; unknown fields rejected
  (``extra='forbid'``). The code lives on the branch/PR, referenced — not
  carried here.
- ID-059 — the default prompt is built by
  :class:`~sfp_agent_runtime.prompt_builder.PromptBuilder` against the
  module-level :data:`_DEFAULT_PROMPT_DIR` (``shared.md`` landed in SFP-68;
  ``coder.md`` and ``coder/implement.md`` land here).

Fail-closed (ID-067): any failure mode of the model run raises a dedicated
:class:`CoderError`. There is no sensible "partial" implementation result.

Structural precedent: mirrors the landed Planner (SFP-70) / Test Designer
(SFP-71) — same runtime seam, same prompt-resolution contract, same fail-closed
posture, differing only in the output contract and that it operates on a
:class:`~sfp_contracts.agents.planner.PrSpec` rather than a parsed ticket.
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
from sfp_contracts.agents.coder import CoderOutput
from sfp_contracts.agents.planner import PrSpec
from sfp_contracts.context.bindings import ResolvedContext

__all__ = ["CoderError", "code"]

_AGENT = "coder"
_TASK = "implement"

#: Directory holding the default prompt fragments, shared with the sibling
#: agents (``../prompts`` relative to this module).
_DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class CoderError(Exception):
    """Raised when the Coder model run fails or yields non-conformant output.

    Fail-closed sentinel (ID-067). Every failure mode of the model run — the run
    raised, returned ``success=False``, returned a ``None`` output, or produced
    output that fails
    :meth:`~sfp_contracts.agents.coder.CoderOutput.model_validate` — surfaces as
    this exception carrying the cause.
    """


def _run_model(runtime: AgentRuntime, request: AgentRunRequest) -> CoderOutput:
    """Run the Coder model and validate its output (fail-closed, ID-067)."""
    try:
        result: AgentRunResult = runtime.run(request)
    except Exception as exc:  # noqa: BLE001 - fail-closed: catch broadly (ID-067)
        raise CoderError(f"Coder model run raised: {type(exc).__name__}: {exc}") from exc

    if not result.success:
        err = result.error if result.error is not None else "unknown"
        raise CoderError(f"Coder model run failed: {err}")

    if result.output is None:
        raise CoderError("Coder model returned no output")

    try:
        return CoderOutput.model_validate(result.output)
    except ValidationError as exc:
        raise CoderError(f"Coder model output invalid: {exc}") from exc


def code(
    pr_spec: PrSpec,
    resolved: ResolvedContext,
    *,
    runtime: AgentRuntime,
    prompt_provider: PromptProvider | None = None,
    ticket_id: str,
) -> CoderOutput:
    """Implement one PR-spec and return its implementation evidence.

    Pipeline:
    1. Resolve the Coder prompt: from ``prompt_provider`` if given, else via
       :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR`.
    2. Build an opaque context mapping (the PR-spec + resolved context) and call
       ``runtime.run``.
    3. Validate the run output into a :class:`CoderOutput` (fail-closed).

    The ``ticket_id`` argument is ALWAYS echoed into the run request and never
    taken from the model output.

    Args:
        pr_spec: The single PR-spec to implement.
        resolved: The ticket's resolved context.
        runtime: The vendor-neutral agent runtime used to run the model.
        prompt_provider: Optional prompt provider. If ``None``, the default
            :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR` is used.
        ticket_id: The ticket identifier — always echoed into the run request.

    Returns:
        The validated :class:`CoderOutput` (implementation evidence by reference).

    Raises:
        CoderError: On any failure mode of the model run (ID-067).
    """
    if prompt_provider is not None:
        prompt = prompt_provider.get_prompt(_AGENT, _TASK)
    else:
        prompt = PromptBuilder(_DEFAULT_PROMPT_DIR).get_prompt(_AGENT, _TASK)

    context: Mapping[str, Any] = {
        "ticket_id": ticket_id,
        "pr_spec": pr_spec.model_dump(),
        "resolved": resolved.model_dump(),
    }
    request = AgentRunRequest(
        agent=_AGENT,
        ticket_id=ticket_id,
        prompt=prompt,
        context=context,
    )

    return _run_model(runtime, request)
