"""Tests for the local init container helper modules (SFP-79).

Covers the coverage-gated library modules under ``infrastructure/local/init/``:
``logging_utils.py``, ``service_registry.py``, ``readiness.py``. The one-shot
scripts (``run_pulumi.py``, ``run_alembic.py``) are excluded from coverage per
the migrations/env.py precedent and are not exercised here.

The helpers are imported by adding the init directory to ``sys.path`` (same
pattern ``tests/test_fk_lint.py`` uses for ``tools/fk_lint.py``).

``readiness.wait_until`` is exercised with injected ``sleep``/``clock`` fakes so
the retry/backoff/timeout logic is tested deterministically — no real time or
network (MAS §12.7).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer
from sqlalchemy.orm import DeclarativeBase

# --- make the init dir importable as flat modules ---------------------------
INIT_DIR = Path(__file__).resolve().parents[1] / "infrastructure" / "local" / "init"
if str(INIT_DIR) not in sys.path:
    sys.path.insert(0, str(INIT_DIR))

import logging_utils  # noqa: E402
import readiness  # noqa: E402
import service_registry  # noqa: E402


# A fresh throwaway declarative base whose metadata has no mapped tables.
class _EmptyBase(DeclarativeBase):
    pass


# A declarative base with one mapped table -> has_mapped_tables is True.
class _PopBase(DeclarativeBase):
    pass


class _Widget(_PopBase):
    __tablename__ = "widgets"
    id = Column(Integer, primary_key=True)


# ============================================================
# logging_utils
# ============================================================


def test_configure_logging_is_idempotent_and_sets_level():
    logger = logging_utils.configure_logging("DEBUG")
    assert logger.name == "sfp.init"
    handlers_before = len(logger.handlers)
    assert logger.level == logging.DEBUG
    # Second call must not add another handler.
    logger2 = logging_utils.configure_logging("WARNING")
    assert logger2 is logger
    assert len(logger.handlers) == handlers_before
    assert logger.level == logging.WARNING
    assert logger.propagate is False


def test_configure_logging_accepts_lowercase_level():
    logger = logging_utils.configure_logging("info")
    assert logger.level == logging.INFO


def test_log_step_and_log_skip_emit_through_logger(caplog):
    logger = logging_utils.configure_logging("INFO")
    with caplog.at_level(logging.INFO, logger="sfp.init"):
        logging_utils.log_step(logger, "do-thing")
        logging_utils.log_skip(logger, "communication", "no tables")
    msgs = "\n".join(r.message for r in caplog.records)
    assert "step: do-thing" in msgs
    assert "skip: service=communication reason=no tables" in msgs


# ============================================================
# service_registry — enumeration
# ============================================================


def test_registry_has_exactly_the_four_services_in_order():
    names = service_registry.service_names()
    assert names == ["identity", "orchestrator", "communication", "external-events"]


def test_all_services_returns_same_tuple():
    assert service_registry.all_services() is service_registry.SERVICE_REGISTRY
    assert len(service_registry.all_services()) == 4


def test_get_service_lookup_and_underscore_alias():
    ext = service_registry.get_service("external-events")
    assert ext.database == "external_events"
    assert ext.module_path == "external_events.infrastructure.persistence"
    # underscore spelling aliased to the hyphen key
    assert service_registry.get_service("external_events") is ext


@pytest.mark.parametrize("name", ["identity", "orchestrator", "communication", "external-events"])
def test_each_entry_has_consistent_paths(name):
    entry = service_registry.get_service(name)
    assert entry.service_dir.name == entry.directory
    assert entry.alembic_ini_path == entry.service_dir / "alembic.ini"
    assert entry.src_dir == entry.service_dir / "src"


def test_database_url_uses_driver_token_per_service():
    ident = service_registry.get_service("identity")
    assert ident.driver == "psycopg"
    assert ident.database_url(host="h", port=5432, user="u", password="p") == (
        "postgresql+psycopg://u:p@h:5432/identity"
    )
    orch = service_registry.get_service("orchestrator")
    assert orch.driver == "asyncpg"
    assert orch.database_url(host="h", port=5432, user="u", password="p").startswith(
        "postgresql+asyncpg://u:p@h:5432/orchestrator"
    )


def test_get_service_unknown_raises_valueerror():
    with pytest.raises(ValueError, match="unknown service"):
        service_registry.get_service("nope")


def test_database_names_match_postgres_init_databases():
    # The four logical DB names created by infrastructure/local/postgres/init.sh.
    expected = {"identity", "orchestrator", "communication", "external_events"}
    actual = {entry.database for entry in service_registry.all_services()}
    assert actual == expected


# ============================================================
# service_registry — load_base / has_mapped_tables / skip signals
# ============================================================


def test_load_base_imports_real_identity_base_with_tables():
    entry = service_registry.get_service("identity")
    base = service_registry.load_base(entry)
    # load_base returns the declarative Base *class* (a DeclarativeBase
    # subclass), not an instance — so assert subclass, not isinstance.
    assert isinstance(base, type) and issubclass(base, DeclarativeBase)
    assert service_registry.has_mapped_tables(base) is True


def test_load_base_raises_serviceloaderror_on_missing_base(monkeypatch):
    entry = service_registry.get_service("identity")

    class _FakeModule:
        pass  # no Base attribute

    def _fake_import(_name: str) -> object:
        return _FakeModule()

    monkeypatch.setattr(service_registry.importlib, "import_module", _fake_import)
    with pytest.raises(service_registry.ServiceLoadError) as exc:
        service_registry.load_base(entry)
    assert "exposes no `Base`" in exc.value.reason


def test_load_base_raises_serviceloaderror_on_import_error(monkeypatch):
    entry = service_registry.get_service("identity")

    def _boom(_name: str) -> object:
        raise RuntimeError("syntax error in module")

    monkeypatch.setattr(service_registry.importlib, "import_module", _boom)
    with pytest.raises(service_registry.ServiceLoadError) as exc:
        service_registry.load_base(entry)
    assert "import failed" in exc.value.reason


def test_has_mapped_tables_false_for_empty_metadata():
    assert service_registry.has_mapped_tables(_EmptyBase) is False


def test_has_mapped_tables_true_when_tables_present():
    assert service_registry.has_mapped_tables(_PopBase) is True


def test_serviceload_error_carries_service_and_reason():
    err = service_registry.ServiceLoadError("communication", "no base")
    assert err.service == "communication"
    assert err.reason == "no base"
    assert "communication: no base" in str(err)


# ============================================================
# readiness — wait_until retry/backoff/timeout (deterministic)
# ============================================================


def _fake_clock():
    """Return a clock whose ``now()`` advances by each sleep amount."""
    state = {"t": 0.0}

    def clock() -> float:
        return state["t"]

    def sleep(d: float) -> None:
        state["t"] += d

    return clock, sleep


def test_wait_until_returns_immediately_when_probe_true():
    clock, sleep = _fake_clock()
    sleeps: list[float] = []
    readiness.wait_until(lambda: True, "x", timeout=10, sleep=sleeps.append, clock=clock)
    assert sleeps == []  # no waiting needed


def test_wait_until_retries_then_succeeds():
    clock, sleep = _fake_clock()
    calls = {"n": 0}

    def probe() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3  # ready on the 3rd attempt

    readiness.wait_until(
        probe, "dep", timeout=30, interval=1, backoff_factor=2, sleep=sleep, clock=clock
    )
    assert calls["n"] == 3
    # backoff: after attempt1 sleep 1, after attempt2 sleep 2 -> total 3
    assert clock() == 3


def test_wait_until_raises_readiness_error_on_timeout():
    clock, sleep = _fake_clock()
    with pytest.raises(readiness.ReadinessError, match="dep not ready"):
        readiness.wait_until(
            lambda: False,
            "dep",
            timeout=5,
            interval=2,
            backoff_factor=1,
            sleep=sleep,
            clock=clock,
        )


def test_wait_until_swallows_probe_exception_and_keeps_waiting():
    clock, sleep = _fake_clock()
    calls = {"n": 0}

    def probe() -> bool:
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("refused")
        return True

    logger = logging_utils.configure_logging("DEBUG")
    readiness.wait_until(
        probe, "dep", timeout=30, interval=1, sleep=sleep, clock=clock, logger=logger
    )
    assert calls["n"] == 2


def test_wait_until_backoff_capped_at_max_interval():
    clock, sleep = _fake_clock()
    calls = {"n": 0}

    def probe() -> bool:
        calls["n"] += 1
        return calls["n"] >= 5  # ready on attempt 5

    readiness.wait_until(
        probe,
        "dep",
        timeout=1000,
        interval=2,
        backoff_factor=10,
        max_interval=5,
        sleep=sleep,
        clock=clock,
    )
    # waits: 2 (cap 5), 5 (cap), 5 (cap), 5 -> but last attempt ready so the
    # sleep after attempt 4 is 5. Sum of sleeps before success = 2+5+5+5 = 17.
    assert clock() == 17


# ============================================================
# readiness — probes
# ============================================================


def test_check_postgres_true_for_in_memory_sqlite():
    # sqlite is always connectable in-process -> SELECT 1 succeeds.
    assert readiness.check_postgres("sqlite://") is True


def test_check_postgres_false_for_unreachable_driver():
    # No DB server here, and the driver will fail to connect.
    assert readiness.check_postgres("postgresql+psycopg://nobody:nopw@127.0.0.1:9/none") is False


def test_check_postgres_false_for_garbage_url():
    assert readiness.check_postgres("not-a-valid-engine-spec-:::") is False


def test_check_localstack_true_when_health_body_mentions_running():
    def opener(_url: str) -> object:
        return '{"features": {"running": ["sns","sqs"]}}'

    assert readiness.check_localstack("http://localstack:4566", opener=opener) is True


def test_check_localstack_false_on_network_error():
    def boom(_url: str) -> object:
        raise OSError("connection refused")

    assert readiness.check_localstack("http://localstack:4566", opener=boom) is False


def test_check_localstack_false_when_body_lacks_ready_keyword():
    def opener(_url: str) -> object:
        return '{"features": {}}'

    assert readiness.check_localstack("http://localstack:4566", opener=opener) is False


def test_check_localstack_uses_health_path(monkeypatch):
    seen: list[str] = []

    def opener(url: str) -> object:
        seen.append(url)
        return "services running"

    readiness.check_localstack("http://localstack:4566/", opener=opener)
    assert seen == ["http://localstack:4566/_localstack/health"]


# ============================================================
# readiness — convenience wrappers wire through to wait_until
# ============================================================


def test_wait_for_postgres_delegates_to_wait_until(monkeypatch):
    seen: dict[str, object] = {}

    def _fake_wait_until(probe, description, **kwargs):
        seen["probe"] = probe
        seen["description"] = description
        seen["timeout"] = kwargs.get("timeout")
        return None

    monkeypatch.setattr(readiness, "wait_until", _fake_wait_until)
    readiness.wait_for_postgres("postgresql+psycopg://u:p@h:5432/identity", timeout=42)
    assert seen["timeout"] == 42
    assert "identity" in str(seen["description"])
    # The probe wraps check_postgres; it is callable (we do not assert its
    # return value — the host is unreachable here by design).
    assert callable(seen["probe"])


def test_wait_for_localstack_delegates_to_wait_until(monkeypatch):
    seen: dict[str, object] = {}

    def _fake_wait_until(probe, description, **kwargs):
        seen["description"] = description
        return None

    monkeypatch.setattr(readiness, "wait_until", _fake_wait_until)
    readiness.wait_for_localstack("http://localstack:4566")
    assert "localstack" in str(seen["description"])
