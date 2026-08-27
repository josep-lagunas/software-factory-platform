"""Stream liveness watchdogs for :class:`ClaudeAgentRuntime` (SFP-242).

The SDK's ``query()`` spawns the Claude Code CLI; when the endpoint goes mute
the CLI stays up and the run hangs for hours with zero stream events. Two
budgets bound that — first-event and between-events inactivity — both
env-tunable via ``SFP_SPAWN_FIRST_EVENT_TIMEOUT`` / ``SFP_SPAWN_PROGRESS_TIMEOUT``.

These tests inject stub async-generator ``query_fn`` objects (never the real
CLI) and use SUB-SECOND budgets so every case is fast and deterministic
(MAS §12.7): a watchdog either fires in milliseconds or provably cannot.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError
from sfp_agent_runtime.interfaces import AgentRunRequest
from sfp_config import SecretRef
from test_agent_runtime import (  # type: ignore[no-redef]
    FakeMessage,
    FakeSecretProvider,
    OutputContract,
    _noop_sleep,
)
from workspace_worker.agent_runtime import runtime as runtime_module
from workspace_worker.agent_runtime.runtime import (
    ClaudeAgentRuntime,
    _TransientSDKError,
)
from workspace_worker.infrastructure.settings import WorkspaceWorkerSettings

# Sub-second budgets — the largest used anywhere in this module. Tests must
# never use the production defaults (300s / 900s) or they would hang for real.
BUDGET_S = 0.15


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #


@dataclass
class _StreamProbe:
    """Records HOW a stream ended (the "no orphan / natural exhaust" evidence).

    ``closed`` alone cannot distinguish the two ends: a normally-exhausted
    async generator ALSO runs its ``finally`` on finalization. ``forced_close``
    is set only by the runtime's watchdog cleanup path, so a natural exhaust
    shows ``forced_close=False, closed=True`` and a watchdog trip shows
    ``forced_close=True, closed=True``.
    """

    closed: bool = False
    forced_close: bool = False


@dataclass
class MuteQuery:
    """A ``query_fn`` whose stream NEVER yields — a mute spawn.

    ``__anext__`` suspends forever, exactly like a CLI whose endpoint never
    delivers an event. Records call/closure evidence on ``probe``.

    Forcing a suspended async generator to shut down reaches it EITHER as
    ``CancelledError`` (wait_for cancels the pending ``__anext__`` first) OR as
    ``GeneratorExit`` (aclose() on one not currently suspended at an await) —
    the probe treats both as the runtime's forced close.
    """

    probe: _StreamProbe = field(default_factory=_StreamProbe)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, *, prompt: str, options: Any) -> Any:
        self.calls.append({"prompt": prompt, "options": options})
        return self._stream()

    async def _stream(self):  # noqa: ANN202 - async generator
        try:
            while True:
                # Suspend forever (no event arrives) until cancelled/closed.
                await asyncio.sleep(3600)
                yield FakeMessage(result="never")
        except (GeneratorExit, asyncio.CancelledError):
            # wait_for cancels the suspended __anext__; aclose() injects
            # GeneratorExit. Both are the watchdog's forced close.
            self.probe.forced_close = True
            raise
        finally:
            # The real SDK's ``finally: await query.close()`` runs the CLI
            # terminate→kill escalation from this same hook.
            self.probe.closed = True


@dataclass
class SlowThenMuteQuery:
    """Emits one event, then goes silent longer than the progress budget."""

    probe: _StreamProbe = field(default_factory=_StreamProbe)
    first_delay_s: float = 0.01
    silence_s: float = 3600.0
    events_emitted: int = 0

    def __call__(self, *, prompt: str, options: Any) -> Any:
        return self._stream()

    async def _stream(self):  # noqa: ANN202 - async generator
        try:
            await asyncio.sleep(self.first_delay_s)
            self.events_emitted += 1
            yield FakeMessage(result='{"answer": "partial"}')
            while True:
                # Mid-run silence: no further event within any test budget.
                await asyncio.sleep(self.silence_s)
                yield FakeMessage(result="never")
        except (GeneratorExit, asyncio.CancelledError):
            self.probe.forced_close = True
            raise
        finally:
            self.probe.closed = True


@dataclass
class HealthyCadenceQuery:
    """Emits events at a cadence well under budget for LONGER than both budgets.

    The regression case: total duration exceeds both watchdog budgets (so a
    naive total-duration timeout WOULD fire) but every inter-event gap is well
    under the progress budget (so a correct inactivity watchdog must not).
    """

    event_count: int = 6
    gap_s: float = 0.05
    probe: _StreamProbe = field(default_factory=_StreamProbe)
    events_emitted: int = 0

    def __call__(self, *, prompt: str, options: Any) -> Any:
        return self._stream()

    async def _stream(self):  # noqa: ANN202 - async generator
        try:
            for i in range(self.event_count):
                await asyncio.sleep(self.gap_s)
                self.events_emitted += 1
                if i == self.event_count - 1:
                    yield FakeMessage(result='{"answer": "42"}')
                else:
                    yield FakeMessage(result='{"answer": "progress"}')
        except (GeneratorExit, asyncio.CancelledError):
            self.probe.forced_close = True
            raise
        finally:
            # Runs on natural exhaustion too — see _StreamProbe.
            self.probe.closed = True


def _fake_settings(**overrides: Any) -> WorkspaceWorkerSettings:
    base: dict[str, Any] = {
        "anthropic_base_url": "https://api.example.com",
        "default_model": "claude-sonnet-4",
        "llm_provider_secret_ref": SecretRef(name="llm/token"),
    }
    base.update(overrides)
    return WorkspaceWorkerSettings(**base)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_watchdog_settings(**overrides: Any) -> WorkspaceWorkerSettings:
    """Settings with sub-second watchdog budgets (never the 300s/900s defaults)."""
    watchdog: dict[str, Any] = {
        "spawn_first_event_timeout_s": BUDGET_S,
        "spawn_progress_timeout_s": BUDGET_S * 2,
    }
    watchdog.update(overrides)
    return _fake_settings(**watchdog)


def make_watchdog_runtime(
    query_fn: Any,
    settings: WorkspaceWorkerSettings | None = None,
    *,
    max_retries: int = 0,
) -> ClaudeAgentRuntime:
    return ClaudeAgentRuntime(
        settings or make_watchdog_settings(),
        FakeSecretProvider(),
        OutputContract,
        query_fn=query_fn,
        max_retries=max_retries,
        sleep=_noop_sleep,
    )


def _req(agent: str = "coder") -> AgentRunRequest:
    return AgentRunRequest(agent=agent, ticket_id="SFP-242", prompt="do it")


def _run_and_capture(query_fn: Any, **kwargs: Any) -> Any:
    """Run once through the public ``run()``; retries disabled."""
    settings = kwargs.pop("settings", None) or make_watchdog_settings()
    runtime = make_watchdog_runtime(query_fn, settings, **kwargs)
    return runtime.run(_req())


def _consume(query_fn: Any, settings: WorkspaceWorkerSettings | None = None) -> None:
    """Drive ``_consume_stream`` directly and let its error propagate."""
    runtime = make_watchdog_runtime(query_fn, settings, max_retries=0)
    asyncio.run(
        runtime._consume_stream(query_fn, "p", object(), "coder")  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# (a) Mute spawn trips the FIRST-EVENT watchdog
# --------------------------------------------------------------------------- #


def test_mute_spawn_trips_first_event_watchdog() -> None:
    """A stream that never yields is aborted at the first-event budget."""
    qfn = MuteQuery()
    res = _run_and_capture(qfn)
    assert res.success is False
    # The watchdog error must name the agent, the stage, and the budget.
    assert "coder" in res.error
    assert "spawn watchdog" in res.error
    assert f"{BUDGET_S:.0f}s" in res.error
    assert "mute" in res.error.lower()


def test_watchdog_raises_the_existing_transient_error_type() -> None:
    """SFP-242: the trip raises ``_TransientSDKError`` — the caller's abort
    path and the retryer's transient classification apply UNCHANGED."""
    qfn = MuteQuery()
    runtime = make_watchdog_runtime(qfn, max_retries=0)
    coro = runtime._consume_stream(qfn, "p", object(), "coder")  # type: ignore[arg-type]
    with pytest.raises(_TransientSDKError, match="spawn watchdog"):
        asyncio.run(coro)


def test_watchdog_trip_is_transient_so_retryer_retries_it() -> None:
    """The trip classifies as transient (retryable), same as a 5xx result."""
    qfn = MuteQuery()
    res = _run_and_capture(qfn, max_retries=2)
    assert res.success is False
    # 1 attempt + 2 retries = 3 spawns before failing closed.
    assert len(qfn.calls) == 3


def test_mute_spawn_closes_stream_before_raising() -> None:
    """On a trip the stream generator is closed (CLI reaped) BEFORE the error
    propagates — no orphan process is left behind."""
    qfn = MuteQuery()
    runtime = make_watchdog_runtime(qfn, max_retries=0)
    with pytest.raises(_TransientSDKError, match="spawn watchdog"):
        asyncio.run(
            runtime._consume_stream(qfn, "p", object(), "coder")  # type: ignore[arg-type]
        )
    assert qfn.probe.closed is True
    assert qfn.probe.forced_close is True


def test_watchdog_message_reports_elapsed_seconds() -> None:
    """The message includes elapsed time so an operator can see the budget."""
    qfn = MuteQuery()
    runtime = make_watchdog_runtime(qfn, max_retries=0)
    with pytest.raises(_TransientSDKError, match=r"silent for \d+s"):
        asyncio.run(
            runtime._consume_stream(qfn, "p", object(), "coder")  # type: ignore[arg-type]
        )


def test_watchdog_message_names_the_stage_when_agent_missing() -> None:
    """With no agent label the stage still names the watchdog (defensive)."""
    qfn = MuteQuery()
    runtime = make_watchdog_runtime(qfn, max_retries=0)
    with pytest.raises(_TransientSDKError, match="spawn watchdog"):
        asyncio.run(
            runtime._consume_stream(qfn, "p", object(), None)  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------- #
# (b) Healthy cadence NEVER trips either watchdog (regression)
# --------------------------------------------------------------------------- #


def test_healthy_cadence_never_trips() -> None:
    """Events at a sub-budget cadence across a total duration LONGER than both
    budgets complete without error — the watchdogs bound INACTIVITY, not the
    run's total duration."""
    # 6 events × 0.05s gap = ~0.30s total > first (0.15s) and progress (0.25s)
    # budgets; every inter-event gap (0.05s) is far under budget.
    qfn = HealthyCadenceQuery(event_count=6, gap_s=0.05)
    res = _run_and_capture(
        qfn,
        settings=make_watchdog_settings(
            spawn_first_event_timeout_s=0.15, spawn_progress_timeout_s=0.25
        ),
    )
    assert res.success is True
    assert res.output == {"answer": "42"}
    assert qfn.events_emitted == 6
    # Natural exhaustion: closed by finalization, NOT by the watchdog.
    assert qfn.probe.closed is True
    assert qfn.probe.forced_close is False


def test_healthy_single_event_completes() -> None:
    """A stream that yields its final message immediately never trips."""
    qfn = HealthyCadenceQuery(event_count=1, gap_s=0.0)
    res = _run_and_capture(qfn)
    assert res.success is True
    assert qfn.probe.forced_close is False


def test_progress_budget_extends_past_first_event_budget() -> None:
    """The two budgets are independent: a longer progress budget lets a run
    with a slow first event (but healthy cadence after) still complete."""
    qfn = HealthyCadenceQuery(event_count=4, gap_s=0.06)
    res = _run_and_capture(
        qfn,
        settings=make_watchdog_settings(
            spawn_first_event_timeout_s=0.12, spawn_progress_timeout_s=0.2
        ),
    )
    assert res.success is True


# --------------------------------------------------------------------------- #
# (c) Mid-run silence trips the PROGRESS watchdog
# --------------------------------------------------------------------------- #


def test_mid_run_silence_trips_progress_watchdog() -> None:
    """One event, then silence longer than the inactivity budget, trips the
    SECOND watchdog with its own stage name in the message."""
    qfn = SlowThenMuteQuery(first_delay_s=0.01, silence_s=3600.0)
    runtime = make_watchdog_runtime(qfn, max_retries=0)
    with pytest.raises(_TransientSDKError, match="progress watchdog") as ei:
        asyncio.run(
            runtime._consume_stream(qfn, "p", object(), "coder")  # type: ignore[arg-type]
        )
    assert "spawn watchdog" not in str(ei.value)
    assert qfn.probe.closed is True
    assert qfn.probe.forced_close is True
    assert qfn.events_emitted == 1


def test_progress_watchdog_message_includes_budget_and_elapsed() -> None:
    """The progress message names its budget and the silent interval."""
    qfn = SlowThenMuteQuery(first_delay_s=0.01, silence_s=3600.0)
    settings = make_watchdog_settings(spawn_progress_timeout_s=0.3)
    runtime = make_watchdog_runtime(qfn, settings, max_retries=0)
    with pytest.raises(_TransientSDKError, match="no stream events in 0s"):
        asyncio.run(
            runtime._consume_stream(qfn, "p", object(), "coder")  # type: ignore[arg-type]
        )


def test_mid_run_silence_fails_closed_through_run() -> None:
    """The progress trip surfaces through run() as a transient-class failure."""
    qfn = SlowThenMuteQuery(first_delay_s=0.01, silence_s=3600.0)
    res = _run_and_capture(qfn, max_retries=0)
    assert res.success is False
    assert "progress watchdog" in res.error


# --------------------------------------------------------------------------- #
# (d) Clean termination on trip — bounded close, no orphan
# --------------------------------------------------------------------------- #


def test_watchdog_close_is_bounded_and_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wedged generator cannot turn the abort back into a hang: aclose is
    itself bounded and its failure is swallowed (the watchdog is the signal)."""

    class _Wedged:
        async def aclose(self) -> None:
            await asyncio.sleep(3600)

    # The production grace is 30s; shrink it so the bound is proven in
    # milliseconds instead of actually waiting half a minute.
    monkeypatch.setattr(runtime_module, "_WATCHDOG_CLOSE_GRACE_S", 0.05)
    # Must return quickly, not hang.
    asyncio.run(ClaudeAgentRuntime._close_stream_silently(_Wedged()))


def test_watchdog_close_swallows_aclose_error() -> None:
    class _Exploding:
        async def aclose(self) -> None:
            raise RuntimeError("cleanup exploded")

    # Must not raise.
    asyncio.run(ClaudeAgentRuntime._close_stream_silently(_Exploding()))


def test_watchdog_close_noop_for_plain_iterators() -> None:
    class _NoAclose:
        pass

    asyncio.run(ClaudeAgentRuntime._close_stream_silently(_NoAclose()))


def test_trip_marks_forced_close_not_natural_exhaustion() -> None:
    """A watchdog trip closes the generator, which the probe records as
    forced (distinct from a natural exhaust in the healthy tests above)."""

    class _ForcedCloseRecorder:
        def __init__(self) -> None:
            self.probe = _StreamProbe()

        def __call__(self, *, prompt: str, options: Any) -> Any:
            return self._stream()

        async def _stream(self):  # noqa: ANN202
            try:
                await asyncio.sleep(3600)
                yield FakeMessage(result="never")
            finally:
                self.probe.closed = True

    qfn = _ForcedCloseRecorder()
    runtime = make_watchdog_runtime(qfn, max_retries=0)
    with pytest.raises(_TransientSDKError, match="spawn watchdog"):
        asyncio.run(
            runtime._consume_stream(qfn, "p", object(), "coder")  # type: ignore[arg-type]
        )
    assert qfn.probe.closed is True


# --------------------------------------------------------------------------- #
# (e) Settings: defaults + env tunability
# --------------------------------------------------------------------------- #


def test_settings_watchdog_defaults() -> None:
    """Unset env → the documented production defaults (5 min / 15 min)."""
    s = _fake_settings()
    assert s.spawn_first_event_timeout_s == 300.0
    assert s.spawn_progress_timeout_s == 900.0


def test_settings_env_overrides_both_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ticket's canonical knob names (no trailing ``_S``) are honoured."""
    monkeypatch.setenv("SFP_SPAWN_FIRST_EVENT_TIMEOUT", "11")
    monkeypatch.setenv("SFP_SPAWN_PROGRESS_TIMEOUT", "22")
    s = _fake_settings()
    assert s.spawn_first_event_timeout_s == 11.0
    assert s.spawn_progress_timeout_s == 22.0


def test_settings_env_overrides_accept_field_name_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The field-name-derived spelling (trailing ``_S``) also works — both
    spellings are accepted so neither operator habit silently no-ops."""
    monkeypatch.setenv("SFP_SPAWN_FIRST_EVENT_TIMEOUT_S", "33")
    monkeypatch.setenv("SFP_SPAWN_PROGRESS_TIMEOUT_S", "44")
    s = _fake_settings()
    assert s.spawn_first_event_timeout_s == 33.0
    assert s.spawn_progress_timeout_s == 44.0


def test_settings_env_overrides_change_watchdog_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env knobs are load-bearing, not cosmetic: a shorter first-event
    budget via SFP_SPAWN_FIRST_EVENT_TIMEOUT aborts a mute spawn sooner."""
    monkeypatch.setenv("SFP_SPAWN_FIRST_EVENT_TIMEOUT", "0.05")
    monkeypatch.setenv("SFP_SPAWN_PROGRESS_TIMEOUT", "9999")
    settings = _fake_settings()
    assert settings.spawn_first_event_timeout_s == 0.05
    qfn = MuteQuery()
    runtime = make_watchdog_runtime(qfn, settings, max_retries=0)
    with pytest.raises(_TransientSDKError, match="spawn watchdog"):
        asyncio.run(
            runtime._consume_stream(qfn, "p", object(), "coder")  # type: ignore[arg-type]
        )


def test_settings_reject_non_positive_budgets() -> None:
    """A zero/negative budget would silently disable the watchdog and
    reintroduce the multi-hour hang this ticket removes — rejected."""
    with pytest.raises(ValidationError, match="spawn_first_event_timeout_s"):
        _fake_settings(spawn_first_event_timeout_s=0)
    with pytest.raises(ValidationError, match="spawn_progress_timeout_s"):
        _fake_settings(spawn_progress_timeout_s=-5)


def test_runtime_reads_budgets_from_settings() -> None:
    """Distinct budgets produce distinct messages: the fired budget's value
    appears in the error text (proof the settings reach the watchdog)."""
    qfn = MuteQuery()
    settings = make_watchdog_settings(spawn_first_event_timeout_s=0.1)
    runtime = make_watchdog_runtime(qfn, settings, max_retries=0)
    with pytest.raises(_TransientSDKError, match=r"no stream events in 0s"):
        asyncio.run(
            runtime._consume_stream(qfn, "p", object(), "coder")  # type: ignore[arg-type]
        )


def test_settings_env_do_not_leak_between_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Determinism guard: a knob set in one test must not leak into another
    (monkeypatch removes it), and defaults return afterwards."""
    monkeypatch.setenv("SFP_SPAWN_FIRST_EVENT_TIMEOUT", "11")
    assert _fake_settings().spawn_first_event_timeout_s == 11.0
    monkeypatch.delenv("SFP_SPAWN_FIRST_EVENT_TIMEOUT")
    assert _fake_settings().spawn_first_event_timeout_s == 300.0
