"""Tests for ClaudeAgentRuntime + WorkspaceWorkerSettings (SFP-36 / Jira SFP-53).

Covers the binding decisions encoded in the SFP-36 doc:
- success path: valid JSON conforming to the output contract.
- hard rejects (NOT retried, query_fn called once): ``is_error`` / missing
  result, non-JSON result, non-conformant output (missing/extra/wrong-type).
- transient failures retried up to ``max_retries + 1`` attempts; succeeds-on-
  third-attempt; non-transient exception fails immediately (called once).
- secret resolved once per run outside the retry loop; resolution failure →
  query_fn never called; the resolved token is forwarded into
  ``options.env["ANTHROPIC_AUTH_TOKEN"]`` and never appears in error/result text.
- anti-spoof: ``agent``/``ticket_id`` come from the request, not parsed output.
- laziness: the SDK is not imported at construction (``query_fn=None``).
- WorkspaceWorkerSettings startup validation (ID-020).

The fake ``query_fn`` is an async generator yielding a small stand-in final
message (the adapter duck-types ``result``/``is_error``/``errors``/
``api_error_status``). The test module does NOT import ``claude_agent_sdk`` so
the laziness assertions stay robust to test ordering.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from sfp_agent_runtime.interfaces import AgentRunRequest
from sfp_config import SecretRef, SecretResolutionError
from workspace_worker.agent_runtime.model_config import AgentModelConfig
from workspace_worker.agent_runtime.runtime import (
    ClaudeAgentRuntime,
    _TransientSDKError,
)
from workspace_worker.infrastructure.settings import WorkspaceWorkerSettings

# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

SECRET_VALUE = "super-secret-token-DO-NOT-LEAK"


async def _noop_sleep(_seconds: float) -> None:
    """Async no-op sleep so retries are instantaneous in tests."""
    return None


@dataclass
class FakeMessage:
    """Minimal stand-in for ``claude_agent_sdk.ResultMessage``.

    The adapter only inspects ``result``/``is_error``/``errors``/
    ``api_error_status``, so a stand-in is sufficient and keeps this test module
    free of any ``claude_agent_sdk`` import (preserving the laziness invariant).
    """

    result: str | None = None
    is_error: bool = False
    errors: list[str] | None = None
    api_error_status: int | None = None


@dataclass
class FakeQuery:
    """An async-generator ``query_fn`` factory with call counting.

    ``messages_factory`` is called per attempt and must return the list of
    messages to yield for that attempt (usually a single final FakeMessage). To
    simulate a raise, set ``raises`` to an exception type/instance per attempt
    via a list of outcomes.
    """

    outcomes: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, *, prompt: str, options: Any) -> Any:
        self.calls.append({"prompt": prompt, "options": options})
        return self._stream(prompt, options)

    async def _stream(self, prompt: str, options: Any):  # noqa: ANN202 - async gen
        idx = len(self.calls) - 1
        outcome = self.outcomes[idx] if idx < len(self.outcomes) else self.outcomes[-1]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, type) and issubclass(outcome, BaseException):
            raise outcome("transient-failure")
        # Otherwise outcome is a list of messages to yield.
        for message in outcome:
            yield message


class FakeSecretProvider:
    """In-memory SecretProvider returning a fixed token (or raising)."""

    def __init__(self, value: str = SECRET_VALUE, fail: bool = False) -> None:
        self._value = value
        self._fail = fail
        self.call_count = 0

    def resolve(self, ref: SecretRef) -> str:
        self.call_count += 1
        if self._fail:
            raise SecretResolutionError(ref, source="fake")
        return self._value


class OutputContract(BaseModel):
    """A small contract mirroring SFP-13…18: strict, ``extra='forbid'``."""

    model_config = ConfigDict(extra="forbid")
    answer: str


def make_settings(**overrides: Any) -> WorkspaceWorkerSettings:
    base: dict[str, Any] = {
        "anthropic_base_url": "https://api.example.com",
        "default_model": "claude-sonnet-4",
        "llm_provider_secret_ref": SecretRef(name="llm/token"),
    }
    base.update(overrides)
    return WorkspaceWorkerSettings(**base)


def make_runtime(
    query_fn: Any | None,
    *,
    max_retries: int = 2,
    max_turns: int | None = None,
    cwd: str | None = None,
    provider: FakeSecretProvider | None = None,
    settings: WorkspaceWorkerSettings | None = None,
    model_resolver: AgentModelConfig | None = None,
) -> ClaudeAgentRuntime:
    kwargs: dict[str, Any] = {
        "query_fn": query_fn,
        "max_retries": max_retries,
        "sleep": _noop_sleep,
        "model_resolver": model_resolver,
    }
    if max_turns is not None:
        kwargs["max_turns"] = max_turns
    if cwd is not None:
        kwargs["cwd"] = cwd
    return ClaudeAgentRuntime(
        settings or make_settings(),
        provider or FakeSecretProvider(),
        OutputContract,
        **kwargs,
    )


def request(agent: str = "planner", ticket_id: str = "SFP-1") -> AgentRunRequest:
    return AgentRunRequest(agent=agent, ticket_id=ticket_id, prompt="do it")


# --------------------------------------------------------------------------- #
# Success
# --------------------------------------------------------------------------- #


def test_success_valid_json_conforms_to_contract() -> None:
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "42"}')]])
    rt = make_runtime(qfn)

    res = rt.run(request())

    assert res.success is True
    assert res.error is None
    assert res.output == {"answer": "42"}
    assert len(qfn.calls) == 1
    # secret resolved exactly once per run
    assert rt._secret_provider.call_count == 1  # type: ignore[attr-defined]


def test_success_forwards_routing_and_auth_into_env() -> None:
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    rt = make_runtime(qfn)

    rt.run(request())

    options = qfn.calls[0]["options"]
    assert options.env["ANTHROPIC_BASE_URL"] == "https://api.example.com"
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == SECRET_VALUE
    assert options.model == "claude-sonnet-4"


def test_extra_env_merged_into_options_env() -> None:
    settings = make_settings(extra_env={"EXTRA_FLAG": "1"})
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    rt = make_runtime(qfn, settings=settings)

    rt.run(request())

    env = qfn.calls[0]["options"].env
    assert env["EXTRA_FLAG"] == "1"
    # routing + auth still present alongside the extra entry
    assert env["ANTHROPIC_BASE_URL"] == "https://api.example.com"
    assert env["ANTHROPIC_AUTH_TOKEN"] == SECRET_VALUE


def test_agent_and_ticket_id_come_from_request_not_output() -> None:
    # The output carries its own "agent" field (contract-permitted here); the
    # runtime must take agent/ticket_id from the REQUEST, never parsed output.
    class SpoofableContract(BaseModel):
        model_config = ConfigDict(extra="forbid")
        answer: str
        agent: str  # output-supplied; runtime must ignore it

    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x", "agent": "evil"}')]])
    rt = ClaudeAgentRuntime(
        make_settings(),
        FakeSecretProvider(),
        SpoofableContract,
        query_fn=qfn,
        max_retries=2,
        sleep=_noop_sleep,
    )

    res = rt.run(request(agent="planner", ticket_id="SFP-99"))

    assert res.success is True
    assert res.agent == "planner"  # from request, NOT parsed "evil"
    assert res.ticket_id == "SFP-99"
    assert res.output == {"answer": "x", "agent": "evil"}


# --------------------------------------------------------------------------- #
# Hard rejects (no retry, query_fn called once)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "result_json",
    [
        '{"answer": "x", "unexpected": 1}',  # extra field (extra='forbid')
        "{}",  # missing required field
        '{"answer": 123}',  # wrong type
    ],
)
def test_non_conformant_output_hard_rejected_once(result_json: str) -> None:
    qfn = FakeQuery(outcomes=[[FakeMessage(result=result_json)]])
    rt = make_runtime(qfn, max_retries=3)

    res = rt.run(request())

    assert res.success is False
    assert res.error is not None
    assert "contract validation" in res.error
    assert len(qfn.calls) == 1  # NOT retried


def test_sdk_error_hard_rejected_once() -> None:
    qfn = FakeQuery(outcomes=[[FakeMessage(is_error=True, result=None, errors=["model refused"])]])
    rt = make_runtime(qfn, max_retries=3)

    res = rt.run(request())

    assert res.success is False
    assert res.error is not None
    assert "model refused" in res.error
    assert len(qfn.calls) == 1


def test_null_result_hard_rejected_once() -> None:
    qfn = FakeQuery(outcomes=[[FakeMessage(result=None, is_error=False)]])
    rt = make_runtime(qfn, max_retries=3)

    res = rt.run(request())

    assert res.success is False
    assert len(qfn.calls) == 1


def test_non_json_result_hard_rejected_once() -> None:
    qfn = FakeQuery(outcomes=[[FakeMessage(result="not-json{")]])
    rt = make_runtime(qfn, max_retries=3)

    res = rt.run(request())

    assert res.success is False
    assert res.error is not None
    assert "not valid JSON" in res.error
    assert len(qfn.calls) == 1


# --------------------------------------------------------------------------- #
# Retry behavior
# --------------------------------------------------------------------------- #


def test_transient_exhausts_retries_then_fails() -> None:
    # All attempts raise a transient exception.
    qfn = FakeQuery(outcomes=[_TransientSDKError, _TransientSDKError, _TransientSDKError])
    rt = make_runtime(qfn, max_retries=2)  # -> 3 attempts max

    res = rt.run(request())

    assert res.success is False
    assert res.error is not None
    # max_retries + 1 attempts
    assert len(qfn.calls) == 3


def test_transient_succeeds_on_third_attempt() -> None:
    qfn = FakeQuery(
        outcomes=[
            _TransientSDKError,
            _TransientSDKError,
            [FakeMessage(result='{"answer": "ok"}')],
        ]
    )
    rt = make_runtime(qfn, max_retries=2)

    res = rt.run(request())

    assert res.success is True
    assert res.output == {"answer": "ok"}
    assert len(qfn.calls) == 3


def test_transient_api_status_is_retried() -> None:
    # A 503 on the final message is a transient result -> retried.
    qfn = FakeQuery(
        outcomes=[
            [FakeMessage(result="ignored", api_error_status=503)],
            [FakeMessage(result='{"answer": "ok"}')],
        ]
    )
    rt = make_runtime(qfn, max_retries=2)

    res = rt.run(request())

    assert res.success is True
    assert len(qfn.calls) == 2


def test_anthropic_overloaded_529_is_retried() -> None:
    # 529 (Anthropic "Overloaded") is retryable per the SDK's own
    # ResultMessage.api_error_status docstring -> retried, not hard-rejected.
    qfn = FakeQuery(
        outcomes=[
            [FakeMessage(result="ignored", api_error_status=529)],
            [FakeMessage(result='{"answer": "ok"}')],
        ]
    )
    rt = make_runtime(qfn, max_retries=2)

    res = rt.run(request())

    assert res.success is True
    assert len(qfn.calls) == 2


def test_empty_stream_is_transient_and_retried() -> None:
    # A stream that yields nothing is a transient failure -> retried.
    qfn = FakeQuery(
        outcomes=[
            [],  # empty stream
            [],  # empty stream
            [FakeMessage(result='{"answer": "ok"}')],
        ]
    )
    rt = make_runtime(qfn, max_retries=2)

    res = rt.run(request())

    assert res.success is True
    assert res.output == {"answer": "ok"}
    assert len(qfn.calls) == 3


def test_non_transient_exception_fails_immediately() -> None:
    qfn = FakeQuery(outcomes=[RuntimeError])
    rt = make_runtime(qfn, max_retries=3)

    res = rt.run(request())

    assert res.success is False
    assert res.error is not None
    assert "RuntimeError" in res.error
    assert len(qfn.calls) == 1  # non-transient -> not retried


def test_transient_exceptions_class_attribute_exposed() -> None:
    # Tests (and callers) classify via this public attribute.
    assert _TransientSDKError in ClaudeAgentRuntime.TRANSIENT_EXCEPTIONS


def test_retry_does_not_real_sleep() -> None:
    # The injected sleep hook must be used; assert no real asyncio.sleep occurred
    # by capturing the wait durations the hook saw.
    seen: list[float] = []

    async def _capturing_sleep(seconds: float) -> None:
        seen.append(seconds)

    qfn = FakeQuery(outcomes=[_TransientSDKError, _TransientSDKError, _TransientSDKError])
    rt = ClaudeAgentRuntime(
        make_settings(),
        FakeSecretProvider(),
        OutputContract,
        query_fn=qfn,
        max_retries=2,
        sleep=_capturing_sleep,
    )

    rt.run(request())

    # Two retries -> two (non-real) sleeps with positive exponential waits.
    assert len(seen) == 2
    assert all(w > 0 for w in seen)


# --------------------------------------------------------------------------- #
# Secret resolution
# --------------------------------------------------------------------------- #


def test_secret_resolution_failure_skips_query_fn() -> None:
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    rt = make_runtime(qfn, provider=FakeSecretProvider(fail=True))

    res = rt.run(request())

    assert res.success is False
    assert res.error is not None
    assert "secret resolution failed" in res.error
    assert len(qfn.calls) == 0  # query_fn NEVER called


def test_secret_not_leaked_into_error_text() -> None:
    # Force a hard reject; the error string must not contain the raw token.
    qfn = FakeQuery(outcomes=[[FakeMessage(result="not-json{")]])
    rt = make_runtime(qfn)

    res = rt.run(request())

    assert res.success is False
    assert res.error is not None
    assert SECRET_VALUE not in res.error
    assert res.output is None


def test_secret_not_leaked_into_transient_failure_text() -> None:
    qfn = FakeQuery(outcomes=[_TransientSDKError, _TransientSDKError, _TransientSDKError])
    rt = make_runtime(qfn, max_retries=2)

    res = rt.run(request())

    assert res.success is False
    assert res.error is not None
    assert SECRET_VALUE not in res.error


# --------------------------------------------------------------------------- #
# Laziness
# --------------------------------------------------------------------------- #


def test_construction_does_not_import_sdk() -> None:
    # Asserted in a pristine subprocess so sys.modules is uncontaminated by
    # other tests (importing the SDK anywhere pollutes the process-global set).
    code = (
        "import sys\n"
        "from sfp_config import SecretRef\n"
        "from workspace_worker.agent_runtime.runtime import ClaudeAgentRuntime\n"
        "from workspace_worker.infrastructure.settings import WorkspaceWorkerSettings\n"
        "s = WorkspaceWorkerSettings(\n"
        "    anthropic_base_url='https://api.example.com',\n"
        "    default_model='claude-sonnet-4',\n"
        "    llm_provider_secret_ref=SecretRef(name='llm/token'),\n"
        ")\n"
        "ClaudeAgentRuntime(s, object(), object(), query_fn=None)\n"
        "assert 'claude_agent_sdk' not in sys.modules, 'SDK imported at construction'\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ},
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_default_query_fn_lazy_imports_sdk_and_unions_cli_error() -> None:
    # Directly exercising _resolve_query_fn does NOT spawn the CLI (only query()
    # does); it imports the SDK and augments the instance's transient set.
    rt = make_runtime(None)  # query_fn=None
    original = rt.TRANSIENT_EXCEPTIONS

    resolved = rt._resolve_query_fn()  # type: ignore[attr-defined]

    import claude_agent_sdk

    assert resolved is claude_agent_sdk.query
    # CLIConnectionError (present per introspected contract) added to instance.
    if hasattr(claude_agent_sdk, "CLIConnectionError"):
        assert claude_agent_sdk.CLIConnectionError in rt.TRANSIENT_EXCEPTIONS
        assert claude_agent_sdk.CLIConnectionError not in original


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_equal_inputs_yield_equal_results() -> None:
    def build() -> ClaudeAgentRuntime:
        return make_runtime(FakeQuery(outcomes=[[FakeMessage(result='{"answer": "v"}')]]))

    r1 = build().run(request())
    r2 = build().run(request())

    assert r1 == r2


# --------------------------------------------------------------------------- #
# WorkspaceWorkerSettings startup validation (ID-020)
# --------------------------------------------------------------------------- #


def test_settings_ok_when_all_fields_present() -> None:
    s = make_settings()
    assert s.anthropic_base_url == "https://api.example.com"
    assert s.default_model == "claude-sonnet-4"
    assert s.llm_provider_secret_ref == SecretRef(name="llm/token")
    assert s.extra_env == {}


@pytest.mark.parametrize("bad_url", ["", "   ", "\t\n"])
def test_settings_reject_blank_base_url(bad_url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(anthropic_base_url=bad_url)


@pytest.mark.parametrize("bad_model", ["", "   "])
def test_settings_reject_blank_default_model(bad_model: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(default_model=bad_model)


def test_settings_reject_non_secret_ref() -> None:
    with pytest.raises(ValidationError):
        # A bare string is not coercible to SecretRef.
        WorkspaceWorkerSettings(
            anthropic_base_url="https://api.example.com",
            default_model="claude-sonnet-4",
            llm_provider_secret_ref="not-a-secret-ref",  # type: ignore[arg-type]
        )


def test_settings_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkspaceWorkerSettings(  # type: ignore[call-arg]
            anthropic_base_url="https://api.example.com",
            default_model="claude-sonnet-4",
        )


# --------------------------------------------------------------------------- #
# Per-role model routing (SFP-37 / Jira SFP-54)
# --------------------------------------------------------------------------- #


def _resolver(**overrides: Any) -> AgentModelConfig:
    """Build an AgentModelConfig with a distinct global default + overrides."""
    base: dict[str, Any] = {"default_model": "global-model"}
    base.update(overrides)
    return AgentModelConfig(**base)


@pytest.mark.parametrize(
    "agent,expected",
    [("planner", "planner-x"), ("coder", "coder-x"), ("reviewer", "reviewer-x")],
)
def test_injected_resolver_drives_per_role_options_model(agent: str, expected: str) -> None:
    resolver = _resolver(planner="planner-x", coder="coder-x", reviewer="reviewer-x")
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    rt = make_runtime(qfn, model_resolver=resolver)

    rt.run(request(agent=agent))

    assert qfn.calls[0]["options"].model == expected


def test_resolver_unknown_role_uses_resolver_default_not_settings_default() -> None:
    # readiness is a real role in use but NOT in ROLES_WITH_OVERRIDE → the
    # resolver's default_model wins, which is DISTINCT from settings.default_model.
    resolver = _resolver(planner="planner-x")  # default_model="global-model"
    settings = make_settings(default_model="claude-sonnet-4")
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    rt = make_runtime(qfn, settings=settings, model_resolver=resolver)

    rt.run(request(agent="readiness"))

    assert qfn.calls[0]["options"].model == "global-model"
    assert qfn.calls[0]["options"].model != settings.default_model


def test_no_resolver_falls_back_to_settings_default_model() -> None:
    # Back-compat: model_resolver=None (the default) → options.model is exactly
    # settings.default_model, unchanged from SFP-36.
    settings = make_settings(default_model="claude-sonnet-4")
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    rt = make_runtime(qfn, settings=settings)  # model_resolver=None

    rt.run(request(agent="planner"))

    assert qfn.calls[0]["options"].model == "claude-sonnet-4"
    assert qfn.calls[0]["options"].model == settings.default_model


def test_whitespace_padded_override_strips_through_runtime() -> None:
    # The validator strips on construction; the stripped id rides into options.model.
    resolver = _resolver(planner="  planner-x  ")
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    rt = make_runtime(qfn, model_resolver=resolver)

    rt.run(request(agent="planner"))

    assert qfn.calls[0]["options"].model == "planner-x"


def test_resolver_case_insensitive_through_runtime() -> None:
    resolver = _resolver(planner="planner-x")
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    rt = make_runtime(qfn, model_resolver=resolver)

    rt.run(request(agent="PLANNER"))

    assert qfn.calls[0]["options"].model == "planner-x"


def test_fenced_json_is_parsed() -> None:
    """ID-019: JSON wrapped in a ```json fence is parsed (fence stripped)."""
    qfn = FakeQuery(outcomes=[[FakeMessage(result='```json\n{"answer": "42"}\n```')]])
    res = make_runtime(qfn).run(request())
    assert res.success is True
    assert res.output == {"answer": "42"}


def test_max_turns_default_forwarded_into_options() -> None:
    """max_turns default (50) is forwarded into ClaudeAgentOptions."""
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    make_runtime(qfn).run(request())
    assert qfn.calls[0]["options"].max_turns == 50


def test_explicit_max_turns_forwarded_into_options() -> None:
    """An explicit max_turns overrides the default in options."""
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    make_runtime(qfn, max_turns=3).run(request())
    assert qfn.calls[0]["options"].max_turns == 3


def test_cwd_forwarded_into_options() -> None:
    """cwd is forwarded into ClaudeAgentOptions (run in a worktree)."""
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    make_runtime(qfn, cwd="/tmp/worktree").run(request())
    assert qfn.calls[0]["options"].cwd == "/tmp/worktree"


def test_cwd_default_none_in_options() -> None:
    """cwd defaults to None (no worktree override)."""
    qfn = FakeQuery(outcomes=[[FakeMessage(result='{"answer": "x"}')]])
    make_runtime(qfn).run(request())
    assert qfn.calls[0]["options"].cwd is None
