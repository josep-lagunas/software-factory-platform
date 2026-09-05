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

Stream liveness watchdogs (SFP-242): the SDK's ``query()`` spawns the Claude
Code CLI and yields its stream. When the endpoint goes mute the CLI stays up and
the run hangs for hours with zero events. Two budgets bound that — a
first-event budget (default 300s) and a between-events inactivity budget
(default 900s), both env-tunable via ``WorkspaceWorkerSettings``
(``SFP_SPAWN_FIRST_EVENT_TIMEOUT`` / ``SFP_SPAWN_PROGRESS_TIMEOUT``). A trip
closes the stream — on the real path that runs the SDK's own terminate→kill
escalation, reaping the CLI — and raises the EXISTING ``_TransientSDKError``
with a watchdog-naming message, so the caller's abort/retry path is unchanged.
Timeouts are measured with :func:`time.monotonic` (MAS §12.7 — never wall
clock).
"""

from __future__ import annotations

import asyncio
import json
import time
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

from workspace_worker.agent_runtime.model_config import AgentModelConfig
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

#: Watchdog stage names used in the raised message (SFP-242). "spawn" is the
#: budget covering the FIRST event after the CLI is spawned; "progress" is the
#: between-events inactivity budget once streaming has started.
_FIRST_EVENT_STAGE = "spawn"
_PROGRESS_STAGE = "progress"

#: Upper bound (s) on stream cleanup after a watchdog trip before the error is
#: raised. Generous vs. the SDK's own close() escalation (~20s worst case);
#: only a generator that ignores cancellation can reach it.
_WATCHDOG_CLOSE_GRACE_S = 30.0

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


def _extract_json(text: str) -> str:
    """Return ``text`` with any surrounding markdown fence stripped.

    Anthropic-compatible providers (e.g. the GLM endpoint, per ID-019) often
    wrap a JSON result in a `````json ... ````` fence despite the prompt asking
    for raw JSON. The runtime parses ``ResultMessage.result`` as JSON; this
    helper makes that parse robust to such wrapping without changing the
    contract. If ``text`` is not fenced, it is returned unchanged (stripped of
    surrounding whitespace). Non-fenced, non-JSON input still raises below.
    """
    s = text.strip()
    if s.startswith("```"):
        # Drop the opening fence line (``` or ```json / ```JSON).
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        elif s.endswith("```"):
            s = ""  # degenerate "```" only
        # Drop a trailing closing fence.
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


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
        max_turns: Bound on the agent's agentic turns, forwarded to
            ``ClaudeAgentOptions(max_turns=...)``. Bounds run-away agent loops
            (and cost) and forces a clean finalization so a run does not return
            an empty ``ResultMessage.result`` (ID-019 empirical finding). Default
            50 — enough for a multi-step Coder run, bounded enough to fail-fast;
            the composition root may override per agent.
        sleep: Async sleep callable used by the retry loop (test hook).
        model_resolver: Optional per-role model router (SFP-37 / Jira SFP-54).
            When present, ``run()`` resolves the model for each request via
            ``model_resolver.resolve(request.agent)``; when ``None`` (the
            default), it falls back to ``settings.default_model`` for every role
            (back-compat with SFP-36 — ``options.model`` is unchanged).
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
        max_turns: int = 50,
        cwd: str | None = None,
        sleep: SleepFn | None = None,
        model_resolver: AgentModelConfig | None = None,
        effort: str | None = None,
        enforce_schema: bool = True,
    ) -> None:
        self._settings = settings
        self._secret_provider = secret_provider
        self._output_contract = output_contract
        self._query_fn: QueryFn | None = query_fn
        self._max_retries = max_retries
        self._max_turns = max_turns
        self._cwd: str | None = cwd
        self._sleep: SleepFn = sleep if sleep is not None else asyncio.sleep
        self._model_resolver: AgentModelConfig | None = model_resolver
        # SMOKE-PATCH (local, NOT committed — formalize via PR): per-role
        # reasoning effort forwarded to ClaudeAgentOptions(effort=...). None
        # leaves the SDK/model default. "low" makes emit-JSON agents finalize
        # in few turns (the main per-call speed + reliability lever).
        self._effort: str | None = effort
        # When False, do NOT pass output_format — the agent must DO tool work
        # before reporting (the Coder). Forcing JSON makes such agents emit the
        # report without performing the work. Emit-JSON agents keep it True.
        self._enforce_schema: bool = enforce_schema

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        """Run the agent; never raises (fail-closed on any unhandled error)."""
        # 1. Resolve the secret ONCE, outside the retry loop. query_fn is NEVER
        #    called when resolution fails.
        try:
            token = self._secret_provider.resolve(self._settings.llm_provider_secret_ref)
        except SecretResolutionError as exc:
            return _failure(request, f"secret resolution failed: {exc}")

        # 2. Build options — routing + auth live in options.env (ID-020). The
        #    model is resolved per-role when a resolver is injected (SFP-37),
        #    otherwise settings.default_model is used for every role.
        options = self._build_options(token, request.agent)

        # 3-8. Stream + parse + validate under the retry policy, bridging async.
        try:
            return asyncio.run(self._run_async(request, options))
        except Exception as exc:  # noqa: BLE001 — fail-closed on anything
            return _failure(request, f"{type(exc).__name__}: {exc}")

    # -- internals ---------------------------------------------------------

    def _build_options(self, token: str, role: str) -> Any:
        """Build ``ClaudeAgentOptions`` with routing + auth in ``env`` (ID-020).

        The model is resolved per ``role`` when a ``model_resolver`` was injected
        (SFP-37 / Jira SFP-54); otherwise ``settings.default_model`` is used
        (back-compat with SFP-36). Importing the ``ClaudeAgentOptions`` dataclass
        does NOT spawn the CLI (only ``query()`` does), so this is safe to
        exercise in tests.
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
        model = (
            self._model_resolver.resolve(role)
            if self._model_resolver is not None
            else self._settings.default_model
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "env": env,
            "max_turns": self._max_turns,
            "cwd": self._cwd,
        }
        # SMOKE-PATCH (local, NOT committed): the spawned CLI is NON-interactive,
        # so the default permission mode ("default") would PROMPT for Write/Edit/
        # Bash and, with no human to answer, the tool call is silently DENIED —
        # the Coder could not write any files. bypassPermissions lets tools run
        # unprompted. Safe: agents run in an ephemeral, pre-trusted worktree.
        kwargs["permission_mode"] = "bypassPermissions"
        # SMOKE-PATCH (local, NOT committed): pass the contract as a JSON schema
        # so the SDK constrains the model and returns parsed, schema-valid JSON
        # on ResultMessage.structured_output (verified GLM honors it). Kills the
        # prompt<->contract drift class. Per-role effort bounds reasoning depth.
        # Skipped for do-then-report agents (the Coder) that must perform tool
        # work before emitting — forcing JSON there makes them skip the work.
        if self._enforce_schema:
            try:
                kwargs["output_format"] = {
                    "type": "json_schema",
                    "schema": self._output_contract.model_json_schema(),
                }
            except Exception:  # noqa: BLE001 — defensive: not every BaseModel emits a schema
                pass
        if self._effort is not None:
            kwargs["effort"] = self._effort
        return ClaudeAgentOptions(**kwargs)

    async def _run_async(self, request: AgentRunRequest, options: Any) -> AgentRunResult:
        query_fn = self._resolve_query_fn()
        # The retryer wraps ONLY stream consumption — transient failures
        # (CLIConnectionError / 5xx / rate-limit / empty stream) are retried;
        # the hard-reject checks below run once, after a clean consume.
        # SMOKE-PATCH (local, NOT committed — formalize via PR): forward
        # request.context to the agent by appending it to the prompt (the SDK
        # query() takes only `prompt`). Without this the model gets the prompt
        # template but no ticket data → empty result.
        import json as _json

        effective_prompt = request.prompt
        if request.context:
            effective_prompt += "\n\n--- RUN CONTEXT (JSON) ---\n" + _json.dumps(
                dict(request.context), default=str, ensure_ascii=False
            )
        final_message: Any = await self._retryer()(
            self._consume_stream, query_fn, effective_prompt, options, request.agent
        )
        # ``request.agent`` is threaded through to the watchdog messages above.

        # 4. Hard-reject an SDK error or missing result (NOT retried).
        is_error = bool(getattr(final_message, "is_error", False))
        result_text = getattr(final_message, "result", None)
        structured = getattr(final_message, "structured_output", None)
        # SMOKE-PATCH: when the SDK enforced output_format, structured_output is
        # already parsed + schema-valid — prefer it. Require result_text only as
        # the text-fallback path.
        if is_error or (structured is None and result_text is None):
            errors = getattr(final_message, "errors", None) or []
            detail = (
                "; ".join(str(e) for e in errors) if errors else "agent produced no usable result"
            )
            return _failure(request, f"agent run errored: {detail}")

        # 5. Parse: prefer structured_output (already a dict); fall back to
        #    fence-stripped JSON parse of result_text (ID-019 fence handling).
        if isinstance(structured, dict):
            parsed: Any = structured
        else:
            # Text-fallback path: result_text must be a str. The empty case
            # (structured is None and result_text is None) was caught above; this
            # guard narrows the type and fail-closes any remaining non-str result.
            if not isinstance(result_text, str):
                return _failure(request, "agent produced no usable result")
            try:
                parsed = json.loads(_extract_json(result_text))
            except json.JSONDecodeError as exc:
                return _failure(request, f"agent output was not valid JSON: {exc}")

        # 6. Validate against the injected output contract (hard reject, no retry).
        try:
            self._output_contract.model_validate(parsed)
        except ValidationError as exc:
            return _failure(request, f"agent output failed contract validation: {exc}")

        # 7. Success — agent/ticket_id come from the REQUEST, never parsed
        #    output (anti-spoof). ``output`` is the raw parsed JSON.
        #    SFP-249: ``final_text`` transports the captured ResultMessage
        #    result text (None-preserving when absent/None) for
        #    human-readable surfaces; ``output`` stays the only decision field.
        final_text = result_text if isinstance(result_text, str) else None
        return AgentRunResult(
            agent=request.agent,
            ticket_id=request.ticket_id,
            success=True,
            output=parsed,
            final_text=final_text,
        )

    async def _consume_stream(
        self, query_fn: QueryFn, prompt: str, options: Any, agent: str | None = None
    ) -> Any:
        """Consume the SDK stream and return the final ``ResultMessage``.

        Raises :class:`_TransientSDKError` (driving a retry) on an empty stream,
        a transient (5xx/429) ``api_error_status``, or a watchdog trip (SFP-242:
        no first event within ``spawn_first_event_timeout_s``, or no further
        event within ``spawn_progress_timeout_s`` of the last one). Any other
        condition is surfaced to :meth:`_run_async` for a hard-reject decision.

        Watchdog mechanics: each ``__anext__`` is wrapped in
        :func:`asyncio.wait_for` with the currently active budget (first-event
        budget until the first event arrives, inactivity budget afterwards). On
        timeout, ``wait_for`` cancels the suspended ``__anext__`` and we
        ``aclose()`` the generator — on the real SDK path its ``finally: await
        query.close()`` runs the transport's shielded terminate→kill escalation
        (grace wait → SIGTERM → SIGKILL), so the CLI is reaped BEFORE the error
        propagates rather than orphaned. Closing is itself bounded so a wedged
        generator cannot turn a minute-scale abort back into a hang.

        ``agent`` names the agent role in the watchdog message (e.g. "coder
        spawn watchdog: ..."); it is the request's role, not parsed output.
        """
        final_message: Any = None
        first_budget = self._settings.spawn_first_event_timeout_s
        progress_budget = self._settings.spawn_progress_timeout_s
        agent_label = agent if agent else "agent"
        stream = query_fn(prompt=prompt, options=options)
        # monotonic (never wall clock) so an NTP jump cannot fake a trip or
        # mask a real one (MAS §12.7 determinism).
        started = time.monotonic()
        last_event_at = started
        try:
            while True:
                budget = first_budget if final_message is None else progress_budget
                try:
                    message = await asyncio.wait_for(stream.__anext__(), timeout=budget)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    stage = _FIRST_EVENT_STAGE if final_message is None else _PROGRESS_STAGE
                    elapsed = time.monotonic() - (
                        started if final_message is None else last_event_at
                    )
                    await self._close_stream_silently(stream)
                    raise _TransientSDKError(
                        f"{agent_label} {stage} watchdog: no stream events in "
                        f"{budget:.0f}s (silent for {elapsed:.0f}s) — CLI stream "
                        f"closed (endpoint mute?)"
                    ) from None
                final_message = message
                last_event_at = time.monotonic()
        except Exception as exc:  # noqa: BLE001 — see below
            # The SDK sometimes raises an opaque "Claude Code returned an error
            # result" on transient CLI/endpoint hiccups; retry rather than
            # hard-fail the whole pipeline on one flaky call. Watchdog trips
            # (already _TransientSDKError) re-raise unchanged so their message
            # names the watchdog instead of being re-wrapped.
            if isinstance(exc, _TransientSDKError):
                raise
            raise _TransientSDKError(f"sdk stream raised: {type(exc).__name__}: {exc}") from exc

        if final_message is None:
            raise _TransientSDKError("agent produced no messages")

        status = getattr(final_message, "api_error_status", None)
        if status is not None and status in _TRANSIENT_API_STATUSES:
            raise _TransientSDKError(f"transient api_error_status={status}")

        # SMOKE-PATCH (local, NOT committed — formalize via PR): treat an empty
        # / whitespace-only `result` as TRANSIENT (retryable). GLM sometimes
        # finalizes without emitting (ID-019 agentic symptom); a retry usually
        # yields output. Without this the fail-closed gate blocks
        # non-deterministically on a single empty response.
        result = getattr(final_message, "result", None)
        structured = getattr(final_message, "structured_output", None)
        # Only retry when BOTH the text result and the structured output are
        # empty — a populated structured_output is a valid result even if the
        # text result is empty (the SDK populated it from output_format).
        result_empty = result is None or (isinstance(result, str) and not result.strip())
        if result_empty and structured is None:
            raise _TransientSDKError("agent produced an empty result")

        return final_message

    @staticmethod
    async def _close_stream_silently(stream: Any) -> None:
        """Close a stream generator on a watchdog trip, best-effort and bounded.

        On the real SDK path ``aclose()`` runs the generator's ``finally`` →
        transport ``close()`` (terminate → kill escalation), releasing the CLI.
        A wedged generator could hang that cleanup, so it is bounded; any error
        is swallowed — the watchdog error is the actionable signal and MUST
        propagate.
        """
        aclose = getattr(stream, "aclose", None)
        if aclose is None:  # pragma: no cover — defensive: not an async generator
            return
        try:
            await asyncio.wait_for(aclose(), timeout=_WATCHDOG_CLOSE_GRACE_S)
        except Exception:  # noqa: BLE001 — cleanup is best-effort
            pass

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
