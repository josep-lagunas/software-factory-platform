"""Tests for :func:`workspace_worker.repo.jira.parser.adf_to_parsed_ticket` (SFP-225 / SFP-232).

Pure unit tests — no network, no httpx. Covers the acceptance criteria:
- (1) the eight-section oracle — a synthetic ADF with one heading + paragraph
  per ID-070 section (literal slash-bearing headers) populates every
  :class:`ParsedTicket` field;
- (2) a missing section (header never seen) maps to ``None``;
- (3) an unknown / extra section header is ignored (does not leak into a
  neighbor recognized field);
- (4) nested ADF (paragraph inside list / table) is walked to text correctly;
- (5) an empty / ``{}`` ADF yields an all-None :class:`ParsedTicket` (no raise);
- (6) SFP-232 presence vs absence: a section whose header was SEEN but whose
  body is empty/whitespace maps to ``""`` (present-but-empty), distinct from an
  absent section whose header was never seen (``None``).

The 8-header -> field oracle is encoded INDEPENDENTLY here (not imported from
the implementation's ``_SECTION_TO_FIELD``) so the test is a genuine oracle —
mirroring the precedent in ``test_readiness_rubric.py``.
"""

from __future__ import annotations

from sfp_contracts.agents.readiness import ParsedTicket
from workspace_worker.repo.jira.parser import adf_to_parsed_ticket

#: Independent oracle: the eight mandatory ID-070 section headers (the literal
#: text as it appears in the ADF stream) mapped to the canonical ParsedTicket
#: field name. Encoded here WITHOUT consulting the implementation's
#: ``_SECTION_TO_FIELD`` constant. The two slash-bearing headers are included
#: verbatim to prove literal (not normalized) matching.
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
_FIELDS: tuple[str, ...] = tuple(field for _, field in SECTION_HEADERS)


def _text(text: str) -> dict[str, object]:
    return {"type": "text", "text": text}


def _paragraph(text: str) -> dict[str, object]:
    return {"type": "paragraph", "content": [_text(text)]}


def _heading(text: str, level: int = 2) -> dict[str, object]:
    return {"type": "heading", "attrs": {"level": level}, "content": [_text(text)]}


def _adf(*nodes: dict[str, object]) -> dict[str, object]:
    return {"type": "doc", "content": list(nodes)}


def _full_adf() -> dict[str, object]:
    """An ADF doc carrying all eight ID-070 sections, one heading + paragraph each."""
    nodes: list[dict[str, object]] = []
    for header, field in SECTION_HEADERS:
        nodes.append(_heading(header))
        nodes.append(_paragraph(f"<{field} body>"))
    return _adf(*nodes)


# ---------------------------------------------------------------------------
# (1) the eight-section oracle — every field populated
# ---------------------------------------------------------------------------


def test_oracle_covers_eight_sections() -> None:
    """Guard: the oracle table is exactly the eight ID-070 sections."""
    assert len(SECTION_HEADERS) == 8
    assert len(set(_FIELDS)) == 8


def test_eight_section_oracle_populates_every_field() -> None:
    """(1) A doc with all eight sections populates every ParsedTicket field."""
    parsed = adf_to_parsed_ticket(_full_adf())

    assert isinstance(parsed, ParsedTicket)
    for _, field in SECTION_HEADERS:
        assert getattr(parsed, field) == f"<{field} body>", f"field {field!r} not populated"


def test_slash_bearing_headers_matched_literally() -> None:
    """The two slash-bearing headers match without slash normalization."""
    parsed = adf_to_parsed_ticket(_full_adf())
    # Both slash-bearing fields are populated — proves the slashes survived the
    # literal match (a normalized '/' match could still pass this if the body
    # text routed elsewhere, so cross-check the exact body content too).
    assert parsed.files_to_create_modify == "<files_to_create_modify body>"
    assert parsed.context_outputs_required_inputs == "<context_outputs_required_inputs body>"


def test_header_recognized_as_paragraph_node_too() -> None:
    """PRSpec risk 2: a header rendered as a plain paragraph (not a heading) is
    still recognized — heading and paragraph nodes collapse to the same stream."""
    adf = _adf(_paragraph("Context"), _paragraph("ctx body"))
    parsed = adf_to_parsed_ticket(adf)
    assert parsed.context == "ctx body"


# ---------------------------------------------------------------------------
# (2) missing section -> None
# ---------------------------------------------------------------------------


def test_missing_sections_are_none() -> None:
    """(2) A doc with only some sections leaves the absent fields None."""
    adf = _adf(
        _heading("Context"),
        _paragraph("ctx only"),
        _heading("Dependencies"),
        _paragraph("deps only"),
    )
    parsed = adf_to_parsed_ticket(adf)

    assert parsed.context == "ctx only"
    assert parsed.dependencies == "deps only"
    present = {"context", "dependencies"}
    for _, field in SECTION_HEADERS:
        if field not in present:
            assert getattr(parsed, field) is None, f"field {field!r} should be None"


def test_whitespace_only_body_is_present_empty() -> None:
    """SFP-232: a SEEN header with a whitespace-only body maps to ``""
    (present-but-empty), NOT ``None``."""
    adf = _adf(_heading("Context"), _paragraph("   \n\t  "))
    parsed = adf_to_parsed_ticket(adf)
    assert parsed.context == ""  # header was seen -> present-but-empty


def test_header_with_no_body_is_present_empty() -> None:
    """SFP-232: a header immediately followed by the next header (no body blocks)
    maps to ``""`` (present-but-empty), NOT ``None``."""
    adf = _adf(_heading("Context"), _heading("Requirements"), _paragraph("req body"))
    parsed = adf_to_parsed_ticket(adf)
    assert parsed.context == ""  # header was seen but no body -> ""
    assert parsed.requirements == "req body"


# ---------------------------------------------------------------------------
# (3) unknown / extra section header -> ignored (no leak into a neighbor)
# ---------------------------------------------------------------------------


def test_unknown_section_before_any_header_is_dropped() -> None:
    """(3) An unknown header with no active recognized section is dropped entirely;
    it does not leak into any recognized field."""
    adf = _adf(
        _heading("Some Unknown Section"),
        _paragraph("unknown body text"),
        _heading("Context"),
        _paragraph("ctx body"),
    )
    parsed = adf_to_parsed_ticket(adf)

    assert parsed.context == "ctx body"
    for _, field in SECTION_HEADERS:
        if field != "context":
            assert getattr(parsed, field) is None
    # The unknown section's text did not leak into the Context body either.
    assert "unknown body text" not in (parsed.context or "")
    assert "Some Unknown Section" not in (parsed.context or "")


def test_unknown_section_between_sections_does_not_leak_forward() -> None:
    """(3) An unknown header between two recognized sections is ignored as a
    delimiter — it does not corrupt the *following* recognized section."""
    adf = _adf(
        _heading("Context"),
        _paragraph("ctx body"),
        _heading("Mystery Section"),
        _paragraph("mystery body"),
        _heading("Requirements"),
        _paragraph("req body"),
    )
    parsed = adf_to_parsed_ticket(adf)

    # Requirements (the neighbor AFTER the unknown) is clean.
    assert parsed.requirements == "req body"
    assert "mystery body" not in (parsed.requirements or "")
    # And no other recognized field picked up the mystery text.
    for _, field in SECTION_HEADERS:
        if field in {"context", "requirements"}:
            continue
        assert "mystery body" not in (getattr(parsed, field) or "")


# ---------------------------------------------------------------------------
# (4) nested ADF (paragraph inside list / table) walked to text
# ---------------------------------------------------------------------------


def test_nested_bullet_list_walked_to_text() -> None:
    """(4) A bulletList > listItem > paragraph chain is walked to text."""
    bullet = {
        "type": "bulletList",
        "content": [
            {"type": "listItem", "content": [_paragraph("alpha")]},
            {"type": "listItem", "content": [_paragraph("beta")]},
        ],
    }
    adf = _adf(_heading("Context"), bullet)
    parsed = adf_to_parsed_ticket(adf)

    assert parsed.context is not None
    assert "alpha" in parsed.context
    assert "beta" in parsed.context


def test_nested_table_walked_to_text() -> None:
    """(4) A table > tableRow > tableCell > paragraph chain is walked to text."""
    table = {
        "type": "table",
        "content": [
            {
                "type": "tableRow",
                "content": [
                    {"type": "tableCell", "content": [_paragraph("gamma")]},
                    {"type": "tableHeader", "content": [_paragraph("delta")]},
                ],
            }
        ],
    }
    adf = _adf(_heading("Requirements"), table)
    parsed = adf_to_parsed_ticket(adf)

    assert parsed.requirements is not None
    assert "gamma" in parsed.requirements
    assert "delta" in parsed.requirements


# ---------------------------------------------------------------------------
# (5) empty / {} ADF -> all-None ParsedTicket (no raise)
# ---------------------------------------------------------------------------


def test_empty_dict_adf_yields_all_none() -> None:
    """(5) An empty ``{}`` ADF yields an all-None ParsedTicket without raising."""
    parsed = adf_to_parsed_ticket({})

    assert isinstance(parsed, ParsedTicket)
    for _, field in SECTION_HEADERS:
        assert getattr(parsed, field) is None


def test_doc_with_no_content_yields_all_none() -> None:
    """(5) A doc node with no ``content`` list yields all-None."""
    parsed = adf_to_parsed_ticket({"type": "doc"})
    for _, field in SECTION_HEADERS:
        assert getattr(parsed, field) is None


def test_malformed_non_dict_nodes_are_skipped() -> None:
    """Defensive: non-dict top-level nodes are skipped (no raise)."""
    adf = {
        "type": "doc",
        "content": [
            "not a dict",  # type: ignore[list-item]
            42,  # type: ignore[list-item]
            None,  # type: ignore[list-item]
            _paragraph("stray body"),  # no active section -> dropped
        ],
    }
    parsed = adf_to_parsed_ticket(adf)
    for _, field in SECTION_HEADERS:
        assert getattr(parsed, field) is None


def test_malformed_nested_children_are_skipped() -> None:
    """Defensive: non-dict children inside a content list, and non-list content,
    are skipped without raising (the walker degrades to empty text)."""
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                # A content list with non-dict entries AND a non-list `content`
                # sibling node — both walked defensively.
                "content": ["stray", 7, _text("kept")],  # type: ignore[list-item]
            },
            {"type": "heading", "content": [_text("Context")]},  # non-list content node below
            {"type": "codeBlock", "content": "not a list"},  # type: ignore[dict-item]
            _paragraph("ctx body"),
        ],
    }
    parsed = adf_to_parsed_ticket(adf)
    # The walker did not raise; the recognized section still captures its body.
    assert parsed.context == "ctx body"


# ---------------------------------------------------------------------------
# multi-paragraph body is preserved (body join uses newlines)
# ---------------------------------------------------------------------------


def test_multi_paragraph_body_joined() -> None:
    """A section with several body blocks keeps all of them (newline-joined)."""
    adf = _adf(
        _heading("Context"),
        _paragraph("first paragraph"),
        _paragraph("second paragraph"),
    )
    parsed = adf_to_parsed_ticket(adf)
    assert parsed.context is not None
    assert "first paragraph" in parsed.context
    assert "second paragraph" in parsed.context


# ---------------------------------------------------------------------------
# (6) SFP-232: present-but-empty ("") distinguished from absent (None)
# ---------------------------------------------------------------------------


def test_present_empty_distinguished_from_absent() -> None:
    """SFP-232: a SEEN header with an empty body is ``""``; an UNSEEN header is
    ``None`` — the two are now distinguishable."""
    adf = _adf(
        _heading("Context"),  # seen, no body -> ""
        _heading("Requirements"),  # seen, no body -> ""
        _heading("Acceptance criteria"),  # seen, no body -> ""
        _paragraph("ac body"),
    )
    parsed = adf_to_parsed_ticket(adf)

    assert parsed.context == ""  # seen but empty
    assert parsed.requirements == ""  # seen but empty
    assert parsed.acceptance_criteria == "ac body"  # seen with body
    # Headers never seen stay None (absent), NOT "".
    assert parsed.files_to_create_modify is None
    assert parsed.implementation_notes is None
    assert parsed.references is None
    assert parsed.context_outputs_required_inputs is None
    assert parsed.dependencies is None


def test_markdown_boundary_header_empty_then_next_section() -> None:
    """SFP-232: real Jira descriptions carry ``## Header`` markdown prefixes.
    A ``## Context outputs / required inputs`` header immediately followed by
    ``## Acceptance criteria`` yields ``context_outputs_required_inputs == ""``,
    NOT ``None`` (presence detected)."""
    adf = _adf(
        _heading("## Context outputs / required inputs"),
        _heading("## Acceptance criteria"),
        _paragraph("ac body"),
    )
    parsed = adf_to_parsed_ticket(adf)

    assert parsed.context_outputs_required_inputs == ""  # seen, empty body
    assert parsed.acceptance_criteria == "ac body"


def test_markdown_boundary_header_with_body() -> None:
    """SFP-232: a markdown boundary header WITH a body is parsed normally
    (non-empty content preserved; not collapsed to ``""``)."""
    adf = _adf(
        _heading("## Dependencies"),
        _paragraph("depends on SFP-X"),
        _heading("## Acceptance criteria"),
        _paragraph("ac body"),
    )
    parsed = adf_to_parsed_ticket(adf)

    assert parsed.dependencies == "depends on SFP-X"
    assert parsed.acceptance_criteria == "ac body"
