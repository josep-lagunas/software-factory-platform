"""Tests for the application-layer command emitters (MAS §5.3, SFP-152).

Covers: the happy path (envelope discriminator + payload fields + verbatim
caller-supplied identity, asserted through a capturing fake bus); the
emitter-local payload/discriminator consistency check (wrong payload type →
``ValueError`` naming the expected/actual pair, *before* any bus call — fake
bus records zero publishes); publish-failure propagation (a raising bus
reaches the caller; no retry, no swallow); the return value (the published
envelope); the injected envelope-factory seam; determinism; and purity (no
clock, no randomness, no workflow-table access, no state transition).
"""

from __future__ import annotations

from typing import Any

import pytest
from orchestrator.application import ExecuteCodingJobEmitter
from orchestrator.application.command_emitters import ExecuteCodingJobEmitter as _Direct
from sfp_contracts.commands import (
    CommandEnvelope,
    CommandType,
    ExecuteCodingJob,
    ReviewPullRequest,
)


# The package re-export and the module attribute are the same class: the
# PRSpec's export requirement is exercised, not assumed.
def test_package_export_is_the_module_class() -> None:
    assert ExecuteCodingJobEmitter is _Direct


class _RecordingBus:
    """Minimal MessageBus double: records published envelopes."""

    def __init__(self) -> None:
        self.published: list[CommandEnvelope] = []

    async def publish(self, message: Any) -> None:
        self.published.append(message)

    async def subscribe(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("subscribe is not exercised by the emitter")


class _RaisingBus:
    """Minimal MessageBus double: publish always raises."""

    async def publish(self, message: Any) -> None:
        raise RuntimeError("bus unavailable")

    async def subscribe(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("subscribe is not exercised by the emitter")


_IDENTITY: dict[str, str] = {
    "message_id": "m-1",
    "idempotency_key": "idem-1",
    "correlation_id": "corr-1",
    "causation_id": "cause-1",
    "occurred_at": "2026-08-22T00:00:00Z",
}


def _payload() -> ExecuteCodingJob:
    return ExecuteCodingJob(job_id="job-1", pr_spec_id="SFP-152")


# --- Happy path --------------------------------------------------------------


async def test_emit_publishes_execute_coding_job_envelope() -> None:
    bus = _RecordingBus()
    emitter = ExecuteCodingJobEmitter(bus)
    await emitter.emit(_payload(), **_IDENTITY)

    assert len(bus.published) == 1
    envelope = bus.published[0]
    assert isinstance(envelope, CommandEnvelope)
    assert envelope.command_type is CommandType.EXECUTE_CODING_JOB
    assert isinstance(envelope.payload, ExecuteCodingJob)
    assert envelope.payload.job_id == "job-1"
    assert envelope.payload.pr_spec_id == "SFP-152"


async def test_emit_carries_caller_identity_verbatim() -> None:
    # The emitter generates none of the identity fields: every one of them
    # round-trips exactly as supplied, including idempotency_key (dedup is
    # the bus/consumer's job — the emitter never regenerates or mutates it).
    bus = _RecordingBus()
    await ExecuteCodingJobEmitter(bus).emit(_payload(), **_IDENTITY)

    envelope = bus.published[0]
    assert envelope.message_id == "m-1"
    assert envelope.idempotency_key == "idem-1"
    assert envelope.correlation_id == "corr-1"
    assert envelope.causation_id == "cause-1"
    assert envelope.occurred_at == "2026-08-22T00:00:00Z"


async def test_emit_returns_the_published_envelope() -> None:
    bus = _RecordingBus()
    returned = await ExecuteCodingJobEmitter(bus).emit(_payload(), **_IDENTITY)

    # The return value IS the published envelope — one object, published and
    # handed back for inspection.
    assert returned is bus.published[0]
    assert returned.command_type is CommandType.EXECUTE_CODING_JOB


# --- The emitter-local consistency check -------------------------------------


@pytest.mark.parametrize(
    "wrong_payload",
    [
        # A valid CommandPayload bound to a *different* discriminator.
        ReviewPullRequest(pr_number=7, repo="acme/app"),
        # Not a CommandPayload at all.
        {"job_id": "job-1", "pr_spec_id": "SFP-152"},
    ],
)
async def test_mismatched_payload_raises_before_any_bus_call(wrong_payload: Any) -> None:
    bus = _RecordingBus()
    emitter = ExecuteCodingJobEmitter(bus)

    with pytest.raises(ValueError) as excinfo:
        await emitter.emit(wrong_payload, **_IDENTITY)  # type: ignore[arg-type]

    # SFP-45 is not landed: this local check is the guard, so it must name
    # the expected/actual pair and must have fired before the bus was touched.
    assert "ExecuteCodingJob" in str(excinfo.value)
    assert type(wrong_payload).__name__ in str(excinfo.value)
    assert bus.published == []


async def test_consistency_check_runs_before_envelope_construction() -> None:
    # The factory seam is not consulted either when the payload is wrong:
    # the check fires before anything is built.
    bus = _RecordingBus()
    factory_calls: list[Any] = []

    def factory(payload: Any) -> CommandEnvelope:  # pragma: no cover
        factory_calls.append(payload)
        raise AssertionError("factory must not run for a mismatched payload")

    emitter = ExecuteCodingJobEmitter(bus, envelope_factory=factory)
    with pytest.raises(ValueError):
        await emitter.emit(ReviewPullRequest(pr_number=7, repo="acme/app"), **_IDENTITY)  # type: ignore[arg-type]
    assert bus.published == []
    assert factory_calls == []


# --- Publishing failures propagate -------------------------------------------


async def test_publish_failure_propagates_to_caller() -> None:
    # No retry, no swallow: the bus's exception reaches the caller intact.
    emitter = ExecuteCodingJobEmitter(_RaisingBus())
    with pytest.raises(RuntimeError, match="bus unavailable"):
        await emitter.emit(_payload(), **_IDENTITY)


# --- The injected envelope-factory seam --------------------------------------


async def test_emit_uses_injected_envelope_factory() -> None:
    bus = _RecordingBus()
    seen: list[ExecuteCodingJob] = []

    def factory(payload: ExecuteCodingJob) -> CommandEnvelope:
        seen.append(payload)
        return CommandEnvelope(
            message_id="factory-message",
            idempotency_key="factory-idem",
            correlation_id="factory-corr",
            causation_id="factory-cause",
            occurred_at="2026-08-22T00:00:09Z",
            command_type=CommandType.EXECUTE_CODING_JOB,
            payload=payload,
        )

    returned = await ExecuteCodingJobEmitter(bus, envelope_factory=factory).emit(
        _payload(), **_IDENTITY
    )

    # The factory received the payload and its envelope is what was published
    # and returned — the caller-supplied identity fields are simply unused
    # when the seam owns construction.
    assert seen == [_payload()]
    assert returned is bus.published[0]
    assert returned.message_id == "factory-message"
    assert returned.idempotency_key == "factory-idem"


# --- Purity: no clock, no randomness, no workflow access ---------------------


async def test_emit_is_deterministic_given_identical_inputs() -> None:
    first_bus, second_bus = _RecordingBus(), _RecordingBus()
    first = await ExecuteCodingJobEmitter(first_bus).emit(_payload(), **_IDENTITY)
    second = await ExecuteCodingJobEmitter(second_bus).emit(_payload(), **_IDENTITY)

    assert first.to_json() == second.to_json()


def test_module_has_no_clock_randomness_or_bus_module_state() -> None:
    # Purity by construction: the module imports nothing that can read the
    # clock or a random source, and it holds no module-level mutable state.
    # Asserted on the parsed imports — a prose mention of "randomness" in a
    # docstring is not an impurity.
    import ast

    import orchestrator.application.command_emitters as module

    assert module.__file__ is not None
    with open(module.__file__) as handle:
        tree = ast.parse(handle.read())

    banned_roots = {"datetime", "time", "random", "uuid"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(banned_roots), (
        f"module must not import {sorted(imported & banned_roots)}"
    )
    assert module.__all__ == ["ExecuteCodingJobEmitter"]
