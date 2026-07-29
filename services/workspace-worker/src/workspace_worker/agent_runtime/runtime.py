"""Concrete AgentRuntime adapter for the Claude Agent SDK (SFP-36 / Jira SFP-53).

This is the one place above the :mod:`sfp_agent_runtime` seam that touches a
vendor SDK (AP-010 / MAS §9.6). The SDK is imported **lazily** — never at module
import and never at construction — so unit tests inject a fake ``query_fn`` (an
async generator yielding a final ``ResultMessage``-like object) and never spawn
the Claude Code CLI subprocess that the real :func:`claude_agent_sdk.query`
drives. Importing the SDK's dataclass types (``ClaudeAgentOptions``) does NOT
spawn the CLI, so options construction is safe in tests; only ``query()`` does.

Verified ``claude-agent-sdk`` contract (introspected v0.2.128, encoded in the
SFP-36 doc):
- ``query(*, prompt, options=None, transport=None)`` is an async generator; the
  FINAL ``ResultMessage`` carries ``result: str | None`` (the agent's text),
  ``is_error: bool``, ``errors: list[str] | None``, ``api_error_status: int |
  None``.
- ``ClaudeAgentOptions(model, env)`` — provider routing + credentials are
  injected via ``options.env`` (``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_AUTH_TOKEN``)
  per ID-020. There is NO ``base_url``/``api_key``/``timeout`` option.

The landed :class:`~sfp_agent_runtime.interfaces.AgentRuntime.run` is sync; the
SDK is async, so ``run`` bridges via :func:`asyncio.run`.

Retry policy (tenacity, SFP-36 binding decision): ONLY transient failures
retried — ``TRANSIENT_EXCEPTIONS`` (``_TransientSDKError`` for empty streams and
5xx/429 ``api_error_status``, plus ``claude_agent_sdk.CLIConnectionError`` once
the SDK is imported). Non-conformant output / ``is_error`` / non-JSON are HARD
rejects (not retried). Fail-closed: any unhandled exception becomes
``AgentRunResult(success=False, ...)``; no exception escapes ``run()``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_config import SecretProvider, SecretResolutionError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from workspace_worker.infrastructure.settings import WorkspaceWorkerSettings

__all__ = ["ClaudeAgentRuntime"]


class _TransientSDKError(Exception):
    """Internal signal: a transient (retryable) SDK / transport failure.

    Raised when the SDK surfaces an infrastructure-grade condition that warrants
    a retry — an empty message stream, or a transient (5xx / 429 rate-limit)
    ``api_error_status`` on the final ``ResultMessage``. This is distinct from a
    hard output error (``is_error`` / non-JSON / contract mismatch), which is
    NOT retried.
    """


#: ``api_error_status`` values treated as transient (retryable). 429 is the
#: rate-limit; 5xx are upstream/server faults. Anything else reaching the
#: hard-reject path is an output error, not infrastructure.
_TRANSIENT_API_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504, 529})

#: Async sleep callable used between retry attempts; tests replace it with a
#: no-op so retries are instantaneous (no real waiting).
SleepFn = Callable[[float], Awaitable[None]]

#: Duck-typed SDK query entry point: an async-generator factory. The adapter
#: consumes its stream and inspects only the final ``ResultMessage``.
QueryFn = Callable[..., AsyncIterator[Any]]


def _failure(request: AgentRunRequest, message: str) -> AgentRunResult:
    """Build a fail-closed result carrying ``agent``/``ticket_id`` from the request."""
    return AgentRunResult(
        agent=request.agent,
        ticket_id=request.ticket_id,
        success=False,
        error=message,
    )


class ClaudeAgentRuntime:
    """AgentRuntime backed by the Claude Agent SDK.

    Provider routing and credentials are injected through ``options.env``
    (``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_AUTH_TOKEN``) per ID-020; the SDK has
    no base_url/api_key options. The auth token is resolved ONCE per run,
    outside the retry loop, via the injected :class:`SecretProvider`.

    Args:
        settings: Typed worker settings (endpoint, model, secret ref).
        secret_provider: Resolves :attr:`settings.llm_provider_secret_ref` to
            the raw token. Called exactly once per :meth:`run`.
        output_contract: A pydantic ``BaseModel`` subclass (one of SFP-13…18)
            used to validate the agent's parsed JSON output.
        query_fn: Injectable async-generator factory; defaults to a LAZY import
            of :func:`claude_agent_sdk.query` (the import happens on the first
            :meth:`run`, never at construction — so the SDK is absent from
            ``sys.modules`` until a run needs it).
        max_retries: Max number of RETRIES after the first attempt (so the run
            attempts at most ``max_retries + 1`` times). Default 3.
        sleep: Async sleep callable used by the retry loop (test hook).
    """

    #: Exceptions that trigger a retry. ``_TransientSDKError`` covers SDK
    #: 5xx/rate-limit results and empty streams. ``CLIConnectionError`` is
    #: added lazily once the SDK is imported; tests inject a fake ``query_fn``
    #: and never import the SDK, so this base set is all tests need to classify
    #: transient vs non-transient. Declared as a plain class attribute (not
    #: ClassVar) so the lazy default path can shadow it per-instance.
    TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (_TransientSDKError,)

    def __init__(
        self,
        settings: WorkspaceWorkerSettings,
        secret_provider: SecretProvider,
        output_contract: type[BaseModel],
        *,
        query_fn: QueryFn | None = None,
        max_retries: int = 3,
        sleep: SleepFn | None = None,
    ) -> None:
        self._settings = settings
        self._secret_provider = secret_provider
        self._output_contract = output_contract
        self._query_fn: QueryFn | None = query_fn
        self._max_retries = max_retries
        self._sleep: SleepFn = sleep if sleep is not None else asyncio.sleep

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        """Run the agent; never raises (fail-closed on any unhandled error)."""
        # 1. Resolve the secret ONCE, outside the retry loop. query_fn is NEVER
        #    called when resolution fails.
        try:
            token = self._secret_provider.resolve(self._settings.llm_provider_secret_ref)
        except SecretResolutionError as exc:
            return _failure(request, f"secret resolution failed: {exc}")

        # 2. Build options — routing + auth live in options.env (ID-020).
        options = self._build_options(token)

        # 3-8. Stream + parse + validate under the retry policy, bridging async.
        try:
            return asyncio.run(self._run_async(request, options))
        except Exception as exc:  # noqa: BLE001 — fail-closed on anything
            return _failure(request, f"{type(exc).__name__}: {exc}")

    # -- internals ---------------------------------------------------------

    def _build_options(self, token: str) -> Any:
        """Build ``ClaudeAgentOptions`` with routing + auth in ``env`` (ID-020).

        Importing the ``ClaudeAgentOptions`` dataclass does NOT spawn the CLI
        (only ``query()`` does), so this is safe to exercise in tests.
        """
        from claude_agent_sdk import ClaudeAgentOptions

        env: dict[str, str] = {
            "ANTHROPIC_BASE_URL": self._settings.anthropic_base_url,
            "ANTHROPIC_AUTH_TOKEN": token,
        }
        if self._settings.extra_env:
            # Merged AFTER the routing/auth entries; duplicate keys here would
            # overwrite ANTHROPIC_*, but extra_env is operator-controlled config.
            env.update(self._settings.extra_env)
        return ClaudeAgentOptions(model=self._settings.default_model, env=env)

    async def _run_async(self, request: AgentRunRequest, options: Any) -> AgentRunResult:
        query_fn = self._resolve_query_fn()
        # The retryer wraps ONLY stream consumption — transient failures
        # (CLIConnectionError / 5xx / rate-limit / empty stream) are retried;
        # the hard-reject checks below run once, after a clean consume.
        final_message: Any = await self._retryer()(
            self._consume_stream, query_fn, request.prompt, options
        )

        # 4. Hard-reject an SDK error or missing result (NOT retried).
        is_error = bool(getattr(final_message, "is_error", False))
        result_text = getattr(final_message, "result", None)
        if is_error or result_text is None:
            errors = getattr(final_message, "errors", None) or []
            detail = (
                "; ".join(str(e) for e in errors) if errors else "agent produced no usable result"
            )
            return _failure(request, f"agent run errored: {detail}")

        # 5. Parse JSON (hard reject, no retry).
        try:
            parsed = json.loads(result_text)
        except json.JSONDecodeError as exc:
            return _failure(request, f"agent output was not valid JSON: {exc}")

        # 6. Validate against the injected output contract (hard reject, no retry).
        try:
            self._output_contract.model_validate(parsed)
        except ValidationError as exc:
            return _failure(request, f"agent output failed contract validation: {exc}")

        # 7. Success — agent/ticket_id come from the REQUEST, never parsed
        #    output (anti-spoof). ``output`` is the raw parsed JSON.
        return AgentRunResult(
            agent=request.agent,
            ticket_id=request.ticket_id,
            success=True,
            output=parsed,
        )

    async def _consume_stream(self, query_fn: QueryFn, prompt: str, options: Any) -> Any:
        """Consume the SDK stream and return the final ``ResultMessage``.

        Raises :class:`_TransientSDKError` (driving a retry) on an empty stream
        or a transient (5xx/429) ``api_error_status``. Any other condition is
        surfaced to :meth:`_run_async` for a hard-reject decision.
        """
        final_message: Any = None
        async for message in query_fn(prompt=prompt, options=options):
            final_message = message

        if final_message is None:
            raise _TransientSDKError("agent produced no messages")

        status = getattr(final_message, "api_error_status", None)
        if status is not None and status in _TRANSIENT_API_STATUSES:
            raise _TransientSDKError(f"transient api_error_status={status}")

        return final_message

    def _retryer(self) -> AsyncRetrying:
        """Tenacity retryer: transient-only, exponential backoff, reraise."""
        return AsyncRetrying(
            sleep=self._sleep,
            stop=stop_after_attempt(self._max_retries + 1),
            wait=wait_exponential(multiplier=1, max=10.0),
            retry=retry_if_exception_type(self.TRANSIENT_EXCEPTIONS),
            reraise=True,
        )

    def _resolve_query_fn(self) -> QueryFn:
        """Return the injected query_fn, or the lazy SDK default."""
        if self._query_fn is not None:
            return self._query_fn
        self._query_fn = self._default_query_fn()
        return self._query_fn

    def _default_query_fn(self) -> QueryFn:
        """Lazy-import the SDK query entry point.

        Importing the SDK does not spawn the CLI; only calling ``query()`` does.
        While here, union ``CLIConnectionError`` into this instance's transient
        set so the retryer also retries CLI connection failures. The real call
        path (``query()`` spawning the CLI) is not exercised in CI — tests
        inject ``query_fn`` — but resolving the entry point is CLI-free.
        """
        import claude_agent_sdk

        cli_error = getattr(claude_agent_sdk, "CLIConnectionError", None)
        if cli_error is not None and cli_error not in self.TRANSIENT_EXCEPTIONS:
            # Shadow the class attribute on this instance only.
            self.TRANSIENT_EXCEPTIONS = (*self.TRANSIENT_EXCEPTIONS, cli_error)
        return claude_agent_sdk.query
