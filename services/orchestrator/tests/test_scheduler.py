"""Tests for the execution admission scheduler (ID-061 / MAS §11.8; SFP-145).

Covers: the exhaustive submit decision table (communication/cancellation
bypass, execution with capacity available, execution at capacity → queued,
unknown command type fail-closed) with reason strings asserted verbatim; FIFO
ordering with 3 queued items; on_complete head admission and the empty-queue
no-admission record; the constructor's fail-closed capacity guard; the
partition-covers-all-8-members test; the read-only ``in_flight()`` accessor;
frozen/immutability of :class:`AdmissionDecision`; determinism; and module
purity (no clock/bus/I/O imports — AP-011).
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest
from orchestrator.domain.scheduler import (
    CANCELLATION_COMMANDS,
    COMMUNICATION_COMMANDS,
    EXECUTION_COMMANDS,
    AdmissionDecision,
    AdmissionScheduler,
)
from pydantic import ValidationError
from sfp_contracts.commands import CommandType

EXECUTE = CommandType.EXECUTE_CODING_JOB.value
SYNCHRONIZE = CommandType.SYNCHRONIZE_PULL_REQUEST.value
REVIEW = CommandType.REVIEW_PULL_REQUEST.value
REQUEST_MERGE = CommandType.REQUEST_MERGE.value
REQUEST_USER_INPUT = CommandType.REQUEST_USER_INPUT.value
NOTIFY_USER = CommandType.NOTIFY_USER.value
CANCEL_CODING = CommandType.CANCEL_CODING_JOB.value
CANCEL_REVIEW = CommandType.CANCEL_REVIEW_JOB.value

# --- Constructor: fail-closed capacity guard ---------------------------------


class TestConstructor:
    def test_capacity_zero_raises(self) -> None:
        """capacity=0 must never silently queue-all (fail-closed)."""
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            AdmissionScheduler(capacity=0)

    def test_negative_capacity_raises(self) -> None:
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            AdmissionScheduler(capacity=-3)

    @pytest.mark.parametrize("capacity", [1, 2, 7, 100])
    def test_valid_capacity_constructs(self, capacity: int) -> None:
        scheduler = AdmissionScheduler(capacity=capacity)
        assert scheduler.in_flight() == 0


# --- submit decision table ----------------------------------------------------


class TestSubmitCommunication:
    @pytest.mark.parametrize("command", sorted(COMMUNICATION_COMMANDS))
    def test_admitted_immediately_at_capacity(self, command: str) -> None:
        """Bypass holds even when every execution slot is occupied."""
        scheduler = AdmissionScheduler(capacity=1)
        assert scheduler.submit(EXECUTE).admitted is True
        decision = scheduler.submit(command)
        assert decision == AdmissionDecision(
            admitted=True, reason="communication: immediate (MAS §11.8)"
        )
        assert decision.queue_position is None
        assert scheduler.in_flight() == 1

    def test_does_not_consume_capacity(self) -> None:
        scheduler = AdmissionScheduler(capacity=2)
        scheduler.submit(REQUEST_USER_INPUT)
        scheduler.submit(NOTIFY_USER)
        scheduler.submit(REQUEST_USER_INPUT)
        assert scheduler.in_flight() == 0
        assert scheduler.submit(EXECUTE).admitted is True


class TestSubmitCancellation:
    @pytest.mark.parametrize("command", sorted(CANCELLATION_COMMANDS))
    def test_admitted_immediately_at_capacity(self, command: str) -> None:
        scheduler = AdmissionScheduler(capacity=1)
        assert scheduler.submit(REVIEW).admitted is True
        decision = scheduler.submit(command)
        assert decision == AdmissionDecision(
            admitted=True,
            reason=(
                "cancellation: immediate (MAS §11.8 — a cancel RELEASES capacity, "
                "never consumes it; it is a control signal, not work)"
            ),
        )
        assert decision.queue_position is None
        assert scheduler.in_flight() == 1

    def test_does_not_consume_capacity(self) -> None:
        scheduler = AdmissionScheduler(capacity=1)
        scheduler.submit(EXECUTE)
        scheduler.submit(CANCEL_CODING)
        scheduler.submit(CANCEL_REVIEW)
        assert scheduler.in_flight() == 1


class TestSubmitExecution:
    def test_admitted_with_capacity_available(self) -> None:
        scheduler = AdmissionScheduler(capacity=2)
        decision = scheduler.submit(EXECUTE)
        assert decision == AdmissionDecision(
            admitted=True, reason="execution: capacity available (1/2)"
        )
        assert decision.queue_position is None
        assert scheduler.in_flight() == 1

    def test_second_slot_counts_up(self) -> None:
        scheduler = AdmissionScheduler(capacity=2)
        scheduler.submit(EXECUTE)
        decision = scheduler.submit(REVIEW)
        assert decision.reason == "execution: capacity available (2/2)"
        assert scheduler.in_flight() == 2

    def test_at_capacity_queues_with_position(self) -> None:
        scheduler = AdmissionScheduler(capacity=1)
        scheduler.submit(EXECUTE)
        decision = scheduler.submit(SYNCHRONIZE)
        assert decision == AdmissionDecision(
            admitted=False, queue_position=1, reason="execution: at capacity, queued"
        )
        assert scheduler.in_flight() == 1

    def test_queued_positions_increment(self) -> None:
        scheduler = AdmissionScheduler(capacity=1)
        scheduler.submit(EXECUTE)
        assert scheduler.submit(REVIEW).queue_position == 1
        assert scheduler.submit(SYNCHRONIZE).queue_position == 2
        assert scheduler.submit(REQUEST_MERGE).queue_position == 3

    def test_capacity_one_reproduces_n1_serialization(self) -> None:
        """capacity=1 is the deterministic N=1 discipline made explicit."""
        scheduler = AdmissionScheduler(capacity=1)
        assert scheduler.submit(EXECUTE).admitted is True
        assert scheduler.submit(REVIEW).admitted is False
        assert scheduler.on_complete(EXECUTE).admitted is True


class TestSubmitUnknown:
    @pytest.mark.parametrize(
        "command",
        ["", "NOT_A_COMMAND", "execute_coding_job", "GeneratePRSpecifications", "EXECUTE"],
    )
    def test_not_admitted_fail_closed(self, command: str) -> None:
        scheduler = AdmissionScheduler(capacity=3)
        decision = scheduler.submit(command)
        assert decision.admitted is False
        assert decision.queue_position is None
        assert decision.reason == f"unknown command type: {command}"
        assert scheduler.in_flight() == 0

    def test_unknown_never_queued(self) -> None:
        scheduler = AdmissionScheduler(capacity=1)
        scheduler.submit(EXECUTE)
        scheduler.submit("TOTALLY_UNKNOWN")
        assert scheduler.on_complete(EXECUTE).reason == "execution: completed, queue empty"


# --- on_complete --------------------------------------------------------------


class TestOnComplete:
    def test_empty_queue_returns_no_admission_record(self) -> None:
        scheduler = AdmissionScheduler(capacity=2)
        scheduler.submit(EXECUTE)
        decision = scheduler.on_complete(EXECUTE)
        assert decision == AdmissionDecision(
            admitted=False, reason="execution: completed, queue empty"
        )
        assert decision.queue_position is None
        assert scheduler.in_flight() == 0

    def test_empty_queue_never_raises(self) -> None:
        scheduler = AdmissionScheduler(capacity=1)
        decision = scheduler.on_complete(EXECUTE)
        assert decision.reason == "execution: completed, queue empty"
        assert decision.admitted is False

    def test_admits_head_with_position(self) -> None:
        scheduler = AdmissionScheduler(capacity=1)
        scheduler.submit(EXECUTE)
        assert scheduler.submit(REVIEW).queue_position == 1
        decision = scheduler.on_complete(EXECUTE)
        assert decision == AdmissionDecision(
            admitted=True, reason="execution: completed, admitting queued head (position 1)"
        )
        assert decision.queue_position is None
        assert scheduler.in_flight() == 1

    def test_fifo_ordering_three_queued(self) -> None:
        """Three queued execution commands admit strictly in arrival order."""
        scheduler = AdmissionScheduler(capacity=1)
        assert scheduler.submit(EXECUTE).admitted is True
        assert scheduler.submit(REVIEW).queue_position == 1
        assert scheduler.submit(SYNCHRONIZE).queue_position == 2
        assert scheduler.submit(REQUEST_MERGE).queue_position == 3

        assert scheduler.on_complete(EXECUTE).reason == (
            "execution: completed, admitting queued head (position 1)"
        )
        assert scheduler.on_complete(REVIEW).reason == (
            "execution: completed, admitting queued head (position 1)"
        )
        assert scheduler.on_complete(SYNCHRONIZE).reason == (
            "execution: completed, admitting queued head (position 1)"
        )
        assert scheduler.on_complete(REQUEST_MERGE).reason == ("execution: completed, queue empty")
        assert scheduler.in_flight() == 0

    def test_fifo_heads_admitted_one_at_a_time(self) -> None:
        """A head admission holds the freed slot; the queue does not drain."""
        scheduler = AdmissionScheduler(capacity=1)
        scheduler.submit(EXECUTE)
        scheduler.submit(REVIEW)
        scheduler.submit(SYNCHRONIZE)
        scheduler.on_complete(EXECUTE)
        assert scheduler.in_flight() == 1
        # SYNCHRONIZE still waits ahead of the new arrival: position 2, not 1.
        assert scheduler.submit(REQUEST_MERGE).queue_position == 2

    def test_completion_of_non_execution_command_releases_nothing(self) -> None:
        scheduler = AdmissionScheduler(capacity=2)
        scheduler.submit(EXECUTE)
        decision = scheduler.on_complete(NOTIFY_USER)
        assert decision.reason == "execution: completed, queue empty"
        assert scheduler.in_flight() == 1


# --- Partition integrity ------------------------------------------------------


class TestPartition:
    def test_partition_covers_exactly_all_eight_catalogue_members(self) -> None:
        catalogue = {member.value for member in CommandType}
        assert len(catalogue) == 8
        union = EXECUTION_COMMANDS | COMMUNICATION_COMMANDS | CANCELLATION_COMMANDS
        assert union == catalogue

    def test_partitions_are_pairwise_disjoint(self) -> None:
        assert not EXECUTION_COMMANDS & COMMUNICATION_COMMANDS
        assert not EXECUTION_COMMANDS & CANCELLATION_COMMANDS
        assert not COMMUNICATION_COMMANDS & CANCELLATION_COMMANDS

    def test_partition_sizes(self) -> None:
        assert len(EXECUTION_COMMANDS) == 4
        assert len(COMMUNICATION_COMMANDS) == 2
        assert len(CANCELLATION_COMMANDS) == 2

    def test_future_ninth_command_fails_loudly_at_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A catalogue member missing from the partition breaks the import.

        Builds a ``CommandType`` stand-in carrying all 8 real members plus a
        9th the partition does not classify, so the guard's failure mode is an
        import-time error here — not silent misrouting to "unknown".
        """
        from enum import StrEnum

        members = {member.name: member.value for member in CommandType}
        members["NEW_UNCLASSIFIED_COMMAND"] = "NEW_UNCLASSIFIED_COMMAND"
        patched = StrEnum("CommandType", members)

        monkeypatch.setattr("sfp_contracts.commands.CommandType", patched, raising=True)
        try:
            with pytest.raises(RuntimeError, match="out of sync"):
                importlib.reload(sys.modules["orchestrator.domain.scheduler"])
        finally:
            monkeypatch.undo()
            importlib.reload(sys.modules["orchestrator.domain.scheduler"])


# --- Model shape --------------------------------------------------------------


class TestAdmissionDecisionModel:
    def test_frozen(self) -> None:
        decision = AdmissionDecision(admitted=True, reason="x")
        with pytest.raises(ValidationError):
            decision.admitted = False  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            AdmissionDecision(admitted=True, reason="x", queue_position=None, extra=1)  # type: ignore[call-arg]

    def test_default_queue_position_is_none(self) -> None:
        decision = AdmissionDecision(admitted=False, reason="x")
        assert decision.queue_position is None


# --- Determinism --------------------------------------------------------------


class TestDeterminism:
    def test_identical_sequences_yield_identical_decisions(self) -> None:
        def run() -> list[AdmissionDecision]:
            scheduler = AdmissionScheduler(capacity=1)
            return [
                scheduler.submit(EXECUTE),
                scheduler.submit(REVIEW),
                scheduler.submit(SYNCHRONIZE),
                scheduler.on_complete(EXECUTE),
                scheduler.on_complete(REVIEW),
            ]

        assert run() == run()

    def test_queue_is_reusable_across_cycles(self) -> None:
        scheduler = AdmissionScheduler(capacity=2)
        assert scheduler.submit(EXECUTE).admitted is True
        assert scheduler.submit(REVIEW).admitted is True
        assert scheduler.in_flight() == 2
        assert scheduler.submit(SYNCHRONIZE).admitted is False
        scheduler.on_complete(EXECUTE)  # admits SYNCHRONIZE
        assert scheduler.in_flight() == 2
        assert scheduler.submit(REQUEST_MERGE).admitted is False
        assert scheduler.on_complete(REVIEW).admitted is True  # admits REQUEST_MERGE
        assert scheduler.in_flight() == 2  # SYNCHRONIZE + REQUEST_MERGE running
        assert scheduler.on_complete(SYNCHRONIZE).reason == "execution: completed, queue empty"
        assert scheduler.on_complete(REQUEST_MERGE).reason == "execution: completed, queue empty"
        assert scheduler.in_flight() == 0
        assert scheduler.on_complete(REQUEST_MERGE).reason == "execution: completed, queue empty"
        assert scheduler.in_flight() == 0
        assert scheduler.submit(EXECUTE).reason == "execution: capacity available (1/2)"


# --- Purity (AP-011) ----------------------------------------------------------


class TestPurity:
    def test_no_forbidden_module_references(self) -> None:
        """The loaded module carries no clock/random/bus/infra module refs."""
        import orchestrator.domain.scheduler as scheduler_module

        forbidden = ("time", "random", "datetime", "asyncio", "sfp_messaging")
        for name in scheduler_module.__dict__:
            assert name.split(".")[0] not in forbidden, f"impure reference: {name}"
        referenced = {
            value.__name__
            for value in vars(scheduler_module).values()
            if isinstance(value, ModuleType)
        }
        for module_name in referenced:
            root = module_name.split(".")[0]
            assert root not in ("time", "random", "datetime", "asyncio", "sfp_messaging"), (
                f"impure imported module: {module_name}"
            )

    def test_module_source_is_clock_free(self) -> None:
        """Static purity check: no time/random/datetime/sleep/bus references."""
        import ast
        import inspect

        source = inspect.getsource(sys.modules["orchestrator.domain.scheduler"])
        tree = ast.parse(source)
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        for root in ("time", "random", "datetime", "asyncio", "sfp_messaging", "os"):
            assert root not in imported_roots, f"impure import root: {root}"
        for token in ("utcnow", "monotonic(", "time.time", "MessageBus"):
            assert token not in source, f"impure token in scheduler source: {token}"
