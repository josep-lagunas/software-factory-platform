"""Orchestrator execution dispatch adapters (SFP-146).

Concrete implementations of the domain seam
:class:`~orchestrator.domain.scheduler.ExecutionDispatcher`. The Protocol and
its :class:`~orchestrator.domain.scheduler.DispatchReceipt` live in the domain
(SFP-145's module); this package owns the *vendor* side:

- :mod:`~orchestrator.infrastructure.dispatch.local` —
  :class:`LocalExecutionDispatcher`: the local-first stand-in (SFP-42
  in-memory MessageBus precedent). Accepts everything, returns a deterministic
  sequence-based ``external_id`` (no clock, no randomness — AP-011).
- :mod:`~orchestrator.infrastructure.dispatch.aws_batch` —
  :class:`BatchExecutionDispatcher`: the AWS Batch implementation contract,
  fixed as a raising stub until hosting provisions infra (doc-132..134 /
  SFP-149..151). Its body is deferred by design, not blocked.

Only the domain Protocol is re-exported here; adapters satisfy it structurally
(duck typing, no inheritance — SFP-42 precedent).

The two witness assignments below are the *static* half of that claim:
``runtime_checkable`` isinstance in tests proves the shape at runtime, but
nothing verifies it under mypy until a concrete dispatcher is assigned to the
Protocol type in checked code. These witnesses are that check — the AWS stub
cannot drift from the seam's signature without failing mypy here (the
"contract pinned by the stub" risk, PRSpec SFP-146).
"""

from orchestrator.domain.scheduler import DispatchReceipt, ExecutionDispatcher
from orchestrator.infrastructure.dispatch.aws_batch import BatchExecutionDispatcher
from orchestrator.infrastructure.dispatch.local import (
    LOCAL_EXECUTION_CAPACITY,
    LocalExecutionDispatcher,
)

#: mypy conformance witnesses. Both constructors are pure (no clock, no
#: environment read, no I/O — MAS §12.7), so module-level construction is
#: side-effect-free; the instances are private and never dispatched.
_LOCAL_DISPATCHER_WITNESS: ExecutionDispatcher = LocalExecutionDispatcher()
_BATCH_DISPATCHER_WITNESS: ExecutionDispatcher = BatchExecutionDispatcher()

__all__ = [
    "LOCAL_EXECUTION_CAPACITY",
    "BatchExecutionDispatcher",
    "DispatchReceipt",
    "ExecutionDispatcher",
    "LocalExecutionDispatcher",
]
