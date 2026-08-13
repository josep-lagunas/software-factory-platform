"""Service enumeration for the local init container (SFP-79).

Inlines the four ``SERVICE_REGISTRY`` entries from ``tools/fk_lint.py``
(identity, orchestrator, communication, external-events) — the canonical name ->
(directory, persistence module) mapping — and extends each entry with the
provisioning metadata the init container needs: the logical database name, the
SQLAlchemy driver, the ``alembic.ini`` location, and the ``DATABASE_URL``
environment variable the service's ``env.py`` reads.

The PRSpec is explicit: do NOT extract this to a shared module in this ticket —
inline the four entries. The same ``importlib.import_module`` + ``getattr(module,
"Base")`` mechanism from ``tools/fk_lint.py`` resolves each service's declarative
``Base``; a service whose persistence module fails to import, exposes no
``Base``, or whose ``Base.metadata`` has no mapped tables is reported for
skipping (log-and-continue) rather than failing the run.

Library module (coverage-gated). Imports SQLAlchemy (already a workspace
dependency) and stdlib only — importable without ``pulumi`` or a live DB.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from sqlalchemy.orm import DeclarativeBase

#: Repository root. Defaults to four parents above this file (correct for the
#: dev tree and tests, where this lives at ``<repo>/infrastructure/local/init/``);
#: overridable via ``SFP_REPO_ROOT`` so the init container can point at its
#: mounted workspace (``/workspace``) regardless of where the script is copied.
REPO_ROOT = Path(os.environ.get("SFP_REPO_ROOT", Path(__file__).resolve().parents[3]))

#: Accept the underscore spelling as an alias (matches the Python package name),
#: so callers need not remember the hyphen in ``external-events``.
_ALIAS: dict[str, str] = {"external_events": "external-events"}


def _resolve_name(name: str) -> str:
    """Return the canonical (hyphen) registry key for ``name``."""
    return _ALIAS.get(name, name)


@dataclass(frozen=True)
class ServiceEntry:
    """One service's provisioning metadata.

    Attributes:
        name: Canonical registry key (hyphenated, e.g. ``external-events``).
        directory: Directory under ``services/`` (matches the on-disk folder).
        module_path: Importable persistence module path whose ``Base`` holds the
            service's ORM metadata (same mapping as ``tools/fk_lint.py``).
        database: Logical database name created by ``postgres/init.sh``.
        driver: SQLAlchemy driver token — ``psycopg`` or ``asyncpg``.
        alembic_ini: Path to the service's ``alembic.ini`` relative to the
            service directory.
        env_var: Environment variable the service's ``env.py`` reads for its
            live database URL (also used to name the override uniformly).
    """

    name: str
    directory: str
    module_path: str
    database: str
    driver: str
    alembic_ini: str
    env_var: str

    @property
    def service_dir(self) -> Path:
        """Absolute path to the service directory under ``services/``."""
        return REPO_ROOT / "services" / self.directory

    @property
    def src_dir(self) -> Path:
        """Absolute path to the service ``src/`` tree (for ``PYTHONPATH``)."""
        return self.service_dir / "src"

    @property
    def alembic_ini_path(self) -> Path:
        """Absolute path to the service's ``alembic.ini``."""
        return self.service_dir / self.alembic_ini

    def database_url(self, *, host: str, port: int, user: str, password: str) -> str:
        """Build the live SQLAlchemy URL for this service's logical database.

        The driver token selects between the synchronous (``psycopg``) and
        asynchronous (``asyncpg``) postgres drivers — the orchestrator baseline
        (SFP-99) is async; the others are sync.
        """
        return f"postgresql+{self.driver}://{user}:{password}@{host}:{port}/{self.database}"


#: The four inlined ``SERVICE_REGISTRY`` entries. Order is the deterministic
#: provisioning order (identity -> orchestrator -> communication ->
#: external-events); matches ``postgres/init.sh``'s ``DATABASES`` ordering and
#: ``tools/fk_lint.py``'s declaration order.
SERVICE_REGISTRY: tuple[ServiceEntry, ...] = (
    ServiceEntry(
        name="identity",
        directory="identity",
        module_path="identity.infrastructure.persistence",
        database="identity",
        driver="psycopg",
        alembic_ini="alembic.ini",
        env_var="IDENTITY_DATABASE_URL",
    ),
    ServiceEntry(
        name="orchestrator",
        directory="orchestrator",
        module_path="orchestrator.infrastructure.persistence",
        database="orchestrator",
        driver="asyncpg",
        alembic_ini="alembic.ini",
        env_var="ORCHESTRATOR_DATABASE_URL",
    ),
    ServiceEntry(
        name="communication",
        directory="communication",
        module_path="communication.infrastructure.persistence",
        database="communication",
        driver="psycopg",
        alembic_ini="alembic.ini",
        env_var="COMMUNICATION_DATABASE_URL",
    ),
    ServiceEntry(
        name="external-events",
        directory="external-events",
        module_path="external_events.infrastructure.persistence",
        database="external_events",
        driver="psycopg",
        alembic_ini="alembic.ini",
        env_var="EXTERNAL_EVENTS_DATABASE_URL",
    ),
)


def all_services() -> tuple[ServiceEntry, ...]:
    """Return the full registry in deterministic provisioning order."""
    return SERVICE_REGISTRY


def get_service(name: str) -> ServiceEntry:
    """Look up a single service by name (underscore alias accepted).

    Raises :class:`ValueError` for an unknown service so the caller fails loud
    rather than silently skipping a typo.
    """
    canonical = _resolve_name(name)
    for entry in SERVICE_REGISTRY:
        if entry.name == canonical:
            return entry
    known = ", ".join(entry.name for entry in SERVICE_REGISTRY)
    raise ValueError(f"unknown service {name!r}; known: {known}")


class ServiceLoadError(Exception):
    """Raised when a service's persistence module cannot be introspected.

    Carries the service name and the underlying message so :func:`load_base`
    callers can log a precise skip reason (import error vs. missing ``Base``)
    without losing the root cause.
    """

    def __init__(self, service: str, reason: str) -> None:
        self.service = service
        self.reason = reason
        super().__init__(f"{service}: {reason}")


def load_base(entry: ServiceEntry) -> type[DeclarativeBase]:
    """Import a service's persistence module and return its declarative ``Base`` *class*.

    Mirrors ``tools/fk_lint.py``'s ``load_service_metadata``: prepend the
    service ``src/`` tree to ``sys.path`` so absolute imports resolve, then
    ``importlib.import_module`` the persistence package and ``getattr`` the
    ``Base``. Raises :class:`ServiceLoadError` if the module cannot be imported
    or exposes no ``Base`` — the caller decides whether to skip or fail.
    """
    if entry.src_dir.is_dir() and str(entry.src_dir) not in sys.path:
        sys.path.insert(0, str(entry.src_dir))
    try:
        module = importlib.import_module(entry.module_path)
    except Exception as exc:  # noqa: BLE001 — any import failure -> skip signal
        raise ServiceLoadError(entry.name, f"import failed: {exc}") from exc
    base = getattr(module, "Base", None)
    if base is None:
        raise ServiceLoadError(entry.name, "persistence module exposes no `Base`")
    return cast("type[DeclarativeBase]", base)


def has_mapped_tables(base: type[DeclarativeBase]) -> bool:
    """Return whether ``base`` registers any ORM table on its metadata.

    A service with a ``Base`` but no mapped tables (no models yet) is skipped at
    INFO — there is nothing to migrate. Only services with real tables are
    migrated.
    """
    return bool(base.metadata.tables)


def service_names(entries: Sequence[ServiceEntry] = SERVICE_REGISTRY) -> list[str]:
    """Return the list of registry names (convenience for logging)."""
    return [entry.name for entry in entries]
