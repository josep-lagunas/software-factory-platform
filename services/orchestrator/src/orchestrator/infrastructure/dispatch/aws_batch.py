"""The AWS Batch execution dispatcher — contract stub (SFP-146).

Fixes the AWS Batch implementation *contract* behind the domain seam
:class:`~orchestrator.domain.scheduler.ExecutionDispatcher` as a raising stub.
The body lands with hosting (doc-132..134 / SFP-149..151) — deferred **by
design**, declared in the PRSpec, not blocked: there is no AWS account or
Batch queue to talk to yet.

Contract this stub pins (SFP-76 queue / ``max-vCpus`` semantics):

- The constructor takes a ``capacity_source`` callable returning the current
  execution-capacity ceiling — in AWS mode this reads the SFP-76 job queue's
  ``max-vCpus`` from the environment, **never** at import time (no config is
  read here today; the future AWS mode reads the env inside the callable).
- :meth:`BatchExecutionDispatcher.dispatch` has the Protocol-identical async
  signature and submits the command to the SFP-76 queue, returning a
  :class:`~orchestrator.domain.scheduler.DispatchReceipt` carrying the Batch
  job id as ``external_id``.
- No boto3 import, no network egress (ID-060 sandbox), no config reads at
  import time — nothing beyond this documented stub until hosting provisions
  the infrastructure.

The class already type-checks against the Protocol under mypy (asserted in
tests), so the future body cannot drift from the contract's signature.
"""

from __future__ import annotations

from collections.abc import Callable

from sfp_contracts.commands import CommandEnvelope

from orchestrator.domain.scheduler import DispatchReceipt


class BatchExecutionDispatcher:
    """AWS Batch dispatcher — interface only; ``dispatch`` raises (SFP-146).

    Satisfies :class:`~orchestrator.domain.scheduler.ExecutionDispatcher`
    structurally (no inheritance — SFP-42 precedent). The body is deferred to
    hosting doc-132..134 / SFP-149..151; until then every :meth:`dispatch`
    call raises :class:`NotImplementedError` so an accidental early wiring
    fails loudly instead of silently no-op'ing (fail-closed).
    """

    def __init__(self, capacity_source: Callable[[], int] | None = None) -> None:
        """Bind the capacity source; perform no AWS interaction.

        ``capacity_source`` returns the SFP-76 job queue's effective execution
        ceiling (``max-vCpus``) when the AWS body lands; today it is stored
        and never read — the future AWS mode resolves it lazily from the
        environment inside the callable, never at import or construction time
        (determinism, MAS §12.7 / ID-060 sandbox).
        """
        self.capacity_source: Callable[[], int] | None = capacity_source

    async def dispatch(self, command: CommandEnvelope) -> DispatchReceipt:
        """Raise: the AWS Batch body is deferred to hosting (doc-132..134)."""
        msg = (
            "BatchExecutionDispatcher.dispatch is not implemented yet: the AWS "
            "Batch body lands with hosting (doc-132..134 / SFP-149..151); use "
            "LocalExecutionDispatcher until then (SFP-146)."
        )
        raise NotImplementedError(msg)
