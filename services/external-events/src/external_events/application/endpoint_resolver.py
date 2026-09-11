"""Application-layer EndpointConfig resolver (SFP-121).

Loads an endpoint's configuration by ``endpoint_id`` from the SFP-113
persistence model (``operational.endpoint_configs``) and returns the four
fields the ingress path needs: ``provider``, ``auth_strategy``,
``secret_ref`` and ``status``.

Scope fences (what this module deliberately does NOT do):

- **No status interpretation.** :class:`~external_events.infrastructure.
  persistence.EndpointStatus` passes through verbatim — an ``INACTIVE``
  endpoint resolves exactly like an ``ACTIVE`` one. The accept/reject check
  ("only ACTIVE accepts") is ingress-level validation owned by SFP-120, per
  the SFP-96 model docstring.
- **No credential retrieval or auth-strategy validation.** ``secret_ref`` is
  an opaque reference string (ID-016), never a secret value; making anything
  of it is SFP-122's job.
- **No transport.** The resolver is bus-free — no SNS/SQS concerns (SFP-118,
  Phase-B deferred).

Error contract (binding, consumed by SFP-120): an unknown or
never-configured ``endpoint_id`` raises :class:`EndpointConfigNotFoundError`
carrying the requested id as the assertable attribute ``endpoint_id`` — an
exception, never a ``None`` return and never a bare ``KeyError`` (precedent:
``compute_waves`` / PR #123). SFP-120 maps this exception to HTTP 404.

The local read-through cache is an optimization only: it short-circuits
repeated reads of *resolved* (found) configs and never suppresses the raise
for unknown or never-configured ids — a cache miss simply falls through to
the database, and database misses are never cached. Determinism (MAS §12.7):
no clock, no network beyond the injected session, no randomness.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import NamedTuple

from sqlalchemy.orm import Session

from external_events.infrastructure.persistence import (
    EndpointConfig,
    EndpointStatus,
)

__all__ = [
    "EndpointConfigNotFoundError",
    "EndpointConfigResolver",
    "ResolvedEndpointConfig",
    "SessionFactory",
    "resolve",
]

#: A factory opening one session as a context manager — the same shape the
#: orchestrator's persistence layer established (``session_scope`` satisfies
#: it). The resolver owns no engine and no session lifecycle of its own; it
#: borrows one session per cache miss and closes it via the context manager.
SessionFactory = Callable[[], AbstractContextManager[Session]]


class EndpointConfigNotFoundError(LookupError):
    """Raised when no ``EndpointConfig`` row exists for an ``endpoint_id``.

    Covers both the unknown-id and the never-configured-id case — from the
    resolver's side they are the same fact: no row in
    ``operational.endpoint_configs``. Subclasses :class:`LookupError` (a
    lookup missed), *not* :class:`KeyError` — callers must single out this
    type, never catch a generic mapping error.

    The carried ``endpoint_id`` attribute is a binding contract: SFP-120
    maps this exception to HTTP 404 and asserts/logs the offending id from
    the exception payload (precedent: ``BuildOrderCycleError`` / PR #123).
    """

    def __init__(self, endpoint_id: str) -> None:
        super().__init__(f"No EndpointConfig found for endpoint_id={endpoint_id!r}")
        self.endpoint_id = endpoint_id


class ResolvedEndpointConfig(NamedTuple):
    """The four ingress-facing fields of one endpoint's configuration.

    A ``NamedTuple`` so the PRSpec's tuple contract
    ``(provider, auth_strategy, secret_ref, status)`` holds literally —
    unpacking and plain-tuple equality both work — while the fields stay
    named and typed for consumers (SFP-120). Values are copied from the
    persisted row; no ORM object escapes the resolver's session.
    """

    provider: str
    auth_strategy: str
    secret_ref: str
    status: EndpointStatus


class EndpointConfigResolver:
    """Resolves endpoint configuration by id, with a local read-through cache.

    Bound at construction to one :data:`SessionFactory`. The instance-local
    cache holds previously *resolved* configs keyed by ``endpoint_id``:

    - cache hit → the resolved tuple is returned with no session opened;
    - cache miss → one session reads the row; a found row populates the
      cache and is returned, a missing row raises
      :class:`EndpointConfigNotFoundError` **and is not cached**, so an
      unknown id raises on every resolve (the cache is an optimization,
      never a correctness source).

    Endpoint configuration is seeded out-of-band (ID-058, administrative
    state for v0); a warm entry therefore reflects the row as of its last
    cache miss. Per-instance state only — no process-global cache, no
    cross-instance sharing.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._cache: dict[str, ResolvedEndpointConfig] = {}

    def resolve(self, endpoint_id: str) -> ResolvedEndpointConfig:
        """Return the configuration for ``endpoint_id`` or raise not-found.

        Args:
            endpoint_id: the endpoint to resolve (the
                ``operational.endpoint_configs`` primary key).

        Returns:
            ``(provider, auth_strategy, secret_ref, status)`` copied from the
            persisted row; ``status`` is the SFP-96
            :class:`~external_events.infrastructure.persistence.EndpointStatus`
            enum passed through verbatim, with no interpretation or filtering.

        Raises:
            EndpointConfigNotFoundError: no row exists for ``endpoint_id`` —
                on a cache miss (first ask) and on every repeat ask, since
                misses are never cached.
        """
        cached = self._cache.get(endpoint_id)
        if cached is not None:
            return cached

        with self._session_factory() as session:
            row = session.get(EndpointConfig, endpoint_id)

        if row is None:
            # DB-level miss and cache miss surface identically: the typed
            # not-found exception carrying the requested id. Never cached.
            raise EndpointConfigNotFoundError(endpoint_id)

        resolved = ResolvedEndpointConfig(
            provider=row.provider,
            auth_strategy=row.auth_strategy,
            secret_ref=row.secret_ref,
            status=row.status,
        )
        self._cache[endpoint_id] = resolved
        return resolved


def resolve(endpoint_id: str, session_factory: SessionFactory) -> ResolvedEndpointConfig:
    """One-shot :meth:`EndpointConfigResolver.resolve` without a held instance.

    Convenience surface for the module's exported ``resolve`` name: builds a
    fresh resolver over ``session_factory`` and resolves once. Callers that
    resolve repeatedly (the SFP-120 ingress path) should hold one
    :class:`EndpointConfigResolver` instead so the local cache is reused.

    Args:
        endpoint_id: the endpoint to resolve.
        session_factory: opens the session the lookup borrows.

    Returns:
        The same four-field tuple :meth:`EndpointConfigResolver.resolve`
        returns.

    Raises:
        EndpointConfigNotFoundError: no row exists for ``endpoint_id``.
    """
    return EndpointConfigResolver(session_factory).resolve(endpoint_id)
