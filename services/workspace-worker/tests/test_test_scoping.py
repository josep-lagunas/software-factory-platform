"""Tests for :mod:`workspace_worker.entrypoints.test_scoping` (SFP-248).

Three families:

* **Unit** — the five classification rules of :func:`compute_test_scope`
  (contracts→FULL, single-service→service tests, isolated new file→service
  tests, multi-service→FULL, external-path→FULL) plus the fail-closed totality
  cases (empty input, unmatched shapes, unknown service) and the purity pins
  (set vs list input, normalization, sorted emission, frozen data).
* **Exhaustive path map (SFP-145-style partition test)** — every directory
  under ``services/`` and ``packages/`` in the REPO maps to exactly one rule
  outcome, and the :data:`IMPORTER_MAP` keys are exactly the real service
  directories. If a new service/package directory appears, a package gains a
  service import, or a service imports another service, this test FAILS and
  forces a map update — scoping can never silently drift from reality.
* **Demonstration** — a scoped pytest invocation for an isolated-module change
  and a FULL invocation for a contracts change, asserted on the concrete
  ``pytest_argv()`` (the run-log of the scoped internal cycle).

The FINAL gate is out of scope here by design (PRSpec out-of-scope): its argv
pin lives in ``test_exec_tests.py`` and is untouched.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from workspace_worker.entrypoints.test_scoping import (
    FULL_SUITE,
    IMPORTER_MAP,
    TestScope,
    compute_test_scope,
)
from workspace_worker.entrypoints.ticket_pipeline import (  # re-export site (SFP-248)
    IMPORTER_MAP as IMPORTER_MAP_VIA_PIPELINE,
)
from workspace_worker.entrypoints.ticket_pipeline import (
    TestScope as TestScope_VIA_PIPELINE,
)
from workspace_worker.entrypoints.ticket_pipeline import (
    compute_test_scope as compute_test_scope_via_pipeline,
)

# Repo root = services/workspace-worker/tests/ → up four levels.
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: The real service directories on disk, discovered (NOT hardcoded) so a new
#: service directory automatically shows up in the exhaustive partition test
#: and FAILS it until IMPORTER_MAP is taught the newcomer.
_SERVICE_DIRS = sorted(p.name for p in (_REPO_ROOT / "services").iterdir() if p.is_dir())

#: The real package directories on disk, same discovery rationale.
_PACKAGE_DIRS = sorted(p.name for p in (_REPO_ROOT / "packages").iterdir() if p.is_dir())


# --- Unit — rule 1: shared surface → FULL -------------------------------------


class TestRuleOneSharedSurfaceIsFull:
    def test_any_sfp_contracts_change_is_full(self) -> None:
        scope = compute_test_scope({"packages/sfp-contracts/src/sfp_contracts/agents/coder.py"})
        assert scope == FULL_SUITE
        assert scope.is_full

    def test_every_package_on_disk_classifies_full(self) -> None:
        # Any path under packages/<name>/ — enumerated from disk so a NEW
        # package directory is automatically covered by this rule test.
        for pkg in _PACKAGE_DIRS:
            scope = compute_test_scope({f"packages/{pkg}/src/{pkg.replace('-', '_')}/x.py"})
            assert scope == FULL_SUITE, f"packages/{pkg} must scope FULL"

    def test_package_test_change_is_full(self) -> None:
        assert compute_test_scope({"packages/sfp-testing/tests/test_x.py"}) == FULL_SUITE

    def test_pyproject_at_any_level_is_full(self) -> None:
        for path in (
            "pyproject.toml",  # root
            "services/workspace-worker/pyproject.toml",  # service
            "packages/sfp-contracts/pyproject.toml",  # package
        ):
            assert compute_test_scope({path}) == FULL_SUITE, path

    def test_conftest_at_any_level_is_full(self) -> None:
        for path in (
            "conftest.py",
            "services/identity/tests/conftest.py",
            "packages/sfp-messaging/conftest.py",
        ):
            assert compute_test_scope({path}) == FULL_SUITE, path

    def test_mixed_contracts_and_service_change_is_full(self) -> None:
        # The guard fires even when the rest of the diff is a single service.
        scope = compute_test_scope(
            {
                "services/orchestrator/src/orchestrator/x.py",
                "packages/sfp-contracts/src/sfp_contracts/y.py",
            }
        )
        assert scope == FULL_SUITE


# --- Unit — rule 2: single service → that service's tests ---------------------


class TestRuleTwoSingleServiceScopes:
    @pytest.mark.parametrize("service_dir", _SERVICE_DIRS)
    def test_src_plus_tests_change_scopes_to_service_tests(self, service_dir: str) -> None:
        scope = compute_test_scope(
            {
                f"services/{service_dir}/src/{service_dir.replace('-', '_')}/mod.py",
                f"services/{service_dir}/tests/test_mod.py",
            }
        )
        assert not scope.is_full
        assert scope.test_paths == (f"services/{service_dir}/tests",)

    def test_new_isolated_file_with_no_importers_scopes_to_service_tests(self) -> None:
        # Rule 3 of the PRSpec: an isolated NEW module nothing imports still
        # scopes to the service's tests — no special casing needed.
        scope = compute_test_scope(
            {"services/workspace-worker/src/workspace_worker/isolated_new.py"}
        )
        assert scope == TestScope(is_full=False, test_paths=("services/workspace-worker/tests",))

    def test_test_only_change_scopes_to_service_tests(self) -> None:
        scope = compute_test_scope({"services/identity/tests/test_login.py"})
        assert scope.test_paths == ("services/identity/tests",)

    def test_argv_is_service_scoped(self) -> None:
        scope = compute_test_scope({"services/orchestrator/src/orchestrator/a.py"})
        assert scope.pytest_argv() == (
            "uv",
            "run",
            "pytest",
            "services/orchestrator/tests",
        )


# --- Unit — rule 3: multi-service → FULL (never unions) -----------------------


class TestRuleThreeMultiServiceIsFull:
    def test_two_services_is_full(self) -> None:
        scope = compute_test_scope(
            {
                "services/identity/src/identity/a.py",
                "services/communication/src/communication/b.py",
            }
        )
        assert scope == FULL_SUITE

    def test_two_services_via_tests_is_full(self) -> None:
        scope = compute_test_scope(
            {
                "services/identity/tests/test_a.py",
                "services/communication/tests/test_b.py",
            }
        )
        assert scope == FULL_SUITE

    def test_three_services_is_full(self) -> None:
        scope = compute_test_scope(
            {
                "services/identity/src/identity/a.py",
                "services/orchestrator/src/orchestrator/b.py",
                "services/external-events/src/external_events/c.py",
            }
        )
        assert scope == FULL_SUITE

    def test_no_union_is_ever_computed(self) -> None:
        # The anti-union pin: a 2-service diff must NOT produce a 2-path scope.
        scope = compute_test_scope(
            {
                "services/identity/tests/test_a.py",
                "services/communication/tests/test_b.py",
            }
        )
        assert scope.is_full
        assert scope.test_paths == ()


# --- Unit — rule 4/5: external paths + unmatched → FULL (fail-closed) ---------


class TestFallbackIsFull:
    @pytest.mark.parametrize(
        "path",
        [
            "tools/create_sfp_ticket.py",
            "tools/check_prspec.py",
            "docs/IMPLEMENTATION_NOTES.md",
            "docs/SFP_Ticket_Hierarchy.md",
            "scripts/run_pipeline.sh",
            "infrastructure/local/compose.yaml",
            "tests/test_imports.py",  # root tests/ — outside services/
            "README.md",
            "run-ticket.sh",
            "uv.lock",
            "source-env.sh",
        ],
    )
    def test_outside_services_and_packages_is_full(self, path: str) -> None:
        assert compute_test_scope({path}) == FULL_SUITE, path

    @pytest.mark.parametrize(
        "path",
        [
            "services/workspace-worker/alembic.ini",
            "services/identity/alembic/versions/0001_x.py",
            "services/communication/README.md",
            "services/external-events/pyproject.toml",  # also rule 1 (config)
        ],
    )
    def test_service_path_outside_src_tests_is_full(self, path: str) -> None:
        assert compute_test_scope({path}) == FULL_SUITE, path

    def test_empty_set_is_full(self) -> None:
        assert compute_test_scope(set()) == FULL_SUITE

    def test_empty_list_is_full(self) -> None:
        assert compute_test_scope([]) == FULL_SUITE

    def test_blank_path_is_full(self) -> None:
        assert compute_test_scope({"", "   "}) == FULL_SUITE

    def test_bare_services_path_is_full(self) -> None:
        assert compute_test_scope({"services"}) == FULL_SUITE
        assert compute_test_scope({"services/"}) == FULL_SUITE

    def test_bare_service_directory_is_full(self) -> None:
        assert compute_test_scope({"services/workspace-worker"}) == FULL_SUITE

    def test_bare_service_src_directory_is_full(self) -> None:
        assert compute_test_scope({"services/workspace-worker/src"}) == FULL_SUITE

    def test_unknown_service_directory_is_full(self) -> None:
        # A services/<name> the map does not know: fail closed until the map
        # is updated (this is the drift-forcing backstop for rule 4d).
        assert compute_test_scope({"services/does-not-exist/src/x/y.py"}) == FULL_SUITE

    def test_service_path_missing_third_component_is_full(self) -> None:
        # "services/<svc>/<file>" — a file directly under the service dir.
        assert compute_test_scope({"services/identity/notes.txt"}) == FULL_SUITE


# --- Unit — purity, determinism, normalization --------------------------------


class TestPurityAndDeterminism:
    def test_set_and_list_inputs_agree(self) -> None:
        paths = [
            "services/orchestrator/src/orchestrator/a.py",
            "services/orchestrator/tests/test_a.py",
        ]
        assert compute_test_scope(set(paths)) == compute_test_scope(paths)

    def test_input_order_does_not_matter(self) -> None:
        a = compute_test_scope(
            {"services/identity/tests/test_a.py", "services/identity/src/i/x.py"}
        )
        b = compute_test_scope(
            {"services/identity/src/i/x.py", "services/identity/tests/test_a.py"}
        )
        assert a == b

    def test_leading_dot_slash_and_absolute_paths_normalize(self) -> None:
        assert compute_test_scope({"./services/identity/tests/test_a.py"}) == compute_test_scope(
            {"services/identity/tests/test_a.py"}
        )
        assert compute_test_scope({"/services/identity/tests/test_a.py"}) == compute_test_scope(
            {"services/identity/tests/test_a.py"}
        )

    def test_repeated_calls_yield_identical_result(self) -> None:
        paths = {"services/communication/src/communication/x.py"}
        assert compute_test_scope(paths) == compute_test_scope(paths)

    def test_importer_roots_are_emitted_sorted(self) -> None:
        # Simulated importer set (patched module binding) proves sort order + the
        # union with the service's own tests — determinism of the emission.
        import workspace_worker.entrypoints.test_scoping as mod

        simulated = frozenset({"services/orchestrator/tests", "services/x/tests"})
        saved = mod.IMPORTER_MAP
        mod.IMPORTER_MAP = {**saved, "identity": simulated}  # type: ignore[assignment]
        try:
            scope = compute_test_scope({"services/identity/src/identity/a.py"})
        finally:
            mod.IMPORTER_MAP = saved  # type: ignore[assignment]
        assert scope.test_paths == (
            "services/identity/tests",
            "services/orchestrator/tests",
            "services/x/tests",
        )

    def test_scope_is_frozen_and_hashable(self) -> None:
        scope = compute_test_scope({"services/identity/src/identity/a.py"})
        with pytest.raises(AttributeError):
            scope.is_full = True  # type: ignore[misc]
        assert hash(scope) == hash(
            TestScope(is_full=False, test_paths=("services/identity/tests",))
        )

    def test_test_scope_is_not_collected_by_pytest(self) -> None:
        # The dataclass carries the collection opt-out (exec/tests.py
        # precedent for Test* result types).
        assert TestScope.__test__ is False


# --- Exhaustive path map — SFP-145-style partition test -----------------------


class TestExhaustivePathMap:
    """Every services/*/ and packages/* path is covered by EXACTLY ONE rule.

    Enumerates the REAL repo tree (not fixtures), so any future directory
    (a new service, a new package) or any future import edge (a package
    importing a service, a service importing another service) breaks this
    class and forces an :data:`IMPORTER_MAP` update — scoping can never
    silently under-scope.
    """

    def test_every_real_service_dir_is_exactly_the_importer_map_keys(self) -> None:
        assert sorted(IMPORTER_MAP) == _SERVICE_DIRS

    def test_every_real_package_dir_maps_to_full(self) -> None:
        # Partition member 1: packages → FULL. Exhaustive over disk.
        for pkg in _PACKAGE_DIRS:
            assert compute_test_scope({f"packages/{pkg}/src/x/y.py"}).is_full

    def test_every_real_service_dir_maps_to_one_outcome(self) -> None:
        # Partition member 2: each service → its own tests. Exhaustive over
        # disk; each directory is covered by exactly this one rule.
        for svc in _SERVICE_DIRS:
            scope = compute_test_scope({f"services/{svc}/src/x/y.py", f"services/{svc}/tests/t.py"})
            assert scope == TestScope(is_full=False, test_paths=(f"services/{svc}/tests",))

    def test_partition_of_real_repo_subdirs_is_total(self) -> None:
        # Every top-level repo directory lands in exactly one bucket:
        # services/ (scoped per-service), packages/ (FULL), everything else
        # (FULL fallback). This is the partition-over-the-whole-repo pin.
        top_dirs = sorted(p.name for p in _REPO_ROOT.iterdir() if p.is_dir() and p.name != ".git")
        for name in top_dirs:
            probe = f"{name}/probe_file.py"
            scope = compute_test_scope({probe})
            if name == "services":
                # services/<unknown>/probe is unmatched inside services → FULL
                assert scope.is_full
            else:
                # packages/ and every other top-level dir → FULL
                assert scope == FULL_SUITE, probe

    def test_no_package_imports_a_service(self) -> None:
        """Static import check backing the empty importer sets (rule 2).

        Scans every package's src for an import of ANY service top-level
        module. A hit means the map's empty sets are a LIE: the service's
        consumers exist and the failing service's test root must be added to
        IMPORTER_MAP for every package consumer.
        """
        service_modules = {svc.replace("-", "_") for svc in _SERVICE_DIRS}
        offenders: list[str] = []
        for pkg in _PACKAGE_DIRS:
            src = _REPO_ROOT / "packages" / pkg / "src"
            if not src.is_dir():
                continue
            for py in src.rglob("*.py"):
                for line in py.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    hit = next(
                        (
                            mod
                            for mod in service_modules
                            if stripped.startswith(f"import {mod}")
                            or stripped.startswith(f"from {mod}")
                        ),
                        None,
                    )
                    if hit:
                        offenders.append(f"{py.relative_to(_REPO_ROOT)}: {stripped} ({hit})")
        assert not offenders, (
            "a package imports a service — IMPORTER_MAP must be updated: " + "; ".join(offenders)
        )

    def test_no_service_imports_another_service(self) -> None:
        """Static import check for cross-service edges (multi-service safety).

        If service A imports service B, a diff confined to B's src affects A's
        tests too: B's importer set in :data:`IMPORTER_MAP` must list A's test
        root. Scanned from disk so the assertion tracks reality.
        """
        service_modules = {svc.replace("-", "_"): svc for svc in _SERVICE_DIRS}
        offenders: list[str] = []
        for svc in _SERVICE_DIRS:
            own = svc.replace("-", "_")
            src = _REPO_ROOT / "services" / svc / "src"
            if not src.is_dir():
                continue
            for py in src.rglob("*.py"):
                for line in py.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    for mod, svc_dir in service_modules.items():
                        if mod == own:
                            continue
                        if stripped.startswith(f"import {mod}") or stripped.startswith(
                            f"from {mod}"
                        ):
                            offenders.append(
                                f"{py.relative_to(_REPO_ROOT)} imports {mod} — add "
                                f"services/{svc_dir}/tests to IMPORTER_MAP consumers"
                            )
        assert not offenders, "; ".join(offenders)

    def test_importer_sets_are_currently_empty_for_every_service(self) -> None:
        # Encodes TODAY'S truth (PRSpec: "currently empty for every service").
        # The two static checks above prove the emptiness is real; if this
        # starts failing alongside them, update the map, not this pin.
        assert {k: sorted(v) for k, v in IMPORTER_MAP.items()} == {svc: [] for svc in _SERVICE_DIRS}

    def test_importer_map_values_are_frozensets(self) -> None:
        for value in IMPORTER_MAP.values():
            assert isinstance(value, frozenset)


# --- Re-export site (the pipeline exposes the helper) -------------------------


class TestPipelineReExport:
    def test_pipeline_reexports_the_scoping_surface(self) -> None:
        assert compute_test_scope_via_pipeline is compute_test_scope
        assert TestScope_VIA_PIPELINE is TestScope
        assert IMPORTER_MAP_VIA_PIPELINE is IMPORTER_MAP


# --- Demonstration — scoped vs full invocation (run-log) ----------------------


class TestDemonstration:
    """PRSpec acceptance: a scoped pytest invocation for an isolated-module
    change and a FULL invocation for a contracts change, demonstrated as a
    REAL subprocess run-log (cwd = repo root, ``--collect-only -q`` so the
    demonstration collects without executing the suite).
    """

    @pytest.fixture
    def _scoped_collect_log(self) -> Iterator[str]:
        scope = compute_test_scope(
            {"services/workspace-worker/src/workspace_worker/isolated_demo_module.py"}
        )
        assert not scope.is_full, "isolated-module change must scope, not run FULL"
        argv = [*scope.pytest_argv(), "--collect-only", "-q"]
        proc = subprocess.run(argv, cwd=_REPO_ROOT, capture_output=True, text=True, check=False)
        yield (f"$ {' '.join(argv)}\nexit_code={proc.returncode}\n{proc.stdout.strip()}")

    def test_isolated_module_change_runs_scoped_pytest_invocation(
        self, _scoped_collect_log: str
    ) -> None:
        log = _scoped_collect_log
        # The invocation is scoped: exactly the service's tests, never bare.
        assert "uv run pytest services/workspace-worker/tests" in log
        assert "exit_code=0" in log, log
        # And it collected only that service's tests — no packages/ test file
        # and no other service's tests appear among the collected node ids
        # (the demonstrated cost reduction). Path-shaped pins, not bare
        # substrings: this file's own test ids legitimately contain the words.
        collected = {line.split("::")[0] for line in log.splitlines() if "::" in line}
        assert any(p.endswith("tests/test_test_scoping.py") for p in collected), log
        assert not any(p.startswith("packages/") for p in collected), collected
        assert not any(
            p.startswith("services/") and "workspace-worker" not in p for p in collected
        ), collected

    def test_contracts_change_runs_full_pytest_invocation(self) -> None:
        scope = compute_test_scope({"packages/sfp-contracts/src/sfp_contracts/agents/coder.py"})
        argv = [*scope.pytest_argv(), "--collect-only", "-q"]
        # The FULL invocation is the bare suite command — no path arguments.
        assert scope.is_full
        assert argv == ["uv", "run", "pytest", "--collect-only", "-q"]
        # Demonstrate it really is the full suite (collects across services
        # AND packages) without executing it.
        proc = subprocess.run(argv, cwd=_REPO_ROOT, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr[-500:]
        assert "services/workspace-worker" in proc.stdout
        assert "services/orchestrator" in proc.stdout
        assert "packages" in proc.stdout


# --- Final gate untouched pin --------------------------------------------------


class TestFinalGateUntouched:
    def test_exec_tests_argv_still_the_full_gate(self) -> None:
        # The FINAL gate remains bare `uv run pytest --cov` — scoping never
        # leaks into it (out-of-scope pin; the full pin lives in
        # test_exec_tests.py and is untouched by this ticket).
        from workspace_worker.exec.tests import _TEST_ARGV

        assert _TEST_ARGV == ["uv", "run", "pytest", "--cov"]

    def test_scoped_argv_has_no_cov(self) -> None:
        # Internal cycles deliberately drop --cov: the coverage gate belongs
        # to the final gate only.
        scoped = compute_test_scope({"services/identity/src/identity/a.py"})
        full = compute_test_scope({"packages/sfp-contracts/src/x.py"})
        assert "--cov" not in scoped.pytest_argv()
        assert "--cov" not in full.pytest_argv()
