"""Tests for the parser dry-run gate (SFP-243): ``description_dry_run`` +
``markdown_adf``.

The dry-run is the enrichment pipeline's sole publish authority — these tests
pin it independently of the enrichment controller (which is covered in
``test_enrichment.py``). Oracles are declared here WITHOUT importing them from
the implementation, mirroring ``test_readiness_gate.py`` / ``test_jira_parser.py``.

Load-bearing points proven here:
- the eight ID-070 headers as EXACT literal strings parse into the matching
  :class:`~sfp_contracts.agents.readiness.ParsedTicket` fields (decorated
  headings do NOT match — the dry-run rejects them);
- ``ok`` is ``True`` iff all eight sections are present AND non-empty — the
  off-frontier rubric's presence-only relaxation does NOT apply here;
- the section list is derived from the parser's own table (drift-proof);
- the Markdown -> ADF bridge reproduces the tools helper's conventions
  (headings drop their ``#`` prefix; ``**Text:**`` becomes a heading;
  consecutive bullets buffer into one ``bulletList``).
"""

from __future__ import annotations

import pytest
from sfp_contracts.agents.readiness import ParsedTicket
from workspace_worker.workflow.description_dry_run import (
    dry_run_adf,
    dry_run_markdown,
    empty_section_fields,
)
from workspace_worker.workflow.markdown_adf import markdown_to_adf

# Independent oracle: the eight mandatory ID-070 headers <-> ParsedTicket
# fields. Encoded here WITHOUT consulting the implementation (mirrors
# test_jira_parser.py's SECTION_HEADERS).
SECTION_HEADERS: tuple[tuple[str, str], ...] = (
    ("Context", "context"),
    ("Requirements", "requirements"),
    ("Files to create/modify", "files_to_create_modify"),
    ("Implementation notes", "implementation_notes"),
    ("References", "references"),
    ("Context outputs / required inputs", "context_outputs_required_inputs"),
    ("Acceptance criteria", "acceptance_criteria"),
    ("Dependencies", "dependencies"),
)
FIELDS: tuple[str, ...] = tuple(field for _, field in SECTION_HEADERS)


# --- fixtures --------------------------------------------------------------


def _full_markdown() -> str:
    """A Markdown description carrying all eight literal headers + bodies."""
    lines: list[str] = []
    for header, field in SECTION_HEADERS:
        lines.append(f"# {header}")
        lines.append(f"{field} body grounded on main.")
        lines.append("")
    return "\n".join(lines)


def _markdown_missing(field: str) -> str:
    """A Markdown description with one section's header + body omitted."""
    keep = [(h, f) for h, f in SECTION_HEADERS if f != field]
    lines: list[str] = []
    for header, f in keep:
        lines.append(f"# {header}")
        lines.append(f"{f} body grounded on main.")
        lines.append("")
    return "\n".join(lines)


def _markdown_empty(field: str) -> str:
    """A Markdown description with one section header present but body empty."""
    lines: list[str] = []
    for header, f in SECTION_HEADERS:
        lines.append(f"# {header}")
        if f == field:
            lines.append("")  # header present, body empty
        else:
            lines.append(f"{f} body grounded on main.")
        lines.append("")
    return "\n".join(lines)


# --- markdown_to_adf bridge (cited port of the tools helper) ---------------


def test_atx_heading_drops_hash_prefix() -> None:
    """``# Context`` -> heading block whose text is the literal ``Context``."""
    adf = markdown_to_adf("# Context\nbody")
    heading = adf["content"][0]
    assert heading["type"] == "heading"
    assert heading["content"][0] == {"type": "text", "text": "Context"}


def test_bold_heading_form_becomes_heading() -> None:
    """``**Context:**`` (the tools helper's form) -> heading ``Context``."""
    adf = markdown_to_adf("**Context:**\nbody")
    heading = adf["content"][0]
    assert heading["type"] == "heading"
    assert heading["content"][0] == {"type": "text", "text": "Context"}


def test_consecutive_bullets_buffer_into_one_list() -> None:
    """Consecutive ``- `` lines become ONE bulletList block (tools convention)."""
    adf = markdown_to_adf("- alpha\n- beta\n\nparagraph")
    blocks = adf["content"]
    assert blocks[0]["type"] == "bulletList"
    items = blocks[0]["content"]
    assert [i["content"][0]["content"][0]["text"] for i in items] == ["alpha", "beta"]
    assert blocks[1]["type"] == "paragraph"


def test_plain_line_becomes_paragraph_and_empty_input_ok() -> None:
    """A plain line -> paragraph; empty input -> empty doc (no raise)."""
    adf = markdown_to_adf("just a line")
    assert adf["content"][0]["type"] == "paragraph"
    empty = markdown_to_adf("   \n  ")
    assert empty == {"type": "doc", "version": 1, "content": []}


def test_determinism_same_input_same_output() -> None:
    """MAS §12.7: the same Markdown always converts to the same ADF."""
    assert markdown_to_adf(_full_markdown()) == markdown_to_adf(_full_markdown())


# --- dry-run: green path ----------------------------------------------------


def test_full_description_is_ok() -> None:
    """All eight literal headers + non-empty bodies -> ok=True, no failures."""
    result = dry_run_markdown(_full_markdown())
    assert result.ok is True
    assert result.failures == ()
    assert result.empty_sections == ()
    # The parsed ticket carries every section's body.
    for _, field in SECTION_HEADERS:
        value = getattr(result.parsed, field)
        assert isinstance(value, str) and value.strip()


def test_dry_run_adf_equivalent_to_markdown() -> None:
    """Both entry points share one verdict path (one publish authority)."""
    from workspace_worker.workflow.markdown_adf import markdown_to_adf as conv

    assert dry_run_adf(conv(_full_markdown())) == dry_run_markdown(_full_markdown())


def test_parsed_is_a_parsed_ticket() -> None:
    """The dry-run returns the parser's actual ParsedTicket (pinned contract)."""
    result = dry_run_markdown(_full_markdown())
    assert isinstance(result.parsed, ParsedTicket)


# --- dry-run: section failures ----------------------------------------------


@pytest.mark.parametrize("field", FIELDS)
def test_missing_section_fails(field: str) -> None:
    """An absent section header -> ok=False naming the section as missing."""
    result = dry_run_markdown(_markdown_missing(field))
    assert result.ok is False
    assert result.empty_sections == (field,)
    assert f"section missing: {field}" in result.failures


@pytest.mark.parametrize("field", FIELDS)
def test_empty_section_fails(field: str) -> None:
    """A present-but-empty section -> ok=False naming it as EMPTY (not missing).

    This pins the deliberate strictness over the SFP-232 off-frontier rubric:
    an enriched ticket is written BECAUSE the gate blocked the current one, so
    a present-but-empty section would relaunch into the same block.
    """
    result = dry_run_markdown(_markdown_empty(field))
    assert result.ok is False
    assert result.empty_sections == (field,)
    assert f"section empty: {field}" in result.failures


def test_empty_input_reports_all_sections_missing() -> None:
    """An empty description -> all eight sections missing, in parser order."""
    result = dry_run_markdown("")
    assert result.ok is False
    assert result.empty_sections == FIELDS
    assert len(result.failures) == 8


# --- dry-run: literal headers (decorated headings rejected) -----------------


@pytest.mark.parametrize(
    ("decoration", "expected_header"),
    [
        ("## 1. {h}", "1. {h}"),
        ("# {h} —", "{h} —"),
        ("# {h}:", "{h}:"),
        ("# **{h}**", "**{h}**"),
    ],
    ids=["numbered", "em-dash", "trailing-colon", "bold-wrapped"],
)
def test_decorated_headers_are_rejected(decoration: str, expected_header: str) -> None:
    """A decorated header does NOT match the literal section split -> failure.

    The parser's header match is literal, so ``## 1. Context`` starts no
    Context section; every downstream section collapses and the dry-run
    rejects the description. This is the acceptance criterion "All 8 section
    headers are the exact literal ID-070 strings (decorated headings rejected
    by dry-run)".
    """
    markdown = decoration.format(h="Context") + "\nbody\n"
    markdown += "\n".join(
        f"# {header}\n{field} body grounded on main.\n" for header, field in SECTION_HEADERS[1:]
    )
    result = dry_run_markdown(markdown)
    assert result.ok is False
    assert "context" in result.empty_sections


def test_slash_bearing_header_matched_literally() -> None:
    """``Context outputs / required inputs`` matches with its slash intact."""
    result = dry_run_markdown(_full_markdown())
    assert result.ok is True
    assert "context_outputs_required_inputs" not in result.empty_sections


# --- section list derived from the parser (drift-proof) ---------------------


def test_section_fields_derived_from_parser_table() -> None:
    """The dry-run's section set IS the parser's header table (no drift)."""
    from workspace_worker.repo.jira import parser as parser_mod

    assert empty_section_fields({"type": "doc", "content": []}) == tuple(
        parser_mod._SECTION_TO_FIELD.values()
    )
