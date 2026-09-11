"""Tests for the SFP-121 EndpointConfig resolver.

Exercises the resolver against a real SQLAlchemy session over in-memory
SQLite so the ORM mapping round-trips (schema-qualified table, ``SAEnum``
status column), keeping the suite deterministic — no network, no wall clock
in any assertion, no external DB.

Two SQLite accommodations, both forced by the pre-existing SFP-113 model,
not by the resolver:

- ``operational.endpoint_configs`` is schema-qualified; SQLite only knows
  schemas as *attached* databases, so the engine attaches an in-memory
  database named ``operational`` on connect.
- ``created_at``/``updated_at`` carry a PostgreSQL ``server_default
  = "now()"``; tests seed explicit (fixed, deterministic) timestamps so the
  PostgreSQL-only default is never fetched back through SQLite.

Covers the acceptance criteria: the four-field happy path with verbatim
enum status, the typed not-found exception (type + carried ``endpoint_id``
payload, never a ``None`` return, never a generic ``KeyError``), the
cache-miss-still-raises rule, and the ``application`` package exports.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from external_events.application import (
    EndpointConfigNotFoundError,
    EndpointConfigResolver,
)
from external_events.application.endpoint_resolver import (
    SessionFactory,
    resolve,
)
from external_events.infrastructure.persistence import (
    Base,
    EndpointConfig,
    EndpointStatus,
)
from sqlalchemy.orm import Session, sessionmaker

#: Fixed, timezone-aware seed timestamps — deterministic, and they keep the
#: PostgreSQL-only ``now()`` server default out of the SQLite round-trip.
_SEED_TS = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def session_factory() -> Iterator[SessionFactory]:
    """A fresh in-memory database per test, ``operational`` schema attached.

    One underlying connection stays open for the whole test (StaticPool):
    SQLite ``:memory:`` databases live and die with their connection, so a
    shared connection is what makes rows planted by one session visible to
    the next. Still fully hermetic — nothing crosses the test's boundary.
    """
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sa.pool.StaticPool,
    )

    @sa.event.listens_for(engine, "connect")
    def _attach_operational(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS operational")  # type: ignore[attr-defined]

    Base.metadata.create_all(engine)
    connection = engine.connect()
    maker = sessionmaker(bind=connection, expire_on_commit=False)

    @contextmanager
    def factory() -> Iterator[Session]:
        session = maker()
        try:
            yield session
        finally:
            session.close()

    yield factory

    connection.close()
    engine.dispose()


def _seed(session_factory: SessionFactory, **overrides: object) -> None:
    """Plant one ``EndpointConfig`` row; overrides patch any field."""
    defaults: dict[str, object] = {
        "endpoint_id": "gh-webhook",
        "provider": "github",
        "auth_strategy": "hmac_sha256",
        "secret_ref": "op://prod/github/webhook",
        "status": EndpointStatus.ACTIVE,
        "created_at": _SEED_TS,
        "updated_at": _SEED_TS,
    }
    defaults.update(overrides)
    with session_factory() as session:
        session.add(EndpointConfig(**defaults))  # type: ignore[arg-type]
        session.commit()


class _CountingFactory:
    """SessionFactory wrapper counting how many sessions were opened."""

    def __init__(self, inner: SessionFactory) -> None:
        self._inner = inner
        self.calls = 0

    def __call__(self) -> AbstractContextManager[Session]:
        self.calls += 1
        return self._inner()


# --- happy path ---------------------------------------------------------------


class TestResolveConfiguredEndpoint:
    def test_returns_all_four_fields_matching_the_row(self, session_factory):
        _seed(session_factory)
        result = EndpointConfigResolver(session_factory).resolve("gh-webhook")

        assert result.provider == "github"
        assert result.auth_strategy == "hmac_sha256"
        assert result.secret_ref == "op://prod/github/webhook"
        assert result.status is EndpointStatus.ACTIVE

    def test_result_is_the_prspec_tuple_in_order(self, session_factory):
        _seed(session_factory)
        result = EndpointConfigResolver(session_factory).resolve("gh-webhook")

        # The contract is the 4-tuple (provider, auth_strategy, secret_ref,
        # status) — NamedTuple must unpack and compare as exactly that.
        assert tuple(result) == (
            "github",
            "hmac_sha256",
            "op://prod/github/webhook",
            EndpointStatus.ACTIVE,
        )
        provider, auth_strategy, secret_ref, status = result
        assert (provider, auth_strategy, secret_ref, status) == (
            "github",
            "hmac_sha256",
            "op://prod/github/webhook",
            EndpointStatus.ACTIVE,
        )

    def test_inactive_status_passes_through_verbatim(self, session_factory):
        # No interpretation, no filtering: INACTIVE resolves exactly like
        # ACTIVE — the accept/reject check is SFP-120's, not the resolver's.
        _seed(session_factory, status=EndpointStatus.INACTIVE)
        result = EndpointConfigResolver(session_factory).resolve("gh-webhook")

        assert result.status is EndpointStatus.INACTIVE

    def test_module_level_resolve_returns_the_row(self, session_factory):
        _seed(session_factory)

        assert resolve("gh-webhook", session_factory) == (
            "github",
            "hmac_sha256",
            "op://prod/github/webhook",
            EndpointStatus.ACTIVE,
        )

    def test_two_distinct_endpoints_resolve_independently(self, session_factory):
        _seed(session_factory)  # gh-webhook / github / hmac_sha256 / ACTIVE
        _seed(
            session_factory,
            endpoint_id="slack-hook",
            provider="slack",
            auth_strategy="token_compare",
            secret_ref="op://prod/slack/webhook",
            status=EndpointStatus.INACTIVE,
        )
        resolver = EndpointConfigResolver(session_factory)

        gh = resolver.resolve("gh-webhook")
        slack = resolver.resolve("slack-hook")

        assert (gh.provider, gh.status) == ("github", EndpointStatus.ACTIVE)
        assert (slack.provider, slack.status) == ("slack", EndpointStatus.INACTIVE)


# --- unknown / never-configured ids ------------------------------------------


class TestUnknownEndpointId:
    def test_unknown_id_on_empty_store_raises_with_the_requested_id(self, session_factory):
        resolver = EndpointConfigResolver(session_factory)

        with pytest.raises(EndpointConfigNotFoundError) as excinfo:
            resolver.resolve("ghost-endpoint")

        assert excinfo.value.endpoint_id == "ghost-endpoint"

    def test_never_configured_id_raises_while_others_exist(self, session_factory):
        _seed(session_factory)
        resolver = EndpointConfigResolver(session_factory)

        with pytest.raises(EndpointConfigNotFoundError) as excinfo:
            resolver.resolve("never-configured")

        assert excinfo.value.endpoint_id == "never-configured"
        assert excinfo.value.endpoint_id != "gh-webhook"  # payload is the ask

    def test_exception_is_typed_not_a_generic_keyerror(self, session_factory):
        resolver = EndpointConfigResolver(session_factory)

        with pytest.raises(EndpointConfigNotFoundError) as excinfo:
            resolver.resolve("ghost-endpoint")

        # A LookupError subclass catchable by its own type — never a mapping
        # error a caller might catch by accident.
        assert isinstance(excinfo.value, LookupError)
        assert not isinstance(excinfo.value, KeyError)

    def test_exception_message_names_the_endpoint(self, session_factory):
        resolver = EndpointConfigResolver(session_factory)

        with pytest.raises(EndpointConfigNotFoundError, match="no-such-id"):
            resolver.resolve("no-such-id")

    def test_module_level_resolve_raises_for_unknown_id(self, session_factory):
        with pytest.raises(EndpointConfigNotFoundError) as excinfo:
            resolve("ghost-endpoint", session_factory)

        assert excinfo.value.endpoint_id == "ghost-endpoint"


# --- the local read-through cache --------------------------------------------


class TestLocalReadCache:
    def test_second_resolve_is_served_from_the_cache(self, session_factory):
        _seed(session_factory)
        counting = _CountingFactory(session_factory)
        resolver = EndpointConfigResolver(counting)

        first = resolver.resolve("gh-webhook")
        second = resolver.resolve("gh-webhook")

        # One session opened (the cache miss); the hit opens none and is the
        # very same resolved tuple.
        assert counting.calls == 1
        assert second is first

    def test_warm_cache_survives_the_row_vanishing(self, session_factory):
        # The cache is a local read-through optimization: a warm entry is
        # served without re-reading the database.
        _seed(session_factory)
        counting = _CountingFactory(session_factory)
        resolver = EndpointConfigResolver(counting)
        warm = resolver.resolve("gh-webhook")

        with session_factory() as session:
            session.execute(sa.delete(EndpointConfig))
            session.commit()

        assert resolver.resolve("gh-webhook") is warm
        assert counting.calls == 1

    def test_cache_miss_for_unknown_id_still_raises_every_time(self, session_factory):
        _seed(session_factory)  # store is non-empty; the ask is not in it
        counting = _CountingFactory(session_factory)
        resolver = EndpointConfigResolver(counting)

        for _ in range(3):
            with pytest.raises(EndpointConfigNotFoundError) as excinfo:
                resolver.resolve("ghost-endpoint")
            assert excinfo.value.endpoint_id == "ghost-endpoint"

        # Misses are never cached — every ask consults the database again,
        # and every consult surfaces the same typed raise.
        assert counting.calls == 3

    def test_cache_is_per_instance_not_shared(self, session_factory):
        _seed(session_factory)
        counting = _CountingFactory(session_factory)
        first_resolver = EndpointConfigResolver(counting)
        second_resolver = EndpointConfigResolver(counting)

        warm = first_resolver.resolve("gh-webhook")
        fresh = second_resolver.resolve("gh-webhook")

        # Equal values, independently resolved: no process-global cache.
        assert counting.calls == 2
        assert fresh == warm
        assert fresh is not warm


# --- package surface ----------------------------------------------------------


class TestApplicationExports:
    def test_resolve_and_not_found_are_exported(self):
        # AC: application/__init__.py exports resolve and
        # EndpointConfigNotFoundError (the names SFP-120 imports).
        from external_events import application

        assert application.resolve is resolve
        assert application.EndpointConfigNotFoundError is EndpointConfigNotFoundError
        assert callable(application.resolve)
        assert "resolve" in application.__all__
        assert "EndpointConfigNotFoundError" in application.__all__

    def test_resolver_class_is_exported(self):
        from external_events import application

        assert application.EndpointConfigResolver is EndpointConfigResolver
