"""The Reviewer agent (SFP-73; DOC SFP-56; ID-022 / ID-066 / ID-067).

This module is the Reviewer evaluator — a *judgment-only* agent. It composes the
PR-spec, the Coder's implementation evidence, and the resolved context with a
model run through the vendor-neutral
:class:`~sfp_agent_runtime.interfaces.AgentRuntime` seam, and returns a single
typed :class:`~sfp_contracts.agents.reviewer.ReviewerOutput` — the review
verdict plus six holistic quality gates. The Reviewer never modifies code; it
only judges.

Grounded in:
- ID-022 — the Reviewer judges a PR against its PR-spec / test plan / acceptance
  criteria; its typed output drives the Orchestrator's merge/rework decision.
- ID-066 — every agent emits a strict JSON contract; unknown fields rejected
  (``extra='forbid'``).
- ID-059 — the default prompt is built by
  :class:`~sfp_agent_runtime.prompt_builder.PromptBuilder` against the
  module-level :data:`_DEFAULT_PROMPT_DIR` (``shared.md`` landed in SFP-68;
  ``reviewer.md`` and ``reviewer/review.md`` land here).

Fail-closed (ID-067): any failure mode of the model run raises a dedicated
:class:`ReviewerError`. There is no sensible "partial" verdict.

Structural precedent: mirrors the landed Planner (SFP-70) / Test Designer
(SFP-71) / Coder (SFP-72) — same runtime seam, same prompt-resolution contract,
same fail-closed posture, differing only in inputs (spec + coder output) and the
output contract.
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
from sfp_contracts.agents.reviewer import ReviewerOutput
from sfp_contracts.context.bindings import ResolvedContext

__all__ = ["ReviewerError", "review", "review_with_text"]

_AGENT = "reviewer"
_TASK = "review"

#: Directory holding the default prompt fragments, shared with the sibling
#: agents (``../prompts`` relative to this module).
_DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class ReviewerError(Exception):
    """Raised when the Reviewer model run fails or yields non-conformant output.

    Fail-closed sentinel (ID-067). Every failure mode of the model run surfaces
    as this exception carrying the cause.
    """


def _run_model_with_text(
    runtime: AgentRuntime, request: AgentRunRequest
) -> tuple[ReviewerOutput, str | None]:
    """Run the Reviewer model; return ``(validated output, final_text)``.

    Fail-closed (ID-067) exactly as the original seam; the only addition is
    capturing the run result's ``final_text`` (SFP-249) — ``None`` when the
    runtime supplied none (e.g. a fake or non-Claude runtime predating the
    field).
    """
    try:
        result: AgentRunResult = runtime.run(request)
    except Exception as exc:  # noqa: BLE001 - fail-closed: catch broadly (ID-067)
        raise ReviewerError(f"Reviewer model run raised: {type(exc).__name__}: {exc}") from exc

    if not result.success:
        err = result.error if result.error is not None else "unknown"
        raise ReviewerError(f"Reviewer model run failed: {err}")

    if result.output is None:
        raise ReviewerError("Reviewer model returned no output")

    try:
        output = ReviewerOutput.model_validate(result.output)
    except ValidationError as exc:
        raise ReviewerError(f"Reviewer model output invalid: {exc}") from exc
    return output, result.final_text


def review(
    pr_spec: PrSpec,
    coder_output: CoderOutput,
    resolved: ResolvedContext,
    *,
    runtime: AgentRuntime,
    prompt_provider: PromptProvider | None = None,
    ticket_id: str,
) -> ReviewerOutput:
    """Judge a PR-spec's implementation and return the review verdict.

    Pipeline:
    1. Resolve the Reviewer prompt: from ``prompt_provider`` if given, else via
       :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR`.
    2. Build an opaque context mapping (the PR-spec, the Coder's evidence, the
       resolved context) and call ``runtime.run``.
    3. Validate the run output into a :class:`ReviewerOutput` (fail-closed).

    The Reviewer is judgment-only — it never modifies code. The ``ticket_id``
    argument is ALWAYS echoed into the run request and never taken from the model
    output.

    Args:
        pr_spec: The PR-spec the implementation is reviewed against.
        coder_output: The Coder's implementation evidence (branch/PR/files).
        resolved: The ticket's resolved context.
        runtime: The vendor-neutral agent runtime used to run the model.
        prompt_provider: Optional prompt provider. If ``None``, the default
            :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR` is used.
        ticket_id: The ticket identifier — always echoed into the run request.

    Returns:
        The validated :class:`ReviewerOutput` (verdict + quality gates).

    Raises:
        ReviewerError: On any failure mode of the model run (ID-067).
    """
    output, _final_text = review_with_text(
        pr_spec,
        coder_output,
        resolved,
        runtime=runtime,
        prompt_provider=prompt_provider,
        ticket_id=ticket_id,
    )
    return output


def review_with_text(
    pr_spec: PrSpec,
    coder_output: CoderOutput,
    resolved: ResolvedContext,
    *,
    runtime: AgentRuntime,
    prompt_provider: PromptProvider | None = None,
    ticket_id: str,
) -> tuple[ReviewerOutput, str | None]:
    """Judge a PR-spec and return ``(verdict, final_text)`` (SFP-249).

    Identical seam, inputs, and fail-closed semantics as :func:`review`; the
    only addition is that the run result's ``final_text`` (the reviewer's final
    textual message, transported by
    :attr:`~sfp_agent_runtime.interfaces.AgentRunResult.final_text`) is captured
    and returned alongside the verdict. The verdict remains the ONLY decision
    field — the text is transport for the GitHub review body surface (ID-021:
    contracts carry structured judgments only; rationale lives on GitHub, never
    in ``ReviewerOutput``). ``final_text`` is ``None`` when the runtime
    captured no final text.

    Returns:
        ``(ReviewerOutput, final_text)`` — the validated verdict and the
        reviewer's final text (``None`` when absent).

    Raises:
        ReviewerError: On any failure mode of the model run (ID-067).
    """
    if prompt_provider is not None:
        prompt = prompt_provider.get_prompt(_AGENT, _TASK)
    else:
        prompt = PromptBuilder(_DEFAULT_PROMPT_DIR).get_prompt(_AGENT, _TASK)

    context: Mapping[str, Any] = {
        "ticket_id": ticket_id,
        "pr_spec": pr_spec.model_dump(),
        "coder_output": coder_output.model_dump(),
        "resolved": resolved.model_dump(),
    }
    request = AgentRunRequest(
        agent=_AGENT,
        ticket_id=ticket_id,
        prompt=prompt,
        context=context,
    )

    output, final_text = _run_model_with_text(runtime, request)
    return output, final_text
