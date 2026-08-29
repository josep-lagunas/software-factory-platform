"""The local-first execution dispatcher (SFP-146).

A concrete :class:`~orchestrator.domain.scheduler.ExecutionDispatcher` that
satisfies the domain Protocol structurally (duck typing, no inheritance — the
SFP-42 in-memory :class:`~sfp_messaging.bus.MessageBus` precedent). It is the
local execution stand-in: it accepts every admitted command and mints a
deterministic, sequence-based ``external_id``.

Determinism (MAS §12.7 / AP-011): the id comes from an ``itertools.count``
sequence owned by the instance — no clock, no randomness, no environment read,
no I/O. Identical dispatch sequences on identically-constructed dispatchers
always yield identical ids; the counter is instance-scoped, so two dispatchers
never interleave into one another's sequences. Constructing a dispatcher is
side-effect-free; only :meth:`~orchestrator.domain.scheduler.ExecutionDispatcher.dispatch`
advances the counter.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import count

from sfp_contracts.commands import CommandEnvelope

from orchestrator.domain.scheduler import DispatchReceipt

#: The default local capacity source: a constant. Execution capacity is
#: admission policy (:class:`~orchestrator.domain.scheduler.AdmissionScheduler`),
#: not dispatch policy, so the dispatcher's default source is a fixed value —
#: a dispatcher that wants a live ceiling injects its own callable.
LOCAL_EXECUTION_CAPACITY = 1


def _local_constant_capacity() -> int:
    """The default :attr:`LOCAL_EXECUTION_CAPACITY` (constant, pure)."""
    return LOCAL_EXECUTION_CAPACITY


class LocalExecutionDispatcher:
    """The local execution dispatcher — accepts everything, deterministic ids.

    The stand-in behind :meth:`AdmissionScheduler.dispatch_admitted
    <orchestrator.domain.scheduler.AdmissionScheduler.dispatch_admitted>` until
    the AWS Batch body lands (doc-132..134 / SFP-149..151). Every dispatched
    command is accepted; the ``external_id`` is ``local-<n:06d>`` from the
    instance's own monotonically increasing sequence, so repeated dispatches
    yield strictly increasing ids with no clock or randomness.

    Attributes:
        capacity_source: Callable returning the current execution-capacity
            ceiling (SFP-76 semantics — the AWS twin reads a live
            ``max-vCpus``). Defaults to the local constant; nothing in the
            local path calls it, but it is part of the constructor contract so
            the AWS stub and the local stand-in stay interchangeable.
    """

    def __init__(self, capacity_source: Callable[[], int] | None = None) -> None:
        """Bind the capacity source and start the id sequence at 1.

        Pure: no I/O, no clock, no environment read. The default capacity
        source is the local constant, evaluated lazily by callers.
        """
        self.capacity_source: Callable[[], int] = (
            capacity_source if capacity_source is not None else _local_constant_capacity
        )
        self._sequence = count(start=1)

    async def dispatch(self, command: CommandEnvelope) -> DispatchReceipt:
        """Accept the command; return its receipt with the next sequence id.

        Never raises and never rejects: admission already decided this command
        may execute, so the local stand-in's job is only to mint a stable,
        deterministic handle for later observation.
        """
        external_id = f"local-{next(self._sequence):06d}"
        return DispatchReceipt(accepted=True, external_id=external_id)
