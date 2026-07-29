"""The Test Designer agent (SFP-71; DOC SFP-54; ID-066 / ID-022).

This module is the Test Designer evaluator. It composes a parsed ready ticket
and its resolved context with a model run produced through the vendor-neutral
:class:`~sfp_agent_runtime.interfaces.AgentRuntime` seam, and returns a single
typed :class:`~sfp_contracts.agents.test_designer.TestDesignerOutput` — a
deterministic test plan (seven ``list[str]`` buckets of *what to test*) for the
PR-spec under design.

Grounded in:
- ID-066 — the Test Designer returns a structured test plan as its deterministic
  output; every agent emits a strict JSON contract, unknown fields rejected
  (``extra='forbid'``).
- ID-022 — the Test Designer's output drives the Coder's test writing
  (judgments + references, not artifacts).
- ID-059 — no prompt text is inlined in source; the default prompt is built by
  the landed :class:`~sfp_agent_runtime.prompt_builder.PromptBuilder` against
  the module-level :data:`_DEFAULT_PROMPT_DIR`, shared with the Planner
  (``shared.md`` landed in SFP-68; ``test_designer.md`` and
  ``test_designer/design.md`` land here).

Fail-closed (ID-067): any failure mode of the model run raises a dedicated
:class:`TestDesignerError` carrying the cause. There is no sensible "partial"
test plan, so failure surfaces as an exception rather than a degenerate
:class:`TestDesignerOutput`.

Structural precedent: this module mirrors the landed Planner
(:mod:`workspace_worker.agents.planner`, SFP-70) and the readiness gate
(SFP-68) — same runtime seam, same prompt-resolution contract, same fail-closed
posture, differing only in the output contract.
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
from sfp_contracts.agents.readiness import ParsedTicket
from sfp_contracts.agents.test_designer import TestDesignerOutput
from sfp_contracts.context.bindings import ResolvedContext

__all__ = ["TestDesignerError", "design_tests"]

#: The agent role and task names used to resolve the Test Designer prompt. These
#: select ``prompts/test_designer.md`` and ``prompts/test_designer/design.md``
#: via the :class:`PromptBuilder` fragment layout (shared -> role -> task; ID-059).
_AGENT = "test_designer"
_TASK = "design"

#: Directory holding the default prompt fragments. Shared with the Planner
#: (``../prompts`` relative to this module). Exposed as a module attribute so
#: tests may redirect it to a temp dir without seeding real files.
_DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class TestDesignerError(Exception):
    """Raised when the Test Designer model run fails or yields non-conformant output.

    Fail-closed sentinel (ID-067). Every failure mode of the model run — the run
    raised, returned ``success=False``, returned a ``None`` output, or produced
    output that fails
    :meth:`~sfp_contracts.agents.test_designer.TestDesignerOutput.model_validate`
    — surfaces as this exception carrying the cause.
    """

    # pytest must not collect this ``Test*``-named class as a test (mirrors the
    # contract's own TestPlan/TestDesignerOutput ``__test__ = False``).
    __test__ = False


def _run_model(runtime: AgentRuntime, request: AgentRunRequest) -> TestDesignerOutput:
    """Run the Test Designer model and validate its output (fail-closed, ID-067).

    Raises:
        TestDesignerError: On any failure mode of the model run.
    """
    try:
        result: AgentRunResult = runtime.run(request)
    except Exception as exc:  # noqa: BLE001 - fail-closed: catch broadly (ID-067)
        raise TestDesignerError(
            f"Test Designer model run raised: {type(exc).__name__}: {exc}"
        ) from exc

    if not result.success:
        err = result.error if result.error is not None else "unknown"
        raise TestDesignerError(f"Test Designer model run failed: {err}")

    if result.output is None:
        raise TestDesignerError("Test Designer model returned no output")

    try:
        return TestDesignerOutput.model_validate(result.output)
    except ValidationError as exc:
        raise TestDesignerError(f"Test Designer model output invalid: {exc}") from exc


def design_tests(
    ticket: ParsedTicket,
    resolved: ResolvedContext,
    *,
    runtime: AgentRuntime,
    prompt_provider: PromptProvider | None = None,
    ticket_id: str,
) -> TestDesignerOutput:
    """Design the test plan for a ready ticket's PR-spec.

    Pipeline:
    1. Resolve the Test Designer prompt: from ``prompt_provider`` if given, else
       via :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR`.
    2. Build an opaque context mapping and call ``runtime.run``.
    3. Validate the run output into a :class:`TestDesignerOutput` (fail-closed on
       any failure mode — :class:`TestDesignerError`).

    The ``ticket_id`` argument is ALWAYS echoed into the run request and never
    taken from the model output.

    Args:
        ticket: The parsed ready ticket whose PR-spec is being designed for.
        resolved: The ticket's resolved context.
        runtime: The vendor-neutral agent runtime used to run the model.
        prompt_provider: Optional prompt provider. If ``None``, the default
            :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR` is used.
        ticket_id: The ticket identifier — always echoed into the run request.

    Returns:
        The validated :class:`TestDesignerOutput` (the test plan).

    Raises:
        TestDesignerError: On any failure mode of the model run (ID-067).
    """
    # (1) Resolve the Test Designer prompt (ID-059: prompts live on disk).
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
