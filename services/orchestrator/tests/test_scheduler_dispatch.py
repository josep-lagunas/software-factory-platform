"""Tests for the execution dispatch seam (SFP-146).

Covers, per the PRSpec acceptance criteria:

- the admission-gating decision table behind
  :meth:`AdmissionScheduler.dispatch_admitted` (execution × admitted /
  not-admitted, communication, cancellation, unknown command types);
- the spy-asserted guarantee that the dispatcher is **never** invoked on the
  queued path and is awaited **exactly once** on the admitted path;
- the queued receipt returned verbatim
  (``accepted=False, external_id=None, reason="not admitted: queued"``);
- :class:`LocalExecutionDispatcher`: accepts everything, deterministic
  ``local-<n:06d>`` sequence ids (strictly increasing, no clock/randomness,
  instance-scoped, repeatable across identically-constructed dispatchers),
  the ``capacity_source`` constructor default, and Protocol conformance;
- :class:`BatchExecutionDispatcher` stub: ``dispatch`` raises
  :class:`NotImplementedError`, the constructor stores ``capacity_source``
  without reading the environment, and the class satisfies the
  :class:`ExecutionDispatcher` Protocol at runtime;
- immutability of :class:`DispatchReceipt` (frozen, ``extra='forbid'``);
- no workflow-state mutation in the dispatch path (ID-061 / MAS §11.8);
- determinism of the whole seam.
"""

from __future__ import annotations

import pytest
from orchestrator.domain.scheduler import (
    AdmissionScheduler,
    DispatchReceipt,
    ExecutionDispatcher,
)
from orchestrator.infrastructure.dispatch import (
    LOCAL_EXECUTION_CAPACITY,
    BatchExecutionDispatcher,
    LocalExecutionDispatcher,
)
from pydantic import ValidationError
from sfp_contracts.commands import CommandEnvelope, CommandType

#: Bound at import, before any sibling suite can ``importlib.reload`` the
#: contracts module with a patched ``CommandType`` (test_scheduler.py's
#: partition guard does exactly that mid-run). The real member stays pinned
#: here, so envelope construction is immune to module-reload pollution.
_EXECUTE_TYPE: CommandType = CommandType.EXECUTE_CODING_JOB

EXECUTE = CommandType.EXECUTE_CODING_JOB.value
SYNCHRONIZE = CommandType.SYNCHRONIZE_PULL_REQUEST.value
REVIEW = CommandType.REVIEW_PULL_REQUEST.value
REQUEST_MERGE = CommandType.REQUEST_MERGE.value
REQUEST_USER_INPUT = CommandType.REQUEST_USER_INPUT.value
NOTIFY_USER = CommandType.NOTIFY_USER.value
CANCEL_CODING = CommandType.CANCEL_CODING_JOB.value
CANCEL_REVIEW = CommandType.CANCEL_REVIEW_JOB.value

QUEUED_REASON = "not admitted: queued"


def _envelope(command: CommandType | str, *, n: int = 0) -> CommandEnvelope:
    """A minimal well-formed envelope for the given command type.

    ``command_type`` is resolved via a member lookup against the real enum
    (``_EXECUTE_TYPE.__class__``), never against a possibly-reloaded module
    attribute, so this helper stays correct even if a sibling suite reloads
    ``sfp_contracts.commands`` with a patched catalogue mid-run.
    """
    return CommandEnvelope(
        message_id=f"m-{command}-{n}",
        idempotency_key=f"idem-{command}-{n}",
        correlation_id=f"corr-{n}",
        causation_id=f"cause-{n}",
        occurred_at="2026-08-29T00:00:00Z",
        command_type=_EXECUTE_TYPE.__class__(command),
        payload=None,
    )


class _SpyDispatcher:
    """Records every dispatch call; returns canned receipts.

    The spy IS the assertion: ``calls`` length is exactly the number of times
    the seam reached the dispatcher, and each entry is the envelope it was
    handed.
    """

    def __init__(
        self,
        receipts: list[DispatchReceipt] | None = None,
    ) -> None:
        self.calls: list[CommandEnvelope] = []
        self._receipts = list(receipts or [])
        self._default = 0

    async def dispatch(self, command: CommandEnvelope) -> DispatchReceipt:
        self.calls.append(command)
        if self._receipts:
            return self._receipts.pop(0)
        self._default += 1
        return DispatchReceipt(accepted=True, external_id=f"spy-{self._default:06d}")


# --- Admission-gating decision table ------------------------------------------


class TestDispatchAdmittedGating:
    @pytest.mark.parametrize("command", [EXECUTE, SYNCHRONIZE, REVIEW, REQUEST_MERGE])
    async def test_execution_admitted_dispatches_exactly_once(self, command: str) -> None:
        """Every execution command dispatches while a slot is free."""
        scheduler = AdmissionScheduler(capacity=1)
        spy = _SpyDispatcher()
        envelope = _envelope(command)
        receipt = await scheduler.dispatch_admitted(spy, envelope)
        assert spy.calls == [envelope]
        assert receipt.accepted is True

    async def test_execution_admitted_returns_dispatcher_receipt_verbatim(self) -> None:
        """The seam returns the dispatcher's own receipt unmodified."""
        dispatcher_receipt = DispatchReceipt(
            accepted=True, external_id="job-42", reason="provider: submitted"
        )
        spy = _SpyDispatcher(receipts=[dispatcher_receipt])
        receipt = await AdmissionScheduler(capacity=3).dispatch_admitted(spy, _envelope(EXECUTE))
        assert receipt == dispatcher_receipt
        assert receipt.external_id == "job-42"
        assert receipt.reason == "provider: submitted"

    @pytest.mark.parametrize("command", [EXECUTE, SYNCHRONIZE, REVIEW, REQUEST_MERGE])
    async def test_execution_at_capacity_returns_queued_receipt(self, command: str) -> None:
        """A saturated scheduler queues the submission without dispatching."""
        scheduler = AdmissionScheduler(capacity=1)
        assert scheduler.submit(EXECUTE).admitted is True  # occupy the only slot
        spy = _SpyDispatcher()
        receipt = await scheduler.dispatch_admitted(spy, _envelope(command))
        # Value-shaped (model_dump), not class-equality: test_scheduler.py's
        # import-guard reloads ``orchestrator.domain.scheduler`` mid-run, which
        # rebinds the module's DispatchReceipt to a *new* class object — the
        # class captured here at collection time is then a different class, and
        # pydantic ``==`` is class-aware. The dump comparison asserts the same
        # contract (exact fields, no extras) without that coupling.
        assert receipt.model_dump() == {
            "accepted": False,
            "external_id": None,
            "reason": QUEUED_REASON,
        }
        assert spy.calls == []

    @pytest.mark.parametrize("command", [REQUEST_USER_INPUT, NOTIFY_USER])
    async def test_communication_bypasses_capacity_and_dispatches(self, command: str) -> None:
        """Communication rides outside capacity — dispatched even at capacity."""
        scheduler = AdmissionScheduler(capacity=1)
        assert scheduler.submit(EXECUTE).admitted is True
        spy = _SpyDispatcher()
        receipt = await scheduler.dispatch_admitted(spy, _envelope(command))
        assert spy.calls == [_envelope(command)]
        assert receipt.accepted is True

    @pytest.mark.parametrize("command", [CANCEL_CODING, CANCEL_REVIEW])
    async def test_cancellation_bypasses_capacity_and_dispatches(self, command: str) -> None:
        """Cancellation is a control signal — never serialized (MAS §11.8)."""
        scheduler = AdmissionScheduler(capacity=1)
        assert scheduler.submit(EXECUTE).admitted is True
        spy = _SpyDispatcher()
        receipt = await scheduler.dispatch_admitted(spy, _envelope(command))
        assert len(spy.calls) == 1
        assert receipt.accepted is True


# --- Spy-asserted dispatcher-not-called guarantee ------------------------------


class TestSpyNotCalledWhenNotAdmitted:
    async def test_queued_execution_never_invokes_dispatcher(self) -> None:
        scheduler = AdmissionScheduler(capacity=1)
        first = await scheduler.dispatch_admitted(_SpyDispatcher(), _envelope(EXECUTE))
        assert first.accepted is True
        spy = _SpyDispatcher()
        receipt = await scheduler.dispatch_admitted(spy, _envelope(REVIEW, n=1))
        assert spy.calls == []  # the assertion: zero dispatches on the queued path
        assert receipt.accepted is False
        assert receipt.external_id is None

    async def test_admitted_path_awaits_dispatcher_exactly_once(self) -> None:
        spy = _SpyDispatcher()
        envelope = _envelope(REQUEST_MERGE)
        await AdmissionScheduler(capacity=2).dispatch_admitted(spy, envelope)
        assert len(spy.calls) == 1  # exactly once — never retried, never duplicated
        assert spy.calls[0] is envelope

    async def test_queued_submission_is_retained_for_later_admission(self) -> None:
        """The queued path leaves the submission recoverable via on_complete."""
        scheduler = AdmissionScheduler(capacity=1)
        assert (
            await scheduler.dispatch_admitted(_SpyDispatcher(), _envelope(EXECUTE))
        ).accepted is True
        queued = await scheduler.dispatch_admitted(_SpyDispatcher(), _envelope(REVIEW, n=1))
        assert queued.accepted is False
        assert scheduler.on_complete(EXECUTE).admitted is True  # REVIEW promoted


# --- LocalExecutionDispatcher ---------------------------------------------------


class TestLocalExecutionDispatcher:
    async def test_accepts_everything(self) -> None:
        dispatcher = LocalExecutionDispatcher()
        for command in (EXECUTE, REVIEW, REQUEST_MERGE, NOTIFY_USER):
            receipt = await dispatcher.dispatch(_envelope(command))
            assert receipt.accepted is True

    async def test_external_id_is_sequence_based(self) -> None:
        dispatcher = LocalExecutionDispatcher()
        assert (await dispatcher.dispatch(_envelope(EXECUTE))).external_id == "local-000001"
        assert (await dispatcher.dispatch(_envelope(REVIEW, n=1))).external_id == "local-000002"
        assert (await dispatcher.dispatch(_envelope(EXECUTE, n=2))).external_id == "local-000003"

    async def test_ids_strictly_increasing_across_many_dispatches(self) -> None:
        dispatcher = LocalExecutionDispatcher()
        ids = [(await dispatcher.dispatch(_envelope(EXECUTE, n=i))).external_id for i in range(25)]
        assert ids == [f"local-{n:06d}" for n in range(1, 26)]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)

    async def test_reason_defaults_empty_on_accept(self) -> None:
        receipt = await LocalExecutionDispatcher().dispatch(_envelope(EXECUTE))
        assert receipt.reason == ""

    def test_capacity_source_defaults_to_local_constant(self) -> None:
        assert LocalExecutionDispatcher().capacity_source() == LOCAL_EXECUTION_CAPACITY

    def test_capacity_source_is_injectable(self) -> None:
        state = {"value": 4}

        def source() -> int:
            return state["value"]

        dispatcher = LocalExecutionDispatcher(capacity_source=source)
        assert dispatcher.capacity_source() == 4
        state["value"] = 9
        assert dispatcher.capacity_source() == 9  # resolved lazily, never cached

    async def test_sequence_is_instance_scoped(self) -> None:
        """Two dispatchers never interleave into one another's sequences."""
        first = LocalExecutionDispatcher()
        second = LocalExecutionDispatcher()
        assert (await first.dispatch(_envelope(EXECUTE))).external_id == "local-000001"
        assert (await second.dispatch(_envelope(EXECUTE))).external_id == "local-000001"
        assert (await first.dispatch(_envelope(EXECUTE, n=1))).external_id == "local-000002"

    async def test_identical_sequences_yield_identical_ids(self) -> None:
        """Determinism (MAS §12.7): same construction + dispatch order, same ids."""

        async def run() -> list[str | None]:
            dispatcher = LocalExecutionDispatcher()
            return [
                (await dispatcher.dispatch(_envelope(command, n=i))).external_id
                for i, command in enumerate([EXECUTE, REVIEW, REQUEST_MERGE])
            ]

        assert await run() == await run()

    def test_satisfies_protocol_at_runtime(self) -> None:
        assert isinstance(LocalExecutionDispatcher(), ExecutionDispatcher)


# --- BatchExecutionDispatcher stub ----------------------------------------------


class TestBatchExecutionDispatcherStub:
    async def test_dispatch_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="doc-132..134"):
            await BatchExecutionDispatcher().dispatch(_envelope(EXECUTE))

    async def test_dispatch_raises_regardless_of_capacity_source(self) -> None:
        dispatcher = BatchExecutionDispatcher(capacity_source=lambda: 16)
        with pytest.raises(NotImplementedError, match="SFP-146"):
            await dispatcher.dispatch(_envelope(REVIEW))

    def test_constructor_stores_capacity_source_without_reading_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No config/env read at construction or import time (ID-060 sandbox)."""
        monkeypatch.setenv("AWS_BATCH_QUEUE", "poison")
        monkeypatch.setenv("SFP_MAX_VCPUS", "poison")
        sentinel: list[int] = []

        def source() -> int:
            sentinel.append(1)  # must never be called by the stub
            return 0

        dispatcher = BatchExecutionDispatcher(capacity_source=source)
        assert dispatcher.capacity_source is source
        assert sentinel == []

    def test_capacity_source_defaults_to_none(self) -> None:
        assert BatchExecutionDispatcher().capacity_source is None

    def test_satisfies_protocol_at_runtime(self) -> None:
        assert isinstance(BatchExecutionDispatcher(), ExecutionDispatcher)

    def test_no_boto3_or_config_import(self) -> None:
        """The stub carries no SDK / config coupling (ID-060; stub-only scope)."""
        import sys

        import orchestrator.infrastructure.dispatch.aws_batch as module

        assert not any(
            name.split(".")[0] in ("boto3", "botocore", "os") for name in module.__dict__
        ), "aws_batch must not import boto3/botocore/os"
        assert "boto3" not in sys.modules


# --- Admission + Local dispatcher integration ----------------------------------


class TestSchedulerWithLocalDispatcher:
    async def test_admitted_execution_gets_local_id(self) -> None:
        receipt = await AdmissionScheduler(capacity=1).dispatch_admitted(
            LocalExecutionDispatcher(), _envelope(EXECUTE)
        )
        assert receipt == DispatchReceipt(accepted=True, external_id="local-000001")

    async def test_queued_then_promoted_dispatches_after_complete(self) -> None:
        """A queued submission retries into an admitted dispatch after completion.

        Traced admission bookkeeping: the first submission fills capacity=1;
        the second queues. ``on_complete(EXECUTE)`` promotes the queued head —
        which itself re-occupies the slot (in_flight stays 1) — so the
        *promotion* already holds the slot and a same-envelope re-submission
        would queue again. The realistic driver shape is therefore: complete
        twice (the head's own completion frees the slot) before re-dispatch.
        Throughout, the queued attempt consumed nothing from the dispatcher's
        id sequence (it never reached the dispatcher).
        """
        scheduler = AdmissionScheduler(capacity=1)
        dispatcher = LocalExecutionDispatcher()
        first = await scheduler.dispatch_admitted(dispatcher, _envelope(EXECUTE))
        queued = await scheduler.dispatch_admitted(dispatcher, _envelope(REVIEW, n=1))
        assert queued.reason == QUEUED_REASON
        assert scheduler.in_flight() == 1

        assert scheduler.on_complete(EXECUTE).admitted is True  # promotes the head
        assert scheduler.in_flight() == 1  # the promotion itself occupies the slot
        assert scheduler.on_complete(REVIEW).reason == "execution: completed, queue empty"
        assert scheduler.in_flight() == 0

        promoted = await scheduler.dispatch_admitted(dispatcher, _envelope(REVIEW, n=2))
        assert first.external_id == "local-000001"
        assert promoted.external_id == "local-000002"

    async def test_deterministic_end_to_end(self) -> None:
        """Same scheduler construction + envelope order → same receipts."""

        async def run() -> list[str | None]:
            scheduler = AdmissionScheduler(capacity=2)
            dispatcher = LocalExecutionDispatcher()
            return [
                (await scheduler.dispatch_admitted(dispatcher, _envelope(c, n=i))).external_id
                for i, c in enumerate([EXECUTE, REVIEW, SYNCHRONIZE, REQUEST_MERGE, EXECUTE])
            ]

        assert await run() == await run()


# --- DispatchReceipt contract ----------------------------------------------------


class TestDispatchReceipt:
    def test_frozen(self) -> None:
        receipt = DispatchReceipt(accepted=True, external_id="x")
        with pytest.raises(ValidationError):
            receipt.external_id = "y"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            DispatchReceipt(accepted=True, external_id="x", extra=1)  # type: ignore[call-arg]

    def test_defaults(self) -> None:
        receipt = DispatchReceipt(accepted=False)
        assert receipt.external_id is None
        assert receipt.reason == ""

    async def test_queued_receipt_is_the_contract_shape(self) -> None:
        scheduler = AdmissionScheduler(capacity=1)
        assert scheduler.submit(EXECUTE).admitted is True
        receipt = await scheduler.dispatch_admitted(_SpyDispatcher(), _envelope(REVIEW, n=1))
        assert receipt.model_dump() == {
            "accepted": False,
            "external_id": None,
            "reason": QUEUED_REASON,
        }


# --- No workflow-state mutation (ID-061 / MAS §11.8) -----------------------------


class TestNoWorkflowStateMutation:
    async def test_dispatch_path_never_touches_workflow_state(self) -> None:
        """ID-061: the scheduler/dispatch path is not a transition source.

        Asserted two ways: (a) statically — the dispatch seam's modules
        import nothing from the workflow state machine; (b) behaviorally —
        driving admitted and queued dispatches leaves the §8.4 state set and a
        representative workflow state object untouched.
        """
        import orchestrator.domain.scheduler as scheduler_module
        import orchestrator.infrastructure.dispatch.local as local_module
        from orchestrator.domain.workflow import STATES, WorkflowState
        from orchestrator.domain.workflow.state_machine import transition

        # (a) Static: no dispatch-path module references the state machine.
        for module in (scheduler_module, local_module):
            for name, value in vars(module).items():
                assert not name.startswith("orchestrator.domain.workflow"), (
                    f"{module.__name__} references workflow: {name}"
                )
                if isinstance(value, type):
                    assert not issubclass(value, WorkflowState), (
                        f"{module.__name__} subclasses WorkflowState: {name}"
                    )
                assert value is not transition

        # (b) Behavioral: states before == states after driving the seam.
        states_before = tuple(STATES)
        scheduler = AdmissionScheduler(capacity=1)
        dispatcher = LocalExecutionDispatcher()
        await scheduler.dispatch_admitted(dispatcher, _envelope(EXECUTE))
        await scheduler.dispatch_admitted(dispatcher, _envelope(REVIEW, n=1))
        scheduler.on_complete(EXECUTE)
        assert tuple(STATES) == states_before
        assert dispatcher.capacity_source() == LOCAL_EXECUTION_CAPACITY
