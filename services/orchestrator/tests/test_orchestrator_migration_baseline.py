"""Tests for the 0001 baseline migration (SFP-99, ID-058).

Validates the Alembic baseline migration for the Orchestrator Service database
(``sfp_orchestrator``): revision identifiers, schema-only upgrade/downgrade
EXECUTED via a stubbed ``op.execute`` recorder (not source-inspected, so the
``op.execute`` bodies contribute to coverage), plus structural checks on
``env.py`` (read as file text — importing it outside the alembic CLI raises
``AttributeError`` on ``alembic.context.config``) and ``alembic.ini``.

Three locked divergences from the Identity baseline (SFP-99):
  * TWO schemas (business + operational), not one.
  * ASYNC env.py (async_engine_from_config + run_sync + asyncio.run).
  * postgresql+asyncpg driver (Identity used postgresql+psycopg).
"""

from __future__ import annotations

import configparser
import importlib.util
import inspect
import re
from pathlib import Path

import pytest
from alembic import op as alembic_op

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_PERSISTENCE = _SERVICE_ROOT / "src" / "orchestrator" / "infrastructure" / "persistence"
_MIGRATIONS = _PERSISTENCE / "migrations"
_MIGRATION_PATH = _MIGRATIONS / "versions" / "0001_create_orchestrator_baseline.py"
_ENV_PATH = _MIGRATIONS / "env.py"


def _load_migration_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "orchestrator_migration_0001_baseline", _MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration_module()


# --- migration module structure --------------------------------------------


def test_migration_module_imports_successfully() -> None:
    """The baseline migration module loads without error."""
    assert hasattr(migration, "upgrade")
    assert hasattr(migration, "downgrade")


def test_revision_identifiers() -> None:
    assert migration.revision == "0001"  # type: ignore[attr-defined]
    assert migration.down_revision is None  # type: ignore[attr-defined]


def test_upgrade_and_downgrade_are_callables() -> None:
    assert callable(migration.upgrade)  # type: ignore[attr-defined]
    assert callable(migration.downgrade)  # type: ignore[attr-defined]


# --- executed schema tests (stubbed op.execute recorder) --------------------
# Executing upgrade()/downgrade() with a stubbed op.execute COVERS the
# op.execute bodies; source-inspection alone would leave them unexecuted and
# dilute coverage below fail_under=90 (no model code yet to dilute the pool).


def _record_op_execute(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []

    def _record(sql: object, *_args: object, **_kwargs: object) -> None:
        recorded.append(str(sql))

    monkeypatch.setattr(alembic_op, "execute", _record)
    return recorded


def test_upgrade_creates_both_schemas_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """upgrade() issues CREATE SCHEMA for business then operational."""
    recorded = _record_op_execute(monkeypatch)
    migration.upgrade()  # type: ignore[operator]
    assert recorded == [
        "CREATE SCHEMA IF NOT EXISTS business",
        "CREATE SCHEMA IF NOT EXISTS operational",
    ]


def test_downgrade_drops_both_schemas_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """downgrade() drops operational then business (reverse of creation)."""
    recorded = _record_op_execute(monkeypatch)
    migration.downgrade()  # type: ignore[operator]
    assert recorded == [
        "DROP SCHEMA IF EXISTS operational",
        "DROP SCHEMA IF EXISTS business",
    ]


# --- negative: schema-only baseline, no tables ------------------------------


def test_upgrade_is_schema_only_no_tables() -> None:
    """Baseline creates no tables (tables are downstream SFP-83..88)."""
    source = inspect.getsource(migration.upgrade)  # type: ignore[arg-type]
    assert "create_table" not in source
    assert "op.create_table" not in source


def test_downgrade_is_schema_only_no_tables() -> None:
    source = inspect.getsource(migration.downgrade)  # type: ignore[arg-type]
    assert "drop_table" not in source
    assert "op.drop_table" not in source


# --- env.py (read as file text; importing outside alembic CLI raises) -------


def test_env_py_uses_async_engine_from_config() -> None:
    env_src = _ENV_PATH.read_text()
    assert "async_engine_from_config" in env_src
    assert "run_sync" in env_src
    assert "asyncio.run" in env_src


def test_env_py_has_no_sync_engine_from_config() -> None:
    """Synchronous engine_from_config must be absent (async divergence).

    ``async_engine_from_config(`` is allowed; the bare sync ``engine_from_config(``
    (not prefixed with ``async_``) must not appear.
    """
    env_src = _ENV_PATH.read_text()
    # Negative lookbehind excludes the async_ variant's call site.
    assert re.search(r"(?<!async_)engine_from_config\s*\(", env_src) is None


def test_env_py_imports_base_and_sets_target_metadata() -> None:
    env_src = _ENV_PATH.read_text()
    assert "from orchestrator.infrastructure.persistence import Base" in env_src
    assert "target_metadata = Base.metadata" in env_src


def test_env_py_honors_database_url_env_override() -> None:
    env_src = _ENV_PATH.read_text()
    assert "ORCHESTRATOR_DATABASE_URL" in env_src
    assert "os.environ" in env_src


def test_env_py_does_not_import_models_package() -> None:
    """No models submodule exists yet (SFP-83..88 are downstream)."""
    env_src = _ENV_PATH.read_text()
    assert "import models" not in env_src


# --- persistence package ----------------------------------------------------


def test_base_is_a_declarative_base() -> None:
    from orchestrator.infrastructure.persistence import Base
    from sqlalchemy.orm import DeclarativeBase

    assert issubclass(Base, DeclarativeBase)


def test_persistence_package_exports_base_only() -> None:
    """No models exist yet; the package exports Base only."""
    import orchestrator.infrastructure.persistence as pkg

    assert hasattr(pkg, "Base")
    assert pkg.__all__ == ["Base"]


# --- alembic.ini ------------------------------------------------------------


def test_alembic_ini_uses_asyncpg_driver() -> None:
    ini_path = _SERVICE_ROOT / "alembic.ini"
    parser = configparser.RawConfigParser()
    parser.read(ini_path)
    url = parser.get("alembic", "sqlalchemy.url")
    assert "postgresql+asyncpg" in url
    assert "postgresql+psycopg" not in url


def test_alembic_ini_script_location_resolves_to_migrations_dir() -> None:
    ini_path = _SERVICE_ROOT / "alembic.ini"
    parser = configparser.RawConfigParser()
    parser.read(ini_path)
    raw = parser.get("alembic", "script_location")
    # Resolve %(here)s — Alembic's interpolation for the ini file's directory.
    resolved = raw.replace("%(here)s", str(_SERVICE_ROOT))
    migrations_dir = Path(resolved)
    assert migrations_dir.is_dir(), f"{migrations_dir} should exist"
    assert migrations_dir.name == "migrations"
    assert str(migrations_dir).endswith("src/orchestrator/infrastructure/persistence/migrations")
