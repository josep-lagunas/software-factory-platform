#!/usr/bin/env python3
"""
SFP — intra-service foreign-key enforcement lint (ID-058, SFP-235).

ID-058 (as amended by SFP-234) makes intra-service cross-aggregate references
**real SQL foreign keys** — the ``UserExternalIdentity.user_id ->
business.users.user_id`` precedent — and permits a plain identifier column
*only* as a **declared deferral** when the target table does not yet exist (the
"bidirectional deferral protocol"). The SFP-101 ``Ticket.project_id``
mis-citation (a plain ``project_id`` framed as a §6.5 policy) is the failure
mode this lint exists to prevent by construction.

This tool introspects each service's ORM ``Base.metadata`` (the FK source of
truth — **not** the Alembic migrations) and verifies every ``_id``-suffixed
reference column is one of:

  1. a real foreign key (``column.foreign_keys`` non-empty — tolerant of
     ``Column(ForeignKey(...))``, ``mapped_column(ForeignKey(...))``, and
     table-level ``ForeignKeyConstraint(...)``);
  2. a **declared deferral** — the target table is (currently) absent from this
     service's metadata AND the column carries an ``info`` marker
     ``{"deferred_fk": "<schema.table.column>", "blocked_on": "<SFP-XXX>"}``,
     i.e. the obligation is declared, not silent;
  3. a **cross-service identifier** — the inferred target table is not in this
     service's metadata at all (necessarily plain per AP-001 / MAS §7.9).

The single failing case is a plain ``_id`` column whose inferred target table
**IS** present in the same metadata but which carries no FK — a same-service
reference that lost its referential integrity.

Primary-key columns are excluded: a ``<entity>_id`` PK is the entity's own
identifier, not a reference, and must not be flagged.

Stdlib + SQLAlchemy only. Deterministic: tables and columns are iterated in
sorted order so the violation list is stable.

Usage:
    python3 tools/fk_lint.py identity orchestrator   # check listed services
    python3 tools/fk_lint.py --all                    # check every known service
    python3 tools/fk_lint.py --list                   # list known services

Exit status: 0 iff zero violations across every checked service, else 1.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy.schema import Column, Table

# ============================================================
# SERVICE REGISTRY — canonical name -> (dir under services/, persistence module)
# ============================================================

REPO_ROOT = Path(__file__).resolve().parents[1]

# Each value is (directory under services/, importable persistence module path).
# Importing the persistence package registers every mapped table on the
# service's ``Base.metadata`` (each service's ``__init__`` re-exports its
# models). The ``external-events`` directory uses a hyphen; the Python package
# is ``external_events`` (underscore).
SERVICE_REGISTRY: dict[str, tuple[str, str]] = {
    "identity": ("identity", "identity.infrastructure.persistence"),
    "orchestrator": ("orchestrator", "orchestrator.infrastructure.persistence"),
    "communication": ("communication", "communication.infrastructure.persistence"),
    "external-events": ("external-events", "external_events.infrastructure.persistence"),
}

# Accept the underscore spelling as an alias so callers need not remember the
# hyphen (matches the Python package name).
_ALIAS = {"external_events": "external-events"}


def _resolve_service(name: str) -> str:
    return _ALIAS.get(name, name)


# ============================================================
# TARGET-TABLE INFERENCE
# ============================================================


def _plural_candidates(stem: str) -> set[str]:
    """Plausible plural table names for a singular ``stem``.

    Generates the small set of English plurals that match the platform's
    ``plural snake_case`` table convention (ID-058). Membership is confirmed
    against the metadata's *actual* table names downstream, so an imperfect
    plural rule never produces a false positive — it only needs to generate
    the *real* name when one exists.
    """
    candidates: set[str] = set()
    if len(stem) >= 2 and stem[-1] == "y" and stem[-2] not in "aeiou":
        candidates.add(stem[:-1] + "ies")
    elif stem.endswith(("s", "x", "z", "ch", "sh")):
        candidates.add(stem + "es")
    candidates.add(stem + "s")
    return candidates


def infer_target_table(column_name: str, table_names: set[str]) -> str | None:
    """Derive the same-service target table implied by an ``_id`` column name.

    Implements the convention from ID-058 / SFP-235: drop the ``_id`` suffix to
    get the singular entity, pluralize to the table name (``project_id`` ->
    ``projects``, ``user_id`` -> ``users``). Returns the matching *real* table
    name from ``table_names``, or ``None`` if no candidate is present — i.e. the
    target is not a same-service table (it is cross-service or not-yet-existing).

    Only the full stem is pluralized (not sub-segments of compound names), so a
    column like ``provider_user_id`` -> ``provider_users`` correctly does NOT
    match a ``users`` table — it is not a reference to ``users``.
    """
    if not column_name.endswith("_id"):
        return None
    stem = column_name[: -len("_id")]
    if not stem:
        return None
    for candidate in _plural_candidates(stem):
        if candidate in table_names:
            return candidate
    return None


# ============================================================
# THE RULE — classify every ``_id`` column; collect violations + deferrals
# ============================================================


def _location(table: Table, column: Column) -> str:
    qualified = f"{table.schema}.{table.name}" if table.schema else table.name
    return f"{qualified}.{column.name}"


def _classify(metadata: MetaData) -> tuple[list[str], list[str]]:
    """Walk every ``_id`` column in ``metadata``.

    Returns ``(violations, deferrals)``:
    - ``violations`` — same-service ``_id`` reference with no FK (the only
      failure mode);
    - ``deferrals`` — declared deferrals (plain column, target absent, marker
      present), reported for visibility ("a deferral is never silent") but NOT
      counted as violations.

    Both PK columns and FK-bearing columns are skipped (PKs are the entity's
    own identifier; FKs already enforce the reference).
    """
    table_names = {t.name for t in metadata.tables.values()}
    violations: list[str] = []
    deferrals: list[str] = []
    for table in sorted(metadata.tables.values(), key=lambda t: (t.schema or "", t.name)):
        for column in sorted(table.columns, key=lambda c: c.name):
            if not column.name.endswith("_id"):
                continue
            if column.primary_key:
                continue
            if column.foreign_keys:
                continue  # real FK in any style -> OK
            target = infer_target_table(column.name, table_names)
            if target is not None:
                violations.append(
                    f"{_location(table, column)}: plain `{column.name}` references "
                    f"same-service table `{target}`; must be a real FOREIGN KEY "
                    f"(ID-058 intra-service FK policy)."
                )
                continue
            # Target absent: cross-service OR a declared deferral. A present
            # `deferred_fk` marker declares the deferral (target lands later,
            # in this same service); its absence means cross-service. Both pass;
            # the marker is surfaced so the deferral is never silent.
            info = column.info or {}
            deferred_fk = info.get("deferred_fk")
            if isinstance(deferred_fk, str) and deferred_fk:
                blocked_on = info.get("blocked_on", "<unspecified>")
                deferrals.append(
                    f"{_location(table, column)}: declared deferral -> "
                    f"{deferred_fk}, blocked on {blocked_on} (ID-058)."
                )
    return violations, deferrals


def find_violations(metadata: MetaData) -> list[str]:
    """Return the list of intra-service FK violations in ``metadata`` (empty == clean)."""
    return _classify(metadata)[0]


def find_deferred(metadata: MetaData) -> list[str]:
    """Return declared-deferral descriptions for ``metadata`` (info-only; never violations)."""
    return _classify(metadata)[1]


# ============================================================
# SERVICE LOADING
# ============================================================


def load_service_metadata(service: str) -> MetaData:
    """Import ``service``'s persistence package and return its ``Base.metadata``.

    The service's ``src/`` directory is prepended to ``sys.path`` so the
    service's absolute imports resolve even if the workspace package was not
    installed (CI installs all packages via ``uv sync --all-packages``; this
    path insertion makes the tool robust either way).
    """
    canonical = _resolve_service(service)
    if canonical not in SERVICE_REGISTRY:
        known = ", ".join(sorted(SERVICE_REGISTRY))
        raise ValueError(f"unknown service {service!r}; known: {known}")
    directory, module_path = SERVICE_REGISTRY[canonical]
    src_dir = REPO_ROOT / "services" / directory / "src"
    if src_dir.is_dir() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    module = importlib.import_module(module_path)
    base = getattr(module, "Base", None)
    if base is None:
        raise AttributeError(f"{module_path} does not expose a `Base` declarative class")
    return base.metadata


def check_service(service: str) -> tuple[list[str], list[str]]:
    """Run the rule against one service. Returns ``(violations, deferrals)``."""
    return _classify(load_service_metadata(service))


# ============================================================
# CLI
# ============================================================


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="fk_lint.py",
        description="Enforce intra-service FK policy by introspecting ORM metadata (ID-058).",
    )
    p.add_argument(
        "services",
        nargs="*",
        help="service name(s) to check (e.g. identity orchestrator); "
        "external_events is accepted as a spelling of external-events",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="check every service in the registry",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="list known services and exit",
    )
    return p.parse_args(argv)


def _print_service_result(service: str, violations: list[str], deferrals: list[str]) -> None:
    if violations:
        print(f"{service}: FAIL ({len(violations)} violation(s))")
        for v in violations:
            print(f"  - {v}")
        return
    if deferrals:
        joined = "; ".join(deferrals)
        print(f"{service}: OK ({len(deferrals)} declared deferral(s): {joined})")
    else:
        print(f"{service}: OK")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list:
        for name in sorted(SERVICE_REGISTRY):
            print(name)
        return 0

    if args.all:
        services = sorted(SERVICE_REGISTRY)
    elif args.services:
        services = [_resolve_service(s) for s in args.services]
        unknown = [s for s in services if s not in SERVICE_REGISTRY]
        if unknown:
            known = ", ".join(sorted(SERVICE_REGISTRY))
            print(
                f"error: unknown service(s): {', '.join(unknown)}; known: {known}", file=sys.stderr
            )
            return 1
    else:
        print(
            "error: provide one or more service names, or --all (use --list to enumerate)",
            file=sys.stderr,
        )
        return 1

    total_violations = 0
    for service in services:
        try:
            violations, deferrals = check_service(service)
        except Exception as exc:  # noqa: BLE001 — surface any load failure as a lint error
            print(f"{service}: ERROR loading metadata: {exc}", file=sys.stderr)
            total_violations += 1
            continue
        _print_service_result(service, violations, deferrals)
        total_violations += len(violations)

    return 0 if total_violations == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
