"""Tests for tools/fk_lint.py — the ID-058 intra-service FK enforcement lint (SFP-235).

Cases (a)-(e) from SFP-235:
  (a) same-service ``_id`` WITH a FK        -> pass
  (b) plain ``_id`` targeting a same-metadata table (no FK) -> violation
  (c) plain ``_id`` with a valid info deferral marker (target absent) -> pass + declared
  (d) plain ``_id`` target absent, NO marker -> pass (cross-service)
  (e) backfill: the real ``Ticket.project_id`` (orchestrator) passes + is declared

Rule unit-tests (a)-(d) build small in-memory ``MetaData`` fixtures rather than
importing whole services. Case (e) imports the real orchestrator metadata to
verify the backfilled marker end-to-end.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import (
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    Table,
)

# --- import the linter directly from tools/ ---------------------------------
ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import fk_lint  # noqa: E402  (path inserted above)

# ============================================================
# Fixture builders — in-memory MetaData (no service import)
# ============================================================


def _md_with_fk() -> MetaData:
    """Case (a): parts.widget_id is a real FK -> must pass."""
    md = MetaData()
    Table(
        "widgets",
        md,
        Column("widget_id", Integer, primary_key=True),
    )
    Table(
        "parts",
        md,
        Column("part_id", Integer, primary_key=True),
        Column("widget_id", Integer, ForeignKey("widgets.widget_id")),
    )
    return md


def _md_plain_same_service() -> MetaData:
    """Case (b): parts.widget_id is plain but `widgets` is in the same metadata."""
    md = MetaData()
    Table("widgets", md, Column("widget_id", Integer, primary_key=True))
    Table(
        "parts",
        md,
        Column("part_id", Integer, primary_key=True),
        Column("widget_id", Integer),  # plain — no FK, same-service target present
    )
    return md


def _md_deferral() -> MetaData:
    """Case (c): orders.customer_id target absent + info marker -> declared deferral."""
    md = MetaData()
    Table(
        "orders",
        md,
        Column("order_id", Integer, primary_key=True),
        Column(
            "customer_id",
            Integer,
            info={"deferred_fk": "business.customers.customer_id", "blocked_on": "SFP-999"},
        ),
    )
    return md


def _md_cross_service() -> MetaData:
    """Case (d): orders.customer_id target absent, NO marker -> cross-service."""
    md = MetaData()
    Table(
        "orders",
        md,
        Column("order_id", Integer, primary_key=True),
        Column("customer_id", Integer),  # plain, no FK, no marker, target absent
    )
    return md


def _md_tablelevel_fk() -> MetaData:
    """The UserExternalIdentity precedent style: table-level ForeignKeyConstraint."""
    md = MetaData()
    Table(
        "users",
        md,
        Column("user_id", Integer, primary_key=True),
        schema="business",
    )
    Table(
        "user_external_identities",
        md,
        Column("external_identity_id", Integer, primary_key=True),
        Column("user_id", Integer),  # FK declared at table level below
        ForeignKeyConstraint(
            ["user_id"],
            ["business.users.user_id"],
            name="fk_user_external_identities_user_id",
        ),
        schema="business",
    )
    return md


# ============================================================
# (a) same-service _id WITH a FK -> pass
# ============================================================


def test_case_a_same_service_id_with_fk_passes():
    md = _md_with_fk()
    assert fk_lint.find_violations(md) == []
    assert fk_lint.find_deferred(md) == []


def test_tablelevel_fk_style_also_passes():
    # Tolerant of ForeignKeyConstraint (the identity precedent) — column.foreign_keys
    # is populated for the table-level constraint too.
    md = _md_tablelevel_fk()
    assert fk_lint.find_violations(md) == []


# ============================================================
# (b) plain _id targeting a same-metadata table -> violation
# ============================================================


def test_case_b_plain_same_service_reference_is_flagged():
    md = _md_plain_same_service()
    violations = fk_lint.find_violations(md)
    assert len(violations) == 1, violations
    v = violations[0]
    assert "widget_id" in v
    assert "widgets" in v
    assert "FOREIGN KEY" in v
    # The PK column widgets.widget_id is NOT flagged (only the plain parts.widget_id).
    assert "parts.widget_id" in v


def test_pk_columns_are_never_flagged():
    # A lone table whose only _id column is its own PK -> clean.
    md = MetaData()
    Table("widgets", md, Column("widget_id", Integer, primary_key=True))
    assert fk_lint.find_violations(md) == []


# ============================================================
# (c) plain _id with a valid info deferral marker (target absent) -> pass + declared
# ============================================================


def test_case_c_deferral_marker_passes_and_is_declared():
    md = _md_deferral()
    assert fk_lint.find_violations(md) == []
    deferred = fk_lint.find_deferred(md)
    assert len(deferred) == 1, deferred
    d = deferred[0]
    assert "customer_id" in d
    assert "business.customers.customer_id" in d
    assert "SFP-999" in d


# ============================================================
# (d) plain _id target absent + NO marker -> pass (cross-service), NOT declared
# ============================================================


def test_case_d_cross_service_passes_and_not_declared():
    md = _md_cross_service()
    assert fk_lint.find_violations(md) == []
    # The distinction from (c): no marker -> no declared deferral.
    assert fk_lint.find_deferred(md) == []


def test_cases_c_and_d_distinguished_only_by_marker():
    # Same column shape (customer_id, target absent); marker is the sole difference.
    assert fk_lint.find_violations(_md_deferral()) == []
    assert fk_lint.find_violations(_md_cross_service()) == []
    assert fk_lint.find_deferred(_md_deferral()) != []
    assert fk_lint.find_deferred(_md_cross_service()) == []


# ============================================================
# infer_target_table — direct unit tests
# ============================================================


def test_infer_target_table_present():
    names = {"widgets", "parts"}
    assert fk_lint.infer_target_table("widget_id", names) == "widgets"
    assert fk_lint.infer_target_table("part_id", names) == "parts"


def test_infer_target_table_absent_returns_none():
    assert fk_lint.infer_target_table("customer_id", {"widgets"}) is None


def test_infer_target_table_compound_stem_does_not_match_subsegment():
    # provider_user_id -> provider_users, NOT users. Guards the false positive
    # where a compound name's trailing word happens to match a real table.
    assert fk_lint.infer_target_table("provider_user_id", {"users"}) is None


def test_infer_target_table_y_pluralization():
    # entity_id -> entities (consonant + y).
    assert fk_lint.infer_target_table("entity_id", {"entities"}) == "entities"


def test_infer_target_table_non_id_returns_none():
    assert fk_lint.infer_target_table("created_at", {"widgets"}) is None


# ============================================================
# (e) backfill: real Ticket.project_id passes + is a declared deferral
# ============================================================


def _orchestrator_md():
    importlib = pytest.importorskip("importlib")
    mod = importlib.import_module("orchestrator.infrastructure.persistence")
    return mod.Base.metadata


def test_case_e_ticket_project_id_passes_and_is_declared():
    md = _orchestrator_md()
    # The whole orchestrator service is clean (no same-service FK violations).
    assert fk_lint.find_violations(md) == []
    deferred = fk_lint.find_deferred(md)
    # Ticket.project_id is a declared deferral on the real model.
    assert any("project_id" in d for d in deferred), deferred
    assert any("business.projects.project_id" in d for d in deferred), deferred
    assert any("SFP-100" in d for d in deferred), deferred


def test_case_e_ticket_project_id_marker_present_on_column():
    # The marker is physically on the ORM column (survives into the committed model).
    md = _orchestrator_md()
    tickets = md.tables.get("business.tickets")
    assert tickets is not None
    info = tickets.columns["project_id"].info
    assert info.get("deferred_fk") == "business.projects.project_id"
    assert info.get("blocked_on") == "SFP-100"


def test_case_e_check_service_orchestrator_clean():
    violations, deferred = fk_lint.check_service("orchestrator")
    assert violations == []
    assert any("project_id" in d for d in deferred), deferred


# ============================================================
# CLI surface (in-process main())
# ============================================================


def test_cli_list_exits_zero_and_names_services(capsys):
    assert fk_lint.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "orchestrator" in out
    assert "identity" in out


def test_cli_no_args_exits_nonzero(capsys):
    rc = fk_lint.main([])
    assert rc == 1
    err = capsys.readouterr().err
    assert "service" in err.lower()


def test_cli_unknown_service_exits_nonzero(capsys):
    rc = fk_lint.main(["nope-service"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown" in err.lower()


def test_cli_alias_underscore_accepted():
    # external_events (underscore) resolves to external-events; just verify it loads.
    violations, _deferred = fk_lint.check_service("external_events")
    assert violations == []


def test_cli_all_runs_clean_for_every_service(capsys):
    # The committed tree has zero same-service FK violations across all services
    # (the single deferral, Ticket.project_id, is declared).
    rc = fk_lint.main(["--all"])
    assert rc == 0, capsys.readouterr().out
