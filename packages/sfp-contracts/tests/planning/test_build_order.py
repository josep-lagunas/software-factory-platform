"""Tests for the build-order DAG logic in ``sfp_contracts.planning.build_order``.

These exercise the four pure functions (:func:`build_index`, :func:`check_dangling`,
:func:`compute_waves`, :func:`compute_ready`) directly via the module, plus the
:class:`BuildOrderCycleError` contract. They were promoted from
``tests/test_build_order.py`` (the TC-003/007/008/014 logic cases) as promo 2 of
3 of the ``tools/`` -> ``sfp_contracts`` effort (PR #122 was promo 1).

The REAL ``create_jira_tickets.parse_hierarchy`` is used as the oracle to parse
synthetic deterministic markdown fixtures (never reimplemented); the module
functions then operate on the parsed ticket dicts. No network, no env, no file
I/O through the module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# --- import the parser directly from tools/ (it stays a tool) ---------------
ROOT = Path(__file__).resolve().parents[4]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import create_jira_tickets as cjt  # noqa: E402
from sfp_contracts.planning.build_order import (  # noqa: E402
    BuildOrderCycleError,
    build_index,
    check_dangling,
    compute_ready,
    compute_waves,
)

BOT = "\U0001f916"  # 🤖

# ============================================================
# FIXTURES (deterministic synthetic markdown — same as the CLI suite)
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

SINGLE = f"""# MANUAL CORE
## TEST Epic — single fixture
### SFP-1 [AREA] {BOT} — only ticket
**Labels:** manual-core, ai-agent, area | **Deps:** — | **Context out:** x
"""


# ============================================================
# HELPERS
# ============================================================


def write_fixture(tmp_path: Path, content: str, name: str = "hierarchy.md") -> str:
    """Write markdown content to a tmp file; return its path as a str."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def parse(content: str, tmp_path: Path) -> tuple[list, dict[int, object]]:
    """Parse a markdown blob via the oracle and build the index."""
    doc = write_fixture(tmp_path, content)
    _, tickets = cjt.parse_hierarchy(doc)
    return tickets, build_index(tickets)


# ============================================================
# build_index
# ============================================================


def test_build_index_keys_are_nums(tmp_path):
    tickets, by_num = parse(PRIMARY, tmp_path)
    assert set(by_num.keys()) == {1, 2, 3, 4, 5, 6, 7, 8}
    # each value is the parsed ticket, keyed by its own num
    for t in tickets:
        assert by_num[t["num"]] is t


def test_build_index_empty():
    assert build_index([]) == {}


# ============================================================
# check_dangling
# ============================================================


def test_check_dangling_returns_none_when_all_resolve(tmp_path):
    _, by_num = parse(PRIMARY, tmp_path)
    tickets = [by_num[n] for n in sorted(by_num)]
    assert check_dangling(tickets, by_num) is None


def test_check_dangling_returns_offender_and_missing(tmp_path):
    tickets, by_num = parse(DANGLING, tmp_path)
    result = check_dangling(tickets, by_num)
    assert result is not None
    offender, missing = result
    assert offender == "SFP-2"
    assert missing == "SFP-9999"


def test_check_dangling_empty():
    assert check_dangling([], {}) is None


# ============================================================
# compute_waves — longest-path invariant + expected map (was TC-003)
# ============================================================


def test_compute_waves_is_longest_path(tmp_path):
    tickets, by_num = parse(PRIMARY, tmp_path)
    waves = compute_waves(tickets, by_num)
    # universal invariant: for every ticket with deps, wave(t) > max(wave(dep))
    for t in tickets:
        if t["deps"]:
            dep_nums = [int(d.split("-")[1]) for d in t["deps"]]
            assert waves[t["num"]] > max(waves[dn] for dn in dep_nums), t["id"]
    # expected waves {1:0, 2:0, 3:1, 4:1, 5:2, 6:3, 7:1, 8:1}
    assert waves == {1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 3, 7: 1, 8: 1}
    assert waves[6] == 3


def test_compute_waves_single_root_is_wave_zero(tmp_path):
    tickets, by_num = parse(SINGLE, tmp_path)
    assert compute_waves(tickets, by_num) == {1: 0}


def test_compute_waves_empty():
    assert compute_waves([], {}) == {}


# ============================================================
# compute_waves — RAISES BuildOrderCycleError (replaces TC-014 at library level)
# ============================================================


def test_compute_waves_raises_on_mutual_cycle(tmp_path):
    tickets, by_num = parse(CYCLE, tmp_path)
    with pytest.raises(BuildOrderCycleError) as exc:
        compute_waves(tickets, by_num)
    chain = str(exc.value)
    # the exception carries the named cycle members
    assert "SFP-2" in chain
    assert "SFP-3" in chain
    # and is a ValueError (catchable as a plain value error)
    assert isinstance(exc.value, ValueError)


def test_compute_waves_raises_on_self_cycle(tmp_path):
    tickets, by_num = parse(SELF_CYCLE, tmp_path)
    with pytest.raises(BuildOrderCycleError) as exc:
        compute_waves(tickets, by_num)
    assert "SFP-1" in str(exc.value)


def test_compute_waves_cycle_chain_format(tmp_path):
    """The chain the exception carries must match the CLI's historical format:
    ``"SFP-2 -> SFP-3 -> SFP-2"`` (cycle nodes joined, then back to the start)."""
    tickets, by_num = parse(CYCLE, tmp_path)
    with pytest.raises(BuildOrderCycleError) as exc:
        compute_waves(tickets, by_num)
    # The mutual cycle 2->3->2 yields chain "SFP-2 -> SFP-3 -> SFP-2" (or the
    # rotation starting at 3); assert the structural shape.
    chain = str(exc.value)
    assert chain.count(" -> ") >= 2
    assert chain.startswith("SFP-")
    assert chain.split(" -> ")[0] == chain.split(" -> ")[-1]


# ============================================================
# compute_ready (was TC-007, TC-008)
# ============================================================


def test_compute_ready_single_done(tmp_path):
    tickets, _ = parse(PRIMARY, tmp_path)
    ready = compute_ready(tickets, {"SFP-1"})
    # ready INCLUDED
    assert ready == ["SFP-2", "SFP-3"]
    # done EXCLUDED
    assert "SFP-1" not in ready
    # non-ready EXCLUDED
    assert "SFP-4" not in ready
    assert "SFP-6" not in ready


def test_compute_ready_multi_done(tmp_path):
    tickets, _ = parse(PRIMARY, tmp_path)
    ready = compute_ready(tickets, {"SFP-1", "SFP-2"})
    # ready INCLUDED
    assert ready == ["SFP-3", "SFP-4", "SFP-7", "SFP-8"]
    # done EXCLUDED
    assert "SFP-1" not in ready
    assert "SFP-2" not in ready
    # non-ready EXCLUDED
    assert "SFP-5" not in ready
    assert "SFP-6" not in ready


def test_compute_ready_empty_done_set_returns_roots(tmp_path):
    tickets, _ = parse(PRIMARY, tmp_path)
    ready = compute_ready(tickets, set())
    assert ready == ["SFP-1", "SFP-2"]


def test_compute_ready_lowest_num_first(tmp_path):
    tickets, _ = parse(PRIMARY, tmp_path)
    ready = compute_ready(tickets, {"SFP-1", "SFP-2"})
    nums = [int(tid.split("-")[1]) for tid in ready]
    assert nums == sorted(nums)


def test_compute_ready_empty_input():
    assert compute_ready([], set()) == []
