"""The :class:`PlannerHost` — orchestrator-side hosting glue for the Planner agent.

Grounded in:
- MAS §9.6 — the Local Execution Engine runs agents through the runtime seam;
  the Orchestrator hosts the Planner, it does not *be* the Planner.
- ID-072 — the Orchestrator invokes the Planner (it decides *that* planning
  happens); the planning itself is the agent's.
- ID-021 — the Planner emits a deterministic JSON ``planner-output`` contract
  (``pr_specs[]``); the host validates into that contract, it never authors
  one.
- ID-066 — every agent emits a strict JSON contract; unknown fields are
  rejected (``extra='forbid'`` on :class:`~sfp_contracts.agents.planner.PlannerOutput`).
- ID-067 — every PR-spec carries ``validation_profile`` plus
  ``validation_profile_reason``; an output without them is not a valid plan.
- SFP-14 — the typed payload the host validates into.
- SFP-53 — the Planner prompt lives in manual-core, already landed; this host
  consumes resolved prompt *text*, it never constructs a prompt.
- SFP-147 — the aggregate manager that persists specs is the caller's; the
  host receives persistence as an injected callable and owns no storage.
- SFP-150 — the implementation ticket (this module).

Scope discipline (the PRSpec's ``out_of_scope``, verbatim intent): no planner
prompt design, no PRSpec linting (SFP-193 lives in contracts/linters), no
storage implementation, no splitting into multiple coded PRs, no chaining to
the SFP-149 readiness host, and no planning intelligence of any kind. The host
is pure glue over three injected seams:

- an :class:`~sfp_agent_runtime.interfaces.AgentRuntime` to *run* the agent;
- a request-builder callable turning the ticket context into the
  :class:`~sfp_agent_runtime.interfaces.AgentRunRequest` carrying the resolved
  planner prompt;
- a persistence callable ``persist_specs(ticket_id, output)``.

Behavioral contract of :meth:`PlannerHost.run_for_ticket`:

1. Build the :class:`~sfp_agent_runtime.interfaces.AgentRunRequest` from the
   ticket via the injected builder (the host adds nothing to it).
2. Execute through the runtime; a failed run (``success=False``) raises
   :class:`PlannerOutputInvalid` — there is no output to repair.
3. Pydantic-validate the raw result into
   :class:`~sfp_contracts.agents.planner.PlannerOutput`; any
   :class:`pydantic.ValidationError` is re-raised chained as
   :class:`PlannerOutputInvalid`. **Never repair, never invent** (MAS §12.9
   discipline: an underspecified/invalid output is surfaced, not improvised
   around).
4. Enforce the ID-067 post-conditions explicitly after validation: at least
   one ``pr_spec`` (an empty plan is invalid), and every ``pr_spec`` carries
   both ``validation_profile`` and ``validation_profile_reason``. These checks
   are defense-in-depth: pydantic already marks the fields required, but the
   acceptance criteria name them as an enforced contract, so the host asserts
   them rather than assuming the schema's shape forever.
5. On success: ``await persist_specs(ticket_id, output)`` **before** returning
   the output — persist-then-return is an ordering contract, not a courtesy.
   Persistence exceptions propagate uncaught; a failed persist means the
   output is *not* returned as persisted.

Purity/determinism: no clock, no randomness, no I/O beyond the injected
callables, no storage, no prompt construction, no linting. The host is
deterministic given its injected seams (MAS §12.7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError
from sfp_contracts.agents.planner import PlannerOutput

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRuntime


class PlannerOutputInvalid(Exception):
    """The Planner's raw output is not a valid :class:`PlannerOutput`.

    Raised on any invalid output — a failed run, malformed JSON shape
    (:class:`pydantic.ValidationError`), an empty plan (zero ``pr_specs``), or
    a PR-spec missing its ID-067 ``validation_profile`` /
    ``validation_profile_reason`` pair. The host never repairs and never
    invents spec content; an invalid output is surfaced, not normalized
    (MAS §12.9).
    """


class PlannerHost:
    """Runs the Planner agent through the runtime and validates its output.

    Pure hosting glue (MAS §9.6 / ID-072): the host owns no planning logic,
    no prompt construction (SFP-53), no storage (SFP-147), and no linting
    (SFP-193). Every side-effecting concern is an injected seam.

    Args:
        runtime: The vendor-neutral agent-runtime seam (AP-010) the Planner
            runs through.
        build_request: Builds the :class:`~sfp_agent_runtime.interfaces.AgentRunRequest`
            (resolved planner prompt + context) from the ticket context the
            caller hands over. The host adds nothing to the request.
        persist_specs: Async callable invoked as
            ``persist_specs(ticket_id, output)`` on success, *before* the
            output is returned. The SFP-147 aggregate manager is the
            caller's; the host owns no storage.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        build_request: Callable[[str], AgentRunRequest],
        persist_specs: Callable[[str, PlannerOutput], Awaitable[None]],
    ) -> None:
        self._runtime = runtime
        self._build_request = build_request
        self._persist_specs = persist_specs

    async def run_for_ticket(self, ticket_id: str) -> PlannerOutput:
        """Run the Planner for ``ticket_id`` and return the persisted output.

        Builds the run request via the injected builder, executes it through
        the runtime, validates the raw result into
        :class:`~sfp_contracts.agents.planner.PlannerOutput`, enforces the
        ID-067 post-conditions, then persists via the injected callable
        before returning. Any invalid output raises
        :class:`PlannerOutputInvalid` and nothing is persisted; persistence
        failures propagate uncaught (the output is not returned as persisted).
        """
        request = self._build_request(ticket_id)
        result = self._runtime.run(request)

        if not result.success:
            raise PlannerOutputInvalid(
                f"planner run for ticket {ticket_id!r} failed: {result.error!r}"
            )

        try:
            output = PlannerOutput.model_validate(result.output)
        except ValidationError as exc:
            raise PlannerOutputInvalid(
                f"planner output for ticket {ticket_id!r} failed validation"
            ) from exc

        # ID-067 post-conditions, enforced explicitly (defense-in-depth:
        # pydantic marks these required, but the contract is named in the
        # acceptance criteria — the host asserts it, it does not assume it).
        # Unreachable through the pydantic layer as it stands today —
        # ``min_length=1`` and the required/non-optional field annotations
        # reject these rows during model_validate — hence ``no cover``; kept
        # per the PRSpec's explicit-checks instruction.
        if not output.pr_specs:  # pragma: no cover
            raise PlannerOutputInvalid(
                f"planner output for ticket {ticket_id!r} has zero pr_specs"
                " (an empty plan is invalid)"
            )
        for spec in output.pr_specs:
            if (  # pragma: no cover
                spec.validation_profile is None or spec.validation_profile_reason is None
            ):
                raise PlannerOutputInvalid(
                    f"pr_spec {spec.id!r} in ticket {ticket_id!r} is missing its"
                    " ID-067 validation_profile / validation_profile_reason pair"
                )

        # Persist-then-return: ordering is the contract, not a courtesy.
        await self._persist_specs(ticket_id, output)
        return output
