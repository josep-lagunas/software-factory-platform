"""Readiness-check utilities for the local init container (SFP-79).

Before provisioning, the init container waits for its dependencies (Postgres +
LocalStack) to accept work. ``docker compose`` ``depends_on`` with
``condition: service_healthy`` covers the happy path, but a passing healthcheck
is not always enough for the next step — e.g. LocalStack may report healthy
before SNS/SQS are fully addressable. This module adds a retry-with-backoff
layer on top, with injectable sleep/clock so the core is unit-tested
deterministically (MAS §12.7 — no real time or network in tests).

Two thin probes (:func:`check_postgres`, :func:`check_localstack`) wrap the
network touch-points; everything else is the generic :func:`wait_until` poll
loop, which is the line-heavy, fully-covered logic.

Library module (coverage-gated). The probes import SQLAlchemy and ``urllib``
lazily inside the function body so the module imports cleanly without a live DB
or network, and so tests can exercise the retry loop with fakes.
"""

from __future__ import annotations

import logging
import time
import urllib.request
from collections.abc import Callable

#: Default poll interval (seconds). Deterministic callers override via args.
DEFAULT_INTERVAL = 2.0
#: Default backoff multiplier — each retry waits ``interval *= backoff``.
DEFAULT_BACKOFF = 1.5
#: Default per-probe cap so backoff does not grow without bound.
DEFAULT_MAX_INTERVAL = 30.0


class ReadinessError(TimeoutError):
    """Raised when a dependency does not become ready within the timeout."""


def wait_until(
    probe: Callable[[], bool],
    description: str,
    *,
    timeout: float,
    interval: float = DEFAULT_INTERVAL,
    backoff_factor: float = DEFAULT_BACKOFF,
    max_interval: float = DEFAULT_MAX_INTERVAL,
    logger: logging.Logger | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Poll ``probe`` until it returns ``True`` or ``timeout`` elapses.

    ``probe`` returning ``True`` means ready; a return of ``False`` or any
    raised exception means not-yet-ready (the exception is swallowed and logged
    at DEBUG so transient connection errors do not abort the wait). The wait
    interval grows by ``backoff_factor`` each retry, capped at ``max_interval``.

    Raises :class:`ReadinessError` on timeout — fail-fast on a dependency that
    never comes up is correct (the user reprovisions with ``up --force-recreate``);
    transient not-yet-ready is *not* a failure.

    ``sleep`` and ``clock`` are injectable for deterministic tests; production
    callers use the real :func:`time.sleep` / :func:`time.monotonic` defaults.
    """
    deadline = clock() + timeout
    attempt = 0
    wait = interval
    while True:
        attempt += 1
        try:
            ready = probe()
        except Exception as exc:  # noqa: BLE001 — any probe error -> keep waiting
            if logger is not None:
                logger.debug(
                    "readiness probe %s raised (attempt %d): %s",
                    description,
                    attempt,
                    exc,
                )
            ready = False
        if ready:
            if logger is not None:
                logger.info("readiness: %s ready after %d attempt(s)", description, attempt)
            return
        if clock() >= deadline:
            raise ReadinessError(
                f"{description} not ready within {timeout:g}s ({attempt} attempt(s))"
            )
        if logger is not None:
            logger.debug(
                "readiness: %s not ready (attempt %d); retrying in %.2fs",
                description,
                attempt,
                wait,
            )
        sleep(wait)
        wait = min(wait * backoff_factor, max_interval)


def check_postgres(database_url: str) -> bool:
    """Return ``True`` iff a ``SELECT 1`` succeeds against ``database_url``.

    Uses a synchronous SQLAlchemy engine with the URL's configured driver. Any
    failure to connect or execute is treated as not-yet-ready (returns
    ``False``) — the caller's :func:`wait_until` loop retries.
    """
    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(database_url)
    except Exception:  # noqa: BLE001 — bad URL -> not ready, not a crash
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — connection refused/driver missing -> not ready
        return False
    else:
        return True
    finally:
        engine.dispose()


def check_localstack(
    endpoint_url: str,
    *,
    opener: Callable[[str], object] | None = None,
) -> bool:
    """Return ``True`` iff LocalStack's health endpoint responds at ``endpoint_url``.

    Hits ``<endpoint>/_localstack/health``. Any URLError or non-presence of the
    ``"running"``/``"available"`` services in the JSON body counts as
    not-yet-ready. ``opener`` is injectable so tests can fake the HTTP response
    without a live LocalStack.
    """
    fetch = opener or _default_health_open
    try:
        body = fetch(endpoint_url.rstrip("/") + "/_localstack/health")
    except Exception:  # noqa: BLE001 — network error -> not ready
        return False
    return "running" in str(body) or "available" in str(body) or "active" in str(body)


def _default_health_open(url: str) -> str:
    """Default LocalStack health probe: GET ``url``, return the response body.

    Returns the raw decoded body; :func:`check_localstack` substring-matches the
    result for the ready keywords LocalStack emits (``running``/``available``/
    ``active``), so no JSON parsing is needed here.
    """
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 — local dev URL
        body: str = resp.read().decode("utf-8", errors="replace")
    return body


def wait_for_postgres(
    database_url: str,
    *,
    timeout: float = 60.0,
    logger: logging.Logger | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until Postgres at ``database_url`` accepts a connection."""
    wait_until(
        lambda: check_postgres(database_url),
        f"postgres ({database_url.rsplit('/', 1)[-1]})",
        timeout=timeout,
        logger=logger,
        sleep=sleep,
        clock=clock,
    )


def wait_for_localstack(
    endpoint_url: str,
    *,
    timeout: float = 90.0,
    logger: logging.Logger | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until LocalStack at ``endpoint_url`` reports healthy."""
    wait_until(
        lambda: check_localstack(endpoint_url),
        f"localstack ({endpoint_url})",
        timeout=timeout,
        logger=logger,
        sleep=sleep,
        clock=clock,
    )


__all__ = [
    "DEFAULT_BACKOFF",
    "DEFAULT_INTERVAL",
    "DEFAULT_MAX_INTERVAL",
    "ReadinessError",
    "check_localstack",
    "check_postgres",
    "wait_for_localstack",
    "wait_for_postgres",
    "wait_until",
]
