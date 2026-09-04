"""Deterministic diff-surface test scoping for Coder internal cycles (SFP-248).

A **pure, total, fail-closed** function — :func:`compute_test_scope` — maps the
set of changed paths to the pytest selection the Coder should use for its
INTERNAL edit→test cycles. It reduces per-cycle cost by running only the tests
the diff surface requires, while the FINAL pre-PR gate (full ``uv run pytest
--cov`` + coverage >=90%, plus build/lint via :mod:`workspace_worker.exec`) stays
exactly as today — this module never touches it.

Classification rules (evaluated in order; EVERY unmatched shape yields the FULL
suite — totality by fail-closed default):

1. **Shared-surface guard — FULL**: any change under ``packages/``
   (``sfp-contracts`` is the canonical strict surface; every package is a
   shared dependency of the services, so no package change may be scoped), or
   any ``pyproject.toml`` / ``conftest.py`` at ANY level (root, service,
   package — a build/test-config change can affect collection everywhere).
2. **Single-service rule — that service's tests**: changes confined to ONE
   service's ``src/`` + ``tests/`` subtrees scope to
   ``services/<svc>/tests`` plus the fixed importer set from
   :data:`IMPORTER_MAP` (currently empty for every service — verified by the
   exhaustive test, so this reduces to that service's tests alone). New
   isolated files (no importers) need no special casing — they already reduce
   to the service's tests.
3. **Multi-service rule — FULL**: a diff touching ``src``/``tests`` of 2+
   distinct services. Unions of scopes are NEVER computed — the full suite is
   computed instead (cheaper to reason about than a stitched union).
4. **Fallback — FULL**: any path outside ``services/`` and ``packages/``
   (``tools/``, ``docs/``, ``scripts/``, ``infrastructure/``, root
   ``tests/``, root config), any path inside ``services/<svc>/`` but outside
   its ``src/``/``tests/`` (``alembic/``, ``alembic.ini``, ``README.md``,
   …), a directory-only or malformed path, an unknown service directory, or
   an EMPTY input.

The RULE lives here as data + code — never as prose scattered in the Coder
prompt. The prompt (:doc:`the Coder implement fragment`) tells the Coder to
COMPUTE the scope via this helper (``compute_test_scope``), not to re-derive it,
and pins the widening rule: on any scoped-run failure the Coder widens (up to
the full suite) before concluding; silently passing on a narrow run alone is
forbidden.

Determinism (MAS §12.7): a pure function of its input — no clock, no network,
no filesystem access; the same input always yields the same
:data:`TestScope` (path arguments are emitted sorted).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import ClassVar

__all__ = [
    "FULL_SUITE",
    "IMPORTER_MAP",
    "TestScope",
    "compute_test_scope",
]

#: Service directory name (``services/<name>``) → the fixed set of ADDITIONAL
#: test-path roots that must run alongside that service's own tests when a diff
#: is confined to that service (rule 2). Values are repo-relative test-path
#: roots (e.g. ``"services/orchestrator/tests"``), NOT service names.
#:
#: Currently EMPTY for every service: no package imports a service and no
#: service imports another service (both facts are asserted by the exhaustive
#: partition test — if an import ever appears, that test fails and forces this
#: map to be updated with the importing side's test root). An empty set means
#: "that service's tests alone".
IMPORTER_MAP: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "communication": frozenset(),
        "external-events": frozenset(),
        "identity": frozenset(),
        "orchestrator": frozenset(),
        "workspace-worker": frozenset(),
    }
)

#: The base argv every internal pytest cycle prefixes. Deliberately WITHOUT
#: ``--cov``: the coverage gate belongs to the FINAL pre-PR gate
#: (``exec/tests.py``'s ``['uv', 'run', 'pytest', '--cov']``), which this
#: scoping never replaces — internal cycles must not pay the coverage
#: instrumentation cost.
_PYTEST_BASE_ARGV: tuple[str, ...] = ("uv", "run", "pytest")

#: Config file basenames that force the FULL suite wherever they appear
#: (rule 1): a build/test-config change can alter collection or dependency
#: resolution everywhere, so it is never scoped.
_FULL_SUITE_FILENAMES: frozenset[str] = frozenset({"pyproject.toml", "conftest.py"})

#: The only subtrees of a service directory that scope (rule 2). Everything
#: else inside ``services/<svc>/`` (``alembic/``, service-level config, docs)
#: falls back to FULL.
_SERVICE_SCOPED_SUBDIRS: frozenset[str] = frozenset({"src", "tests"})


@dataclass(frozen=True, slots=True)
class TestScope:
    """The pytest selection for one Coder internal test cycle (SFP-248).

    Two shapes only:

    * **FULL** — :attr:`is_full` is ``True`` and :attr:`test_paths` is empty;
      the invocation is bare ``uv run pytest`` (the whole suite, exactly as the
      final gate runs it, minus ``--cov``).
    * **SCOPED** — :attr:`is_full` is ``False`` and :attr:`test_paths` holds
      the sorted, repo-relative test-path roots to pass to pytest.

    Attributes:
        is_full: ``True`` iff the full suite must run.
        test_paths: Sorted pytest path arguments; EMPTY iff :attr:`is_full`.
    """

    # Not a pytest test class despite the ``Test*`` name (exec/tests.py
    # precedent: TestError/TestResult carry the same opt-out).
    __test__: ClassVar[bool] = False

    is_full: bool
    test_paths: tuple[str, ...]

    def pytest_argv(self) -> tuple[str, ...]:
        """Return the concrete pytest argv for this scope.

        FULL → ``("uv", "run", "pytest")``; SCOPED → the same prefix plus the
        sorted :attr:`test_paths` (e.g. ``("uv", "run", "pytest",
        "services/workspace-worker/tests")``).
        """
        if self.is_full:
            return _PYTEST_BASE_ARGV
        return _PYTEST_BASE_ARGV + self.test_paths


#: The fail-closed singleton every unmatched input reduces to.
FULL_SUITE = TestScope(is_full=True, test_paths=())


def _normalize(raw: str) -> str:
    """Strip surrounding whitespace and leading ``./`` / ``/`` decorations.

    Deterministic, pure string surgery — the classifier reasons over one
    canonical POSIX form regardless of how the caller spelled the path.
    (``str.lstrip("./")`` would eat dots inside names like ``.gitignore``;
    the explicit loop does not.)
    """
    path = raw.strip()
    while path.startswith("./"):
        path = path[2:]
    return path.removeprefix("/")


def compute_test_scope(changed_paths: AbstractSet[str] | Sequence[str]) -> TestScope:
    """Map a changed-path set to the pytest scope for one internal cycle.

    Pure and total: the same input always yields the same :class:`TestScope`;
    no clock, network, or filesystem access. Fail-closed: EVERY unmatched or
    degenerate input (empty set, blank path, bare ``services``, unknown service
    directory, path outside ``services/``/``packages/``, non-``src``/``tests``
    path inside a service, config file anywhere) yields :data:`FULL_SUITE`.

    Args:
        changed_paths: The repo-relative changed paths (added / modified /
            deleted alike — classification is by path shape only). A ``set`` is
            the canonical form; any sequence (list/tuple) of the same strings
            yields the identical scope.

    Returns:
        The :class:`TestScope` — either :data:`FULL_SUITE` or a scoped
        selection of that one service's tests plus its :data:`IMPORTER_MAP`
        importer roots.

    Rules (see the module docstring for the full table):

    1. any path under ``packages/`` or named ``pyproject.toml`` /
       ``conftest.py`` at any level → :data:`FULL_SUITE`;
    2. paths confined to one service's ``src/`` + ``tests/`` → that service's
       tests + :data:`IMPORTER_MAP[service]` (currently empty everywhere);
    3. paths touching ``src``/``tests`` of 2+ distinct services →
       :data:`FULL_SUITE` (unions are never computed);
    4. anything else → :data:`FULL_SUITE`.
    """
    # Totality guard: an empty diff gives nothing to narrow on — run everything.
    if not changed_paths:
        return FULL_SUITE

    services: set[str] = set()
    for raw in changed_paths:
        path = _normalize(raw)
        # Blank/whitespace path: malformed input — fail closed, never skip.
        if not path:
            return FULL_SUITE

        # Rule 1a — config files at ANY level force the full suite.
        if PurePosixPath(path).name in _FULL_SUITE_FILENAMES:
            return FULL_SUITE

        parts = PurePosixPath(path).parts
        if not parts:  # pragma: no cover — PurePosixPath never splits to empty
            return FULL_SUITE

        # Rule 1b — every package is a shared dependency of the services
        # (sfp-contracts is the canonical strict surface): never scoped.
        if parts[0] == "packages":
            return FULL_SUITE

        # Rule 4a — anything outside services/ (tools/, docs/, scripts/,
        # infrastructure/, root tests/, root config, …) falls back to FULL.
        if parts[0] != "services":
            return FULL_SUITE

        # Rule 4b — "services" alone, or a file sitting directly under
        # services/: unmatched shape.
        if len(parts) < 2 or not parts[1]:
            return FULL_SUITE
        service_dir = parts[1]

        # Rule 4c — only src/ and tests/ subtrees scope; everything else in
        # the service dir (alembic/, alembic.ini, README, …) is FULL. The
        # length guard also covers a bare "services/<svc>/src" directory path.
        if len(parts) < 4 or parts[2] not in _SERVICE_SCOPED_SUBDIRS:
            return FULL_SUITE

        services.add(service_dir)

    # Rule 3 — a diff spanning 2+ services NEVER unions scopes: full suite.
    # (services is empty only when every path was blank-handled above, which
    # already returned; the guard keeps the function total regardless.)
    if len(services) != 1:
        return FULL_SUITE

    service_dir = next(iter(services))

    # Rule 4d — a services/<name> directory the map does not know (a new or
    # renamed service) cannot be scoped: fail closed until the map is updated.
    try:
        importer_roots = IMPORTER_MAP[service_dir]
    except KeyError:
        return FULL_SUITE

    # Rule 2 — that service's tests plus its fixed importer set (currently
    # empty for every service ⇒ the service's tests alone), sorted for
    # determinism.
    test_paths = {f"services/{service_dir}/tests", *importer_roots}
    return TestScope(is_full=False, test_paths=tuple(sorted(test_paths)))
