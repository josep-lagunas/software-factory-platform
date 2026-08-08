"""The readiness gate model evaluator (SFP-68; ID-064 layer 2).

This module is the *model* half of the readiness gate (layer 2). It composes the
deterministic layer-1 rubric (:func:`evaluate_readiness_rubric`, SFP-67) with a
model evaluation produced through the vendor-neutral
:class:`~sfp_agent_runtime.interfaces.AgentRuntime` seam, and returns a single
combined :class:`~sfp_contracts.agents.readiness.ReadinessOutput`.

Grounded in:
- ID-064 (amended) — the Readiness evaluator is two layers: (1) a deterministic
  rubric that checks the ticket carries the mandatory ID-070 sections, and (2) a
  model evaluator that surfaces semantic gaps. This module is layer 2 and
  composes both layers into one ``ReadinessOutput``.
- ID-065 — the combined ``verdict`` drives routing (``READY`` proceeds,
  ``NEEDS_CLARIFICATION`` routes back, ``MANUAL_REQUIRED`` escalates).
- ID-067 — fail-closed: any failure mode of the model run appends a descriptive
  message to ``blocking_ambiguities`` and yields ``NEEDS_CLARIFICATION``; the
  gate never returns ``READY`` on a failure path. The layer-1 rubric still runs
  and contributes its own results on every path.
- ID-059 — no prompt text is inlined in source; the default prompt is built by
  the landed :class:`~sfp_agent_runtime.prompt_builder.PromptBuilder` against the
  module-level :data:`_DEFAULT_PROMPT_DIR`, which points at the ``prompts/`` dir
  shipped with this ticket.

Design choices (binding combination rule — all ambiguities pre-resolved):
- The layer-1 rubric is authoritative and always runs. Its ``rubric_results`` are
  passed through UNCHANGED (the model cannot influence them) and its
  missing-section messages always lead ``blocking_ambiguities``.
- The model's non-MANUAL verdict is NOT trusted on its face — only its evidence
  (``ReadinessOutput.blocking_ambiguities`` — the "model gaps") feeds the union
  of both ``blocking_ambiguities`` and ``missing_inputs``. Only
  ``MANUAL_REQUIRED`` is honored from the model's verdict field, and it has
  precedence over a failed rubric section.
- ``ticket_id`` is ALWAYS the gate argument, never the model's.
- The model's own ``missing_inputs`` field is NOT separately unioned — only its
  ``blocking_ambiguities`` count as model gaps.
- Fail-closed is symmetric across all four failure modes (run returned
  ``success=False``; ``output`` is ``None``; ``model_validate`` raises; the run
  itself raised).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sfp_agent_runtime.interfaces import (
    AgentRunRequest,
    AgentRuntime,
    PromptProvider,
)
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.agents.readiness import (
    ParsedTicket,
    ReadinessOutput,
    ReadinessVerdict,
)
from sfp_contracts.context.bindings import ResolvedContext

from workspace_worker.workflow.readiness_rubric import evaluate_readiness_rubric

__all__ = ["evaluate_readiness"]

#: The agent role and task names used to resolve the readiness prompt. These
#: select ``prompts/readiness.md`` and ``prompts/readiness/evaluate.md`` via the
#: :class:`PromptBuilder` fragment layout (shared -> role -> task; ID-059).
_AGENT = "readiness"
_TASK = "evaluate"

#: Directory holding the default readiness prompt fragments, colocated with this
#: package (``prompts/``). Exposed as a module attribute so tests may redirect it
#: to a temp dir without seeding real files.
_DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _dedup_preserve_order(items: list[str]) -> list[str]:
    """Return ``items`` with duplicates removed, preserving first-seen order.

    Used to union lists deterministically (combination rule): layer-1 / resolver
    items come first, model gaps next, so a duplicate model gap collapses into
    its earlier (deterministic) position.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _run_model(
    runtime: AgentRuntime, request: AgentRunRequest
) -> tuple[ReadinessOutput | None, str | None]:
    """Run the readiness model and validate its output (fail-closed, ID-067).

    Returns a ``(model_output, fail_message)`` pair. On success, ``model_output``
    is the validated layer-2 :class:`ReadinessOutput` and ``fail_message`` is
    ``None``. On any of the four failure modes, ``model_output`` is ``None`` and
    ``fail_message`` is the descriptive fail-closed string to be appended to
    ``blocking_ambiguities``.
    """
    try:
        result = runtime.run(request)
    except Exception as exc:  # noqa: BLE001 - fail-closed: catch broadly (ID-067)
        return None, f"Readiness model run raised: {type(exc).__name__}: {exc}"

    if not result.success:
        err = result.error if result.error is not None else "unknown"
        return None, f"Readiness model run failed: {err}"

    if result.output is None:
        return None, "Readiness model returned no output"

    try:
        model_output = ReadinessOutput.model_validate(result.output)
    except ValidationError as exc:
        return None, f"Readiness model output invalid: {exc}"

    return model_output, None


def evaluate_readiness(
    ticket: ParsedTicket,
    resolved: ResolvedContext,
    *,
    runtime: AgentRuntime,
    prompt_provider: PromptProvider | None = None,
    ticket_id: str,
    at_frontier: bool = False,
) -> ReadinessOutput:
    """Evaluate a ticket's readiness, composing the rubric (layer 1) with the
    model evaluator (layer 2).

    Pipeline:
    1. Run the deterministic layer-1 rubric
       (:func:`evaluate_readiness_rubric`) — authoritative, always runs.
    2. Resolve the readiness prompt: from ``prompt_provider`` if given, else via
       :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR`.
    3. Build an opaque context mapping and call ``runtime.run``.
    4. Validate the run output into a layer-2 :class:`ReadinessOutput`
       (fail-closed on any failure mode).
    5. Combine per the binding combination rule.

    Combination rule (binding):
    - ``rubric_results`` <- layer-1 pass-through ONLY (model cannot influence).
    - ``blocking_ambiguities`` <- layer-1 messages + model gaps, deduped in
      deterministic order, with the fail-closed message appended last.
    - ``missing_inputs`` <- ``resolved.missing`` + model gaps, deduped.
    - ``ticket_id`` <- always the gate ``ticket_id`` argument.
    - ``verdict`` <- ``MANUAL_REQUIRED`` iff the model output's verdict is
      ``MANUAL_REQUIRED`` (precedence over a failed rubric section); else
      ``NEEDS_CLARIFICATION`` if any blocking ambiguity OR missing input OR
      failed rubric section; else ``READY``.

    Fail-closed (ID-067): any model failure appends a descriptive message to
    ``blocking_ambiguities`` and the verdict is ``NEEDS_CLARIFICATION`` (never
    ``READY``, and never ``MANUAL_REQUIRED`` since there is no model output to
    honor). The layer-1 rubric still runs and contributes its own results.

    Args:
        ticket: The parsed ticket to assess.
        resolved: The ticket's resolved context (its ``missing`` list feeds
            ``missing_inputs``).
        runtime: The vendor-neutral agent runtime used to run the model.
        prompt_provider: Optional prompt provider. If ``None``, the default
            :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR` is used.
        ticket_id: The ticket identifier — always echoed into the result and
            never taken from the model output.
        at_frontier: Whether the ticket sits at the human/automatic frontier
            (SFP-232). Forwarded to the layer-1 rubric, where the two *boundary*
            ID-070 sections are required as presence only iff this is ``True``.
            Computed deterministically by
            :func:`workspace_worker.workflow.frontier.compute_at_frontier`.
            Defaults to ``False`` (off-frontier).

    Returns:
        The combined :class:`ReadinessOutput`.
    """
    # (1) Layer-1 rubric — authoritative, always runs (even on a model failure).
    rubric = evaluate_readiness_rubric(ticket, ticket_id=ticket_id, at_frontier=at_frontier)
    rubric_failed = any(not passed for passed in rubric.rubric_results.values())

    # (2) Resolve the readiness prompt (ID-059: prompts live on disk, not here).
    if prompt_provider is not None:
        prompt = prompt_provider.get_prompt(_AGENT, _TASK)
    else:
        prompt = PromptBuilder(_DEFAULT_PROMPT_DIR).get_prompt(_AGENT, _TASK)

    # (3) Build the opaque context mapping and call the model.
    context: Mapping[str, Any] = {
        "ticket_id": ticket_id,
        "ticket": ticket.model_dump(),
        "resolved": resolved.model_dump(),
        "rubric_results": dict(rubric.rubric_results),
    }
    request = AgentRunRequest(
        agent=_AGENT,
        ticket_id=ticket_id,
        prompt=prompt,
        context=context,
    )
    model_output, fail_message = _run_model(runtime, request)

    # (4) Combine. Model gaps == the layer-2 ReadinessOutput.blocking_ambiguities.
    # The model's own missing_inputs field is NOT unioned (binding rule).
    model_gaps: list[str] = (
        list(model_output.blocking_ambiguities) if model_output is not None else []
    )

    blocking = _dedup_preserve_order(list(rubric.blocking_ambiguities) + model_gaps)
    missing = _dedup_preserve_order(list(resolved.missing) + model_gaps)
    if fail_message is not None:
        blocking.append(fail_message)

    # (5) Verdict — MANUAL iff the model says MANUAL (precedence); else
    # NEEDS_CLARIFICATION if anything blocks or the rubric failed; else READY.
    if model_output is not None and model_output.verdict is ReadinessVerdict.MANUAL_REQUIRED:
        verdict = ReadinessVerdict.MANUAL_REQUIRED
    elif blocking or missing or rubric_failed:
        verdict = ReadinessVerdict.NEEDS_CLARIFICATION
    else:
        verdict = ReadinessVerdict.READY

    return ReadinessOutput(
        ticket_id=ticket_id,
        verdict=verdict,
        blocking_ambiguities=blocking,
        missing_inputs=missing,
        rubric_results=rubric.rubric_results,
    )
