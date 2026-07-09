"""Tests for tools/build_order.py — the SFP-195 deterministic build-order generator.

Covers TC-001..016. The REAL cjt.parse_hierarchy is used as the oracle (never
reimplemented). All file I/O is redirected to tmp_path; the real docs/ tree is
never mutated. Synthetic fixtures are deterministic (no network, no env).
"""

import json
import sys
from pathlib import Path

import pytest

# --- import build_order + its dependency directly from tools/ ---------------
ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import build_order  # noqa: E402
import create_jira_tickets as cjt  # noqa: E402

REAL_DOC = ROOT / "docs" / "SFP_Ticket_Hierarchy.md"
BOT = "\U0001f916"  # 🤖

# ============================================================
# FIXTURES (deterministic synthetic markdown)
# ============================================================

PRIMARY = f"""# MANUAL CORE
## TEST Epic — synthetic fixture
### SFP-1 [AREA] {BOT} — root A
**Labels:** manual-core, ai-agent, area | **Deps:** — | **Context out:** x
### SFP-2 [AREA] {BOT} — root B
**Labels:** manual-core, ai-agent, area | **Deps:** — | **Context out:** x
### SFP-3 [AREA] {BOT} — single dep on SFP-1
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-1 | **Context out:** x
### SFP-4 [AREA] {BOT} — multi dep on SFP-1, SFP-2 (diamond)
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-1, SFP-2 | **Context out:** x
### SFP-5 [AREA] {BOT} — 2-deep chain on SFP-3
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-3 | **Context out:** x
### SFP-6 [AREA] {BOT} — 3-deep chain on SFP-5
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-5 | **Context out:** x
### SFP-7 [AREA] {BOT} — shares deps {{1,2}} with SFP-4 (same wave, ascending)
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-1, SFP-2 | **Context out:** x
### SFP-8 [AREA] {BOT} — B→A marker stripped, dep on SFP-2
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-2 *(B→A)* | **Context out:** x
"""

CYCLE = f"""# MANUAL CORE
## TEST Epic — cycle fixture
### SFP-2 [AREA] {BOT} — cycle a
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-3 | **Context out:** x
### SFP-3 [AREA] {BOT} — cycle b
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-2 | **Context out:** x
"""

SELF_CYCLE = f"""# MANUAL CORE
## TEST Epic — self-cycle fixture
### SFP-1 [AREA] {BOT} — self dep
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-1 | **Context out:** x
"""

DANGLING = f"""# MANUAL CORE
## TEST Epic — dangling fixture
### SFP-2 [AREA] {BOT} — dangling dep
**Labels:** manual-core, ai-agent, area | **Deps:** SFP-9999 | **Context out:** x
"""

EMPTY = """# MANUAL CORE
## TEST Epic — empty fixture
"""

SINGLE = f"""# MANUAL CORE
## TEST Epic — single fixture
### SFP-1 [AREA] {BOT} — only ticket
**Labels:** manual-core, ai-agent, area | **Deps:** — | **Context out:** x
"""


# ============================================================
# HELPERS
# ============================================================


def write_fixture(tmp_path, content, name="hierarchy.md"):
    """Write markdown content to a tmp file; return its path."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def transitive_deps(num, by_num):
    """Return the set of 'SFP-N' id strings for all transitive deps of `num`."""
    seen = set()
    stack = list(by_num[num]["deps"])
    while stack:
        d = stack.pop()
        if d in seen:
            continue
        seen.add(d)
        dnum = int(d.split("-")[1])
        if dnum in by_num:
            stack.extend(by_num[dnum]["deps"])
    return seen


def run_main(tmp_path, doc_path, extra=None):
    """Invoke build_order.main with outputs redirected to tmp_path."""
    out_md = tmp_path / "BUILD_ORDER.md"
    out_json = tmp_path / "build_order.json"
    argv = ["--doc", str(doc_path), "--out-md", str(out_md), "--out-json", str(out_json)]
    if extra:
        argv.extend(extra)
    rc = build_order.main(argv)
    return rc, out_md, out_json


# ============================================================
# TC-001 — parse: id-set == cjt.parse_hierarchy oracle, count == 8
# ============================================================


def test_tc001_parse_matches_oracle(tmp_path):
    doc = write_fixture(tmp_path, PRIMARY)
    rc, _, out_json = run_main(tmp_path, doc)
    assert rc == 0
    _, oracle = cjt.parse_hierarchy(str(doc))
    data = json.loads(out_json.read_text())
    assert {t["ticket"] for t in data["tickets"]} == {t["id"] for t in oracle}
    assert len(data["tickets"]) == 8


# ============================================================
# TC-002 — integration smoke on the real hierarchy doc
# ============================================================


def test_tc002_integration_smoke_real_doc(tmp_path):
    rc, _, out_json = run_main(tmp_path, REAL_DOC)
    assert rc == 0
    _, oracle = cjt.parse_hierarchy(str(REAL_DOC))
    data = json.loads(out_json.read_text())
    # count == parse_hierarchy count == 171 (NOT the doc's stated 170)
    assert len(data["tickets"]) == len(oracle) == 171
    assert len(data["flat_order"]) == 171


# ============================================================
# TC-003 — wave == longest-path (universal invariant; SFP-6 == 3)
# ============================================================


def test_tc003_wave_is_longest_path(tmp_path):
    doc = write_fixture(tmp_path, PRIMARY)
    _, tickets = cjt.parse_hierarchy(str(doc))
    by_num = build_order.build_index(tickets)
    waves = build_order.compute_waves(tickets, by_num)
    # universal: for every ticket with deps, wave(t) > max(wave(dep))
    for t in tickets:
        if t["deps"]:
            dep_nums = [int(d.split("-")[1]) for d in t["deps"]]
            assert waves[t["num"]] > max(waves[dn] for dn in dep_nums), t["id"]
    # expected waves {1:0, 2:0, 3:1, 4:1, 5:2, 6:3, 7:1, 8:1}
    assert waves == {1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 3, 7: 1, 8: 1}
    assert waves[6] == 3


# ============================================================
# TC-004 — within-wave ascending (wave-1 == [3,4,7,8])
# ============================================================


def test_tc004_within_wave_ascending(tmp_path):
    doc = write_fixture(tmp_path, PRIMARY)
    _, tickets = cjt.parse_hierarchy(str(doc))
    by_num = build_order.build_index(tickets)
    waves = build_order.compute_waves(tickets, by_num)
    groups = build_order._group_by_wave(tickets, waves)
    assert [t["num"] for t in groups[0]] == [1, 2]
    assert [t["num"] for t in groups[1]] == [3, 4, 7, 8]
    assert [t["num"] for t in groups[2]] == [5]
    assert [t["num"] for t in groups[3]] == [6]


# ============================================================
# TC-005 — cycle detection names the cycle members
# ============================================================


def test_tc005_cycle_names_members(tmp_path):
    doc = write_fixture(tmp_path, CYCLE)
    with pytest.raises(SystemExit) as exc:
        run_main(tmp_path, doc)
    msg = str(exc.value)
    assert "SFP-2" in msg
    assert "SFP-3" in msg


# ============================================================
# TC-006 — dangling dep names the offender + missing dep
# ============================================================


def test_tc006_dangling_names_offender(tmp_path):
    doc = write_fixture(tmp_path, DANGLING)
    with pytest.raises(SystemExit) as exc:
        run_main(tmp_path, doc)
    msg = str(exc.value)
    assert "SFP-9999" in msg  # the missing dep
    assert "SFP-2" in msg  # the offender ticket


# ============================================================
# TC-007 — --done SFP-1 → ready [2,3] (both directions)
# ============================================================


def test_tc007_done_ready_set_single(tmp_path):
    doc = write_fixture(tmp_path, PRIMARY)
    _, tickets = cjt.parse_hierarchy(str(doc))
    ready = build_order.compute_ready(tickets, {"SFP-1"})
    # ready INCLUDED
    assert ready == ["SFP-2", "SFP-3"]
    # done EXCLUDED
    assert "SFP-1" not in ready
    # non-ready EXCLUDED
    assert "SFP-4" not in ready
    assert "SFP-6" not in ready


# ============================================================
# TC-008 — --done SFP-1,SFP-2 → ready [3,4,7,8] (both directions)
# ============================================================


def test_tc008_done_ready_set_multi(tmp_path):
    doc = write_fixture(tmp_path, PRIMARY)
    _, tickets = cjt.parse_hierarchy(str(doc))
    ready = build_order.compute_ready(tickets, {"SFP-1", "SFP-2"})
    # ready INCLUDED
    assert ready == ["SFP-3", "SFP-4", "SFP-7", "SFP-8"]
    # done EXCLUDED
    assert "SFP-1" not in ready
    assert "SFP-2" not in ready
    # non-ready EXCLUDED
    assert "SFP-5" not in ready
    assert "SFP-6" not in ready


# ============================================================
# TC-009 — emit md+json; flat_order ordered; 3-deep index > transitive deps
# ============================================================


def test_tc009_emit_and_ordering(tmp_path):
    doc = write_fixture(tmp_path, PRIMARY)
    rc, out_md, out_json = run_main(tmp_path, doc)
    assert rc == 0
    assert out_md.exists() and out_json.exists()
    data = json.loads(out_json.read_text())
    # flat_order ordered by (wave asc, num asc)
    expected_flat = ["SFP-1", "SFP-2", "SFP-3", "SFP-4", "SFP-7", "SFP-8", "SFP-5", "SFP-6"]
    assert data["flat_order"] == expected_flat
    # 3-deep ticket (SFP-6) index > index of all its transitive deps
    _, tickets = cjt.parse_hierarchy(str(doc))
    by_num = build_order.build_index(tickets)
    trans = transitive_deps(6, by_num)
    idx6 = data["flat_order"].index("SFP-6")
    for dep_id in trans:
        assert idx6 > data["flat_order"].index(dep_id), dep_id


# ============================================================
# TC-010 — json schema completeness + md/json count parity
# ============================================================


def test_tc010_json_schema_and_parity(tmp_path):
    doc = write_fixture(tmp_path, PRIMARY)
    rc, out_md, out_json = run_main(tmp_path, doc)
    assert rc == 0
    data = json.loads(out_json.read_text())
    expected_keys = {"ticket", "wave", "deps", "area", "executor", "phase", "title"}
    for t in data["tickets"]:
        assert set(t.keys()) == expected_keys
    # md/json count parity: one table row per ticket
    md_rows = [row for row in out_md.read_text().splitlines() if row.startswith("| SFP-")]
    assert len(md_rows) == len(data["tickets"]) == 8


# ============================================================
# TC-011 — determinism (byte-identical re-emit, no timestamps)
# ============================================================


def test_tc011_determinism(tmp_path):
    doc = write_fixture(tmp_path, PRIMARY)
    # emit twice into separate paths (no name collision)
    md_a, j_a = tmp_path / "a.md", tmp_path / "a.json"
    md_b, j_b = tmp_path / "b.md", tmp_path / "b.json"
    build_order.main(["--doc", str(doc), "--out-md", str(md_a), "--out-json", str(j_a)])
    build_order.main(["--doc", str(doc), "--out-md", str(md_b), "--out-json", str(j_b)])
    assert md_a.read_text() == md_b.read_text()
    assert j_a.read_text() == j_b.read_text()
    # no timestamps: source must not import datetime/time or emit now()
    src = (ROOT / "tools" / "build_order.py").read_text()
    assert "import datetime" not in src
    assert "import time" not in src
    assert "datetime.now" not in src
    assert "time.time" not in src


# ============================================================
# TC-012 — empty hierarchy → flat_order []
# ============================================================


def test_tc012_empty(tmp_path):
    doc = write_fixture(tmp_path, EMPTY)
    rc, out_md, out_json = run_main(tmp_path, doc)
    assert rc == 0
    data = json.loads(out_json.read_text())
    assert data["flat_order"] == []
    assert data["tickets"] == []
    # md still has a header but no wave sections
    assert "## Wave" not in out_md.read_text()


# ============================================================
# TC-013 — single root ticket
# ============================================================


def test_tc013_single(tmp_path):
    doc = write_fixture(tmp_path, SINGLE)
    rc, _, out_json = run_main(tmp_path, doc)
    assert rc == 0
    data = json.loads(out_json.read_text())
    assert data["flat_order"] == ["SFP-1"]
    assert len(data["tickets"]) == 1
    assert data["tickets"][0]["wave"] == 0


# ============================================================
# TC-014 — self-cycle (SFP-1 → SFP-1)
# ============================================================


def test_tc014_self_cycle(tmp_path):
    doc = write_fixture(tmp_path, SELF_CYCLE)
    with pytest.raises(SystemExit) as exc:
        run_main(tmp_path, doc)
    assert "SFP-1" in str(exc.value)


# ============================================================
# TC-015 — --done CLI stdout (ready set printed, no docs emitted)
# ============================================================


def test_tc015_done_cli_stdout(tmp_path, capsys):
    doc = write_fixture(tmp_path, PRIMARY)
    rc = build_order.main(["--doc", str(doc), "--done", "SFP-1,SFP-2"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["SFP-3", "SFP-4", "SFP-7", "SFP-8"]
    # --done mode must NOT emit docs at the default paths
    assert not (tmp_path / "BUILD_ORDER.md").exists()


# ============================================================
# TC-016 — coverage gate (missing-doc branch + API surface)
# ============================================================


def test_tc016_missing_doc_exits(tmp_path):
    """Covers the file-not-found branch. The >=90% coverage gate itself is
    enforced by `coverage report --fail-under=90` in the verification command."""
    with pytest.raises(SystemExit) as exc:
        build_order.main(["--doc", str(tmp_path / "nonexistent.md")])
    assert "not found" in str(exc.value)


def test_tc016_public_api_surface():
    """Structural smoke — all documented functions are present and callable."""
    for name in (
        "build_index",
        "check_dangling",
        "compute_waves",
        "compute_ready",
        "emit_md",
        "emit_json",
        "main",
    ):
        assert callable(getattr(build_order, name)), name
