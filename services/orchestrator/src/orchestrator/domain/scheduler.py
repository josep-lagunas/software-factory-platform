"""Deterministic execution admission (ID-061 / MAS §11.8; SFP-145).

The :class:`AdmissionScheduler` is the domain-level replacement for the manual
"N=1 serialization" discipline: execution-bound commands are gated against a
capacity limit with FIFO queuing, while communication and cancellation
commands bypass immediately. It decides **when** an execution command may
emit — it never decides workflow state (ID-061: the scheduler is not a
transition source).

Purity (AP-011): the module reads no clock, performs no I/O, and touches no
bus. Queue order is arrival order by an internal monotonically increasing
sequence counter, so identical submission sequences always yield identical
decisions.

The command partition (execution / communication / cancellation) is derived
from the authoritative ``sfp_contracts.commands`` catalogue (ID-031 / SFP-219)
and asserted to cover all 8 :class:`~sfp_contracts.commands.CommandType`
members at import time, so a future 9th command fails loudly here rather than
being silently misrouted to "unknown".

Out of scope here: binding capacity to an AWS Batch ``max-vCpus`` ceiling (the
:class:`ExecutionDispatcher` implementations under
``orchestrator/infrastructure/dispatch/`` own that, SFP-146); out of scope
(SFP-152..157): wiring admission checks into the emitters.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from sfp_contracts.commands import CommandEnvelope, CommandType

#: Execution-bound commands: consume a capacity slot for their duration.
#: They are the commands the ID-061 manual N=1 discipline serialized.
EXECUTION_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.EXECUTE_CODING_JOB.value,
        CommandType.REVIEW_PULL_REQUEST.value,
        CommandType.SYNCHRONIZE_PULL_REQUEST.value,
        CommandType.REQUEST_MERGE.value,
    }
)

#: Communication commands: never serialized — they ride outside capacity
#: (MAS §11.8) because they unblock a human, not a worker.
COMMUNICATION_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.REQUEST_USER_INPUT.value,
        CommandType.NOTIFY_USER.value,
    }
)

#: Cancellation commands: bypass immediately. A cancel RELEASES capacity —
#: never consumes it: it is a control signal, not work (MAS §11.8).
CANCELLATION_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.CANCEL_CODING_JOB.value,
        CommandType.CANCEL_REVIEW_JOB.value,
    }
)


def _assert_partition_covers_catalogue() -> None:
    """Fail loudly if the partition misses a :class:`CommandType` member.

    The three partitions plus each other must cover exactly the 8 catalogue
    members (ID-031). A future 9th command that is not classified here must
    break this import, not silently route to the fail-closed "unknown" branch
    of :meth:`AdmissionScheduler.submit` — where it would be *never admitted*
    with no signal.
    """
    classified = EXECUTION_COMMANDS | COMMUNICATION_COMMANDS | CANCELLATION_COMMANDS
    catalogue = {member.value for member in CommandType}
    if classified != catalogue:
        unclassified = sorted(catalogue - classified)
        invented = sorted(classified - catalogue)
        msg = (
            "Admission command partition is out of sync with "
            f"sfp_contracts.commands.CommandType (ID-031): "
            f"unclassified members {unclassified}, non-catalogue members {invented}"
        )
        raise RuntimeError(msg)


_assert_partition_covers_catalogue()


class AdmissionDecision(BaseModel):
    """A single recorded admission verdict (SFP-145).

    Frozen and ``extra='forbid'`` per the immutable-history rule (MAS §8.12):
    a decision is history, never mutated after the fact. ``queue_position``
    is the 1-based position assigned to a queued execution command, ``None``
    for every decision that is not a queuing event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    admitted: bool
    queue_position: int | None = None
    reason: str


@dataclass(slots=True)
class _QueuedCommand:
    """A queued execution submission awaiting a capacity slot.

    Carries the arrival sequence that fixes FIFO order (AP-011: the sequence
    counter, not a wall-clock read, is the ordering key).
    """

    command_type: str
    sequence: int


@dataclass
class AdmissionScheduler:
    """Gates execution-bound commands against capacity with FIFO queuing.

    Deterministic (AP-011) and pure: decisions depend only on the order of
    ``submit`` / ``on_complete`` calls and the fixed capacity — never on time,
    randomness, or environment. Communication and cancellation commands
    bypass; execution commands are admitted while in-flight count is below
    capacity and FIFO-queued beyond it; unknown command types are never
    admitted (fail-closed).
    """

    capacity: int
    _in_flight: int = field(default=0, init=False)
    _queue: deque[_QueuedCommand] = field(default_factory=deque, init=False)
    _next_sequence: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        """Fail closed on non-positive capacity.

        ``capacity=0`` would make every execution command queue forever —
        a silent deadlock — so it is a construction error, never a runtime
        behavior (fail-closed).
        """
        if self.capacity < 1:
            msg = f"capacity must be >= 1 (got {self.capacity}); capacity=0 would queue-all forever"
            raise ValueError(msg)

    def in_flight(self) -> int:
        """The current in-flight execution count (read-only observability)."""
        return self._in_flight

    def submit(self, command_type: str) -> AdmissionDecision:
        """Apply the admission decision table to one command submission.

        Decision table (reason strings are contract — asserted verbatim in
        tests):

        - communication → admitted, ``communication: immediate (MAS §11.8)``;
        - cancellation → admitted, ``cancellation: immediate (MAS §11.8 — a
          cancel RELEASES capacity, never consumes it; it is a control
          signal, not work)``;
        - execution, in-flight < capacity → admitted,
          ``execution: capacity available (<n>/<cap>)``;
        - execution, at capacity → queued with a 1-based ``queue_position``,
          ``execution: at capacity, queued``;
        - anything else → not admitted, ``unknown command type``.
        """
        if command_type in COMMUNICATION_COMMANDS:
            return AdmissionDecision(
                admitted=True,
                reason="communication: immediate (MAS §11.8)",
            )
        if command_type in CANCELLATION_COMMANDS:
            return AdmissionDecision(
                admitted=True,
                reason=(
                    "cancellation: immediate (MAS §11.8 — a cancel RELEASES capacity, "
                    "never consumes it; it is a control signal, not work)"
                ),
            )
        if command_type in EXECUTION_COMMANDS:
            if self._in_flight < self.capacity:
                self._in_flight += 1
                return AdmissionDecision(
                    admitted=True,
                    reason=(f"execution: capacity available ({self._in_flight}/{self.capacity})"),
                )
            position = len(self._queue) + 1
            self._queue.append(
                _QueuedCommand(command_type=command_type, sequence=self._next_sequence)
            )
            self._next_sequence += 1
            return AdmissionDecision(
                admitted=False,
                queue_position=position,
                reason="execution: at capacity, queued",
            )
        return AdmissionDecision(
            admitted=False,
            reason=f"unknown command type: {command_type}",
        )

    def on_complete(self, command_type: str) -> AdmissionDecision:
        """Record one execution completion and admit the FIFO head, if any.

        Releases a capacity slot for an execution command, then admits the
        longest-waiting queued submission — its ``queue_position`` is the
        1-based head position. With an empty queue this returns the recorded
        no-admission outcome. It never raises: an out-of-partition or unknown
        ``command_type`` still produces the empty-queue record (only an
        execution command ever holds capacity, so a non-execution completion
        releases nothing).

        Reasons are contract: ``execution: completed, admitting queued head
        (position <n>)`` and ``execution: completed, queue empty``.
        """
        if command_type in EXECUTION_COMMANDS:
            self._in_flight = max(0, self._in_flight - 1)
        if self._queue:
            self._queue.popleft()
            self._in_flight += 1
            # The head was assigned position 1 at queue time; each completed
            # admission promotes the next head to position 1 in turn.
            return AdmissionDecision(
                admitted=True,
                reason="execution: completed, admitting queued head (position 1)",
            )
        return AdmissionDecision(
            admitted=False,
            reason="execution: completed, queue empty",
        )

    async def dispatch_admitted(
        self,
        dispatcher: ExecutionDispatcher,
        envelope: CommandEnvelope,
    ) -> DispatchReceipt:
        """Gate one envelope through admission, then dispatch if admitted.

        The execution dispatch seam (SFP-146): submission is keyed by
        ``envelope.command_type``; a not-admitted submission returns the queued
        receipt **without awaiting** ``dispatcher.dispatch`` — the dispatcher is
        never invoked on the queued path (the spy-asserted guarantee). An
        admitted submission awaits ``dispatcher.dispatch(envelope)`` exactly
        once and returns its :class:`DispatchReceipt` unmodified.

        Pure with respect to workflow state (ID-061 / MAS §11.8): this method
        records admission bookkeeping and (at most) awaits the dispatcher; it
        never touches workflow state — the scheduler is not a transition
        source. The dispatcher owns vendor interaction; whether a dispatched
        command later advances workflow state is the consumer/observation
        side's concern (out of scope here).
        """
        decision = self.submit(envelope.command_type.value)
        if not decision.admitted:
            return DispatchReceipt(
                accepted=False,
                external_id=None,
                reason="not admitted: queued",
            )
        return await dispatcher.dispatch(envelope)


class DispatchReceipt(BaseModel):
    """The outcome of one execution dispatch attempt (SFP-146).

    An orchestrator-local twin of communication's
    :class:`~communication.interfaces.outbound.DeliveryReceipt` (SFP-133
    pattern reference only — deliberately NOT imported: cross-service imports
    are identifier-only analogs, not code dependencies, per the intra-service
    FK policy). Exactly the three fields below; unknown fields are rejected.
    Frozen like :class:`AdmissionDecision`: a receipt is history, never
    mutated after the fact (MAS §8.12).

    Attributes:
        accepted: Whether the dispatcher accepted the command for execution
            (or admission accepted it, on the dispatched path).
        external_id: The execution-provider-assigned identifier (Local:
            sequence id; future AWS Batch: the job id). ``None`` when nothing
            was dispatched (the queued path) or the provider assigned none.
        reason: A human-readable reason string — contract, asserted verbatim
            in tests (``"not admitted: queued"`` on the queued path; otherwise
            the dispatcher's own reason, empty for a plain local accept).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    external_id: str | None = None
    reason: str = ""


@runtime_checkable
class ExecutionDispatcher(Protocol):
    """The vendor-neutral execution dispatch seam (SFP-146).

    Mirrors the in-memory bus Protocol precedent (SFP-42): concrete
    dispatchers (Local now, AWS Batch later) satisfy this structurally — no
    inheritance, no SDK import above the seam.
    :meth:`AdmissionScheduler.dispatch_admitted` is the only caller; the
    dispatcher is invoked **only** for admitted execution-bound submissions.
    """

    async def dispatch(self, command: CommandEnvelope) -> DispatchReceipt:
        """Dispatch one admitted command; return its :class:`DispatchReceipt`."""
        ...
