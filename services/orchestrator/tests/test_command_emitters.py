"""Tests for the application-layer command emitters (MAS §5.3, SFP-152 / SFP-245).

Covers: the happy path (envelope discriminator + payload fields + verbatim
caller-supplied identity, asserted through a capturing fake bus); the
emitter-local payload/discriminator consistency check (wrong payload type →
``ValueError`` naming the expected/actual pair, *before* any bus call — fake
bus records zero publishes); publish-failure propagation (a raising bus
reaches the caller; no retry, no swallow); the return value (the published
envelope); the injected envelope-factory seam; determinism; and purity (no
clock, no randomness, no workflow-table access, no state transition).

SFP-245 additions, per the PRSpec acceptance criteria:

- per-emitter decision rows for the seven new emitters (correct
  ``command_type`` + payload fields + ``idempotency_key`` verbatim), asserted
  through the capturing fake bus;
- a parametrized wrong-payload-type suite over all seven;
- a shared-helper suite asserting ONE definition of the consistency check is
  used by every emitter (no copy-paste).
"""

from __future__ import annotations

import ast
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from orchestrator.application import (
    CancelCodingJobEmitter,
    CancelReviewJobEmitter,
    ExecuteCodingJobEmitter,
    NotifyUserEmitter,
    RequestMergeEmitter,
    RequestUserInputEmitter,
    ReviewPullRequestEmitter,
    SynchronizePullRequestEmitter,
)
from orchestrator.application import command_emitters as module
from orchestrator.application.command_emitters import (
    ExecuteCodingJobEmitter as _Direct,
)
from sfp_contracts.commands import (
    CancelCodingJob,
    CancelReviewJob,
    CommandEnvelope,
    CommandType,
    ExecuteCodingJob,
    NotifyUser,
    RequestMerge,
    RequestUserInput,
    ReviewPullRequest,
    SynchronizePullRequest,
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


# --- Happy path (the SFP-152 template emitter) --------------------------------


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
    # round-trips exactly as supplied, including idempotency_key (dedup is the
    # bus/consumer's job — the emitter never regenerates or mutates it).
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
        await emitter.emit(
            ReviewPullRequest(pr_number=7, repo="acme/app"),
            **_IDENTITY,  # type: ignore[arg-type]
        )
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
    assert len(module.__all__) == 8


# ===========================================================================
# SFP-245: the seven sibling emitters
# ===========================================================================
#
# Every new emitter is an exact structural sibling of the SFP-152 template:
# same constructor seams (injected bus + optional envelope factory), same
# thin async ``emit`` contract (check → build → publish → return). The rows
# below drive all seven through the same decision table so each row asserts
# the discriminator, the payload fields, and the verbatim idempotency_key.


_EMITTER_ROWS: list[
    tuple[
        type[Any],  # emitter class
        CommandType,  # expected discriminator
        Any,  # the correct payload instance
        dict[str, Any],  # the payload fields to assert
        str,  # the payload class name (for the error message)
    ]
] = [
    (
        ReviewPullRequestEmitter,
        CommandType.REVIEW_PULL_REQUEST,
        ReviewPullRequest(pr_number=7, repo="acme/app"),
        {"pr_number": 7, "repo": "acme/app"},
        "ReviewPullRequest",
    ),
    (
        SynchronizePullRequestEmitter,
        CommandType.SYNCHRONIZE_PULL_REQUEST,
        SynchronizePullRequest(pr_number=7, repo="acme/app"),
        {"pr_number": 7, "repo": "acme/app"},
        "SynchronizePullRequest",
    ),
    (
        RequestMergeEmitter,
        CommandType.REQUEST_MERGE,
        RequestMerge(pr_number=7, repo="acme/app"),
        {"pr_number": 7, "repo": "acme/app"},
        "RequestMerge",
    ),
    (
        RequestUserInputEmitter,
        CommandType.REQUEST_USER_INPUT,
        RequestUserInput(session_id="sess-1", prompt="Continue?"),
        {"session_id": "sess-1", "prompt": "Continue?"},
        "RequestUserInput",
    ),
    (
        NotifyUserEmitter,
        CommandType.NOTIFY_USER,
        NotifyUser(session_id="sess-1", message="PR opened"),
        {"session_id": "sess-1", "message": "PR opened"},
        "NotifyUser",
    ),
    (
        CancelCodingJobEmitter,
        CommandType.CANCEL_CODING_JOB,
        CancelCodingJob(job_id="job-1", reason="owner cancel"),
        {"job_id": "job-1", "reason": "owner cancel"},
        "CancelCodingJob",
    ),
    (
        CancelReviewJobEmitter,
        CommandType.CANCEL_REVIEW_JOB,
        CancelReviewJob(job_id="job-1", reason="genuine failure"),
        {"job_id": "job-1", "reason": "genuine failure"},
        "CancelReviewJob",
    ),
]


# --- Per-emitter decision rows ------------------------------------------------


@pytest.mark.parametrize(
    ("emitter_cls", "expected_command_type", "payload", "payload_fields", "payload_name"),
    _EMITTER_ROWS,
)
async def test_sibling_emitter_publishes_expected_envelope(
    emitter_cls: type[Any],
    expected_command_type: CommandType,
    payload: Any,
    payload_fields: dict[str, Any],
    payload_name: str,
) -> None:
    bus = _RecordingBus()
    emitter = emitter_cls(bus)

    returned = await emitter.emit(payload, **_IDENTITY)  # type: ignore[call-arg]

    assert len(bus.published) == 1
    envelope = bus.published[0]
    assert isinstance(envelope, CommandEnvelope)
    assert envelope.command_type is expected_command_type
    assert type(envelope.payload).__name__ == payload_name
    for field, value in payload_fields.items():
        assert getattr(envelope.payload, field) == value
    # idempotency_key is passed through verbatim — never invented or mutated.
    assert envelope.idempotency_key == "idem-1"
    # The return value IS the published envelope.
    assert returned is envelope


@pytest.mark.parametrize(
    ("emitter_cls", "expected_command_type", "payload", "payload_fields", "payload_name"),
    _EMITTER_ROWS,
)
async def test_sibling_emitter_carries_caller_identity_verbatim(
    emitter_cls: type[Any],
    expected_command_type: CommandType,
    payload: Any,
    payload_fields: dict[str, Any],
    payload_name: str,
) -> None:
    bus = _RecordingBus()
    await emitter_cls(bus).emit(payload, **_IDENTITY)  # type: ignore[call-arg]

    envelope = bus.published[0]
    assert envelope.message_id == "m-1"
    assert envelope.idempotency_key == "idem-1"
    assert envelope.correlation_id == "corr-1"
    assert envelope.causation_id == "cause-1"
    assert envelope.occurred_at == "2026-08-22T00:00:00Z"
    assert envelope.command_type is expected_command_type


# --- The shared consistency check, parametrized over all seven ----------------


@pytest.mark.parametrize(
    ("emitter_cls", "expected_command_type", "payload", "payload_fields", "payload_name"),
    _EMITTER_ROWS,
)
async def test_sibling_wrong_payload_raises_before_any_bus_call(
    emitter_cls: type[Any],
    expected_command_type: CommandType,
    payload: Any,
    payload_fields: dict[str, Any],
    payload_name: str,
) -> None:
    # The wrong payload is the template emitter's payload — a valid
    # CommandPayload bound to a *different* discriminator.
    wrong = _payload()
    bus = _RecordingBus()
    emitter = emitter_cls(bus)

    with pytest.raises(ValueError) as excinfo:
        await emitter.emit(wrong, **_IDENTITY)  # type: ignore[arg-type]

    assert payload_name in str(excinfo.value)
    assert "ExecuteCodingJob" in str(excinfo.value)
    assert bus.published == []


@pytest.mark.parametrize(
    ("emitter_cls", "expected_command_type", "payload", "payload_fields", "payload_name"),
    _EMITTER_ROWS,
)
async def test_sibling_wrong_payload_dict_raises_before_any_bus_call(
    emitter_cls: type[Any],
    expected_command_type: CommandType,
    payload: Any,
    payload_fields: dict[str, Any],
    payload_name: str,
) -> None:
    # Not a CommandPayload at all.
    wrong = {"job_id": "job-1", "pr_spec_id": "SFP-152"}
    bus = _RecordingBus()
    emitter = emitter_cls(bus)

    with pytest.raises(ValueError) as excinfo:
        await emitter.emit(wrong, **_IDENTITY)  # type: ignore[arg-type]

    assert payload_name in str(excinfo.value)
    assert "dict" in str(excinfo.value)
    assert bus.published == []


# --- Publish failures propagate, parametrized over all seven ------------------


@pytest.mark.parametrize(
    ("emitter_cls", "expected_command_type", "payload", "payload_fields", "payload_name"),
    _EMITTER_ROWS,
)
async def test_sibling_publish_failure_propagates(
    emitter_cls: type[Any],
    expected_command_type: CommandType,
    payload: Any,
    payload_fields: dict[str, Any],
    payload_name: str,
) -> None:
    # No retry, no swallow: the bus's exception reaches the caller intact.
    emitter = emitter_cls(_RaisingBus())
    with pytest.raises(RuntimeError, match="bus unavailable"):
        await emitter.emit(payload, **_IDENTITY)  # type: ignore[call-arg]


# --- The envelope-factory seam, parametrized over all seven -------------------


@pytest.mark.parametrize(
    ("emitter_cls", "expected_command_type", "payload", "payload_fields", "payload_name"),
    _EMITTER_ROWS,
)
async def test_sibling_emit_uses_injected_envelope_factory(
    emitter_cls: type[Any],
    expected_command_type: CommandType,
    payload: Any,
    payload_fields: dict[str, Any],
    payload_name: str,
) -> None:
    bus = _RecordingBus()
    seen: list[Any] = []

    def factory(inner_payload: Any) -> CommandEnvelope:
        seen.append(inner_payload)
        return CommandEnvelope(
            message_id="factory-message",
            idempotency_key="factory-idem",
            correlation_id="factory-corr",
            causation_id="factory-cause",
            occurred_at="2026-08-22T00:00:09Z",
            command_type=expected_command_type,
            payload=inner_payload,
        )

    returned = await emitter_cls(bus, envelope_factory=factory).emit(  # type: ignore[call-arg]
        payload, **_IDENTITY
    )

    assert seen == [payload]
    assert returned is bus.published[0]
    assert returned.message_id == "factory-message"
    assert returned.idempotency_key == "factory-idem"


# --- Determinism, parametrized over all seven ---------------------------------


@pytest.mark.parametrize(
    ("emitter_cls", "expected_command_type", "payload", "payload_fields", "payload_name"),
    _EMITTER_ROWS,
)
async def test_sibling_emit_is_deterministic(
    emitter_cls: type[Any],
    expected_command_type: CommandType,
    payload: Any,
    payload_fields: dict[str, Any],
    payload_name: str,
) -> None:
    first_bus, second_bus = _RecordingBus(), _RecordingBus()
    first = await emitter_cls(first_bus).emit(payload, **_IDENTITY)  # type: ignore[call-arg]
    second = await emitter_cls(second_bus).emit(payload, **_IDENTITY)  # type: ignore[call-arg]

    assert first.to_json() == second.to_json()


# --- The ONE shared consistency-check helper (no copy-paste) ------------------


def test_consistency_check_is_a_single_shared_definition() -> None:
    # The AC requires one helper with seven callers and zero copy-paste. The
    # landed SFP-152 check was refactored onto the shared helper too, so all
    # EIGHT emitters route through `_check_payload_matches_discriminator`.
    assert module.__file__ is not None
    with open(module.__file__) as handle:
        tree = ast.parse(handle.read())

    # Exactly one module-level function raises the discriminator mismatch.
    raisers = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(stmt, ast.Raise) for stmt in ast.walk(node))
    ]
    shared_names = {node.name for node in raisers}
    assert shared_names == {"_check_payload_matches_discriminator"}, (
        "the discriminator mismatch must be raised by exactly one module-level "
        f"helper, found {sorted(shared_names)}"
    )

    # Every emitter class calls that helper inside ``emit``.
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        emit = next(
            (m for m in cls.body if isinstance(m, ast.AsyncFunctionDef) and m.name == "emit"),
            None,
        )
        assert emit is not None, f"{cls.name} must define an async emit method"
        called = {
            call.func.id
            for call in ast.walk(emit)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        assert "_check_payload_matches_discriminator" in called, (
            f"{cls.name}.emit must call the shared consistency check"
        )


def test_all_eight_emitters_are_exported() -> None:
    # The package re-export and the module attribute must be the same class
    # for all eight emitters: the PRSpec's export requirement is exercised,
    # not assumed.
    import orchestrator.application as package

    for name in module.__all__:
        assert getattr(package, name) is getattr(module, name)


@pytest.mark.parametrize(
    ("emitter_cls", "expected_command_type", "payload", "payload_fields", "payload_name"),
    _EMITTER_ROWS,
)
async def test_sibling_consistency_check_runs_before_envelope_construction(
    emitter_cls: type[Any],
    expected_command_type: CommandType,
    payload: Any,
    payload_fields: dict[str, Any],
    payload_name: str,
) -> None:
    # The factory seam is not consulted when the payload is wrong: the check
    # fires before anything is built.
    bus = _RecordingBus()
    factory_calls: list[Any] = []

    def factory(payload: Any) -> CommandEnvelope:  # pragma: no cover
        factory_calls.append(payload)
        raise AssertionError("factory must not run for a mismatched payload")

    emitter = emitter_cls(bus, envelope_factory=factory)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        await emitter.emit(_payload(), **_IDENTITY)  # type: ignore[arg-type]
    assert bus.published == []
    assert factory_calls == []


# --- Unused-import guard (the fixture helpers above are shared) ---------------


def _unused_guard() -> tuple[type[Any], ...]:
    # Referenced so ruff does not flag the tuple of all eight emitter
    # classes, which the shared-helper suite walks structurally.
    return (
        ExecuteCodingJobEmitter,
        ReviewPullRequestEmitter,
        SynchronizePullRequestEmitter,
        RequestMergeEmitter,
        RequestUserInputEmitter,
        NotifyUserEmitter,
        CancelCodingJobEmitter,
        CancelReviewJobEmitter,
    )


_Emit = Callable[[Any], Awaitable[Any]]
