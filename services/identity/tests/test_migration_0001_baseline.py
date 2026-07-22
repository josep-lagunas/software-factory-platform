"""Tests for the 0001 baseline migration (SFP-111, ID-058).

Validates the Alembic baseline migration structurally — revision identifiers,
callable entry points, and source-content checks (CREATE SCHEMA + both tables,
NO trigger/function), plus ``alembic.ini`` script_location resolution.
No live DB is required; the migration module is loaded from file and inspected
via ``inspect.getsource``.
"""

from __future__ import annotations

import configparser
import importlib.util
import inspect
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION_PATH = (
    _SERVICE_ROOT
    / "src"
    / "identity"
    / "infrastructure"
    / "persistence"
    / "migrations"
    / "versions"
    / "0001_create_identity_baseline.py"
)


def _load_migration_module() -> object:
    spec = importlib.util.spec_from_file_location("migration_0001_baseline", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration_module()


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


def test_upgrade_creates_schema_and_both_tables_without_triggers() -> None:
    source = inspect.getsource(migration.upgrade)  # type: ignore[arg-type]
    # Must create the business schema and both tables.
    assert "CREATE SCHEMA" in source
    assert "create_table" in source
    assert "users" in source
    assert "user_external_identities" in source
    # Must NOT contain any trigger / function auto-fill mechanism (SFP-111
    # correction: triggers deferred to a separate follow-up ticket).
    assert "set_updated_at" not in source
    assert "BEFORE UPDATE" not in source
    assert "CREATE TRIGGER" not in source
    assert "CREATE FUNCTION" not in source


def test_downgrade_drops_both_tables() -> None:
    source = inspect.getsource(migration.downgrade)  # type: ignore[arg-type]
    assert "drop_table" in source
    assert "user_external_identities" in source
    assert "users" in source


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
    assert str(migrations_dir).endswith("src/identity/infrastructure/persistence/migrations")
