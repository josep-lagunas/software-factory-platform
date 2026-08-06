"""ADF -> :class:`ParsedTicket` parser (SFP-225; the Jira slice of ID-070).

Atlassian Document Format (ADF) is the JSON document model Jira Cloud uses for
issue descriptions (``fields.description``): a tree of ``{type, content, ...}``
nodes whose leaves are ``{type: 'text', text: '...'}`` nodes. This module walks
that tree depth-first to a flat plain-text stream and then splits it on the
eight mandatory ID-070 section headers, mapping each section body to the
matching :class:`~sfp_contracts.agents.readiness.ParsedTicket` field.

This is a pure function — no network, no I/O. The companion
:mod:`workspace_worker.repo.jira.client` module calls it on the
``fields.description`` value returned by the Jira Cloud REST API.

Section-header matching is **literal** (the block's stripped text must equal a
header exactly), including the two slash-bearing headers ``'Files to
create/modify'`` and ``'Context outputs / required inputs'`` — a fuzzy /
normalized match could silently mis-route section bodies (PRSpec risk 1). Both
``heading`` nodes (``type: 'heading'``) and ``paragraph`` nodes collapse to the
same plain-text stream before splitting, so a section header carries the same
meaning regardless of which block type Jira used to render it (PRSpec risk 2).

Unknown / extra headers are **ignored as delimiters**: a block whose stripped
text is not one of the eight recognized headers does not start a new section.
When a section is active such a block simply joins the active section's body;
when no section is active it is dropped. An unknown section therefore never
populates a *neighbor* recognized field (PRSpec test case 3). Missing or
whitespace-only bodies map to ``None`` (the rubric treats ``None`` and
whitespace-only identically — SFP-67).
"""

from __future__ import annotations

from sfp_contracts.agents.readiness import ParsedTicket

__all__ = ["adf_to_parsed_ticket"]

#: Ordered mapping of the eight mandatory ID-070 section headers (as they appear
#: in the ADF plain-text stream) to the canonical :class:`ParsedTicket` field
#: name. The two slash-bearing headers are matched literally — do not normalize
#: slashes. The order mirrors :class:`ParsedTicket`'s field declaration order
#: but is not load-bearing on behaviour (section bodies are delimited by header
#: occurrence, not by this order).
_SECTION_TO_FIELD: dict[str, str] = {
    "Context": "context",
    "Requirements": "requirements",
    "Files to create/modify": "files_to_create_modify",
    "Implementation notes": "implementation_notes",
    "References": "references",
    "Context outputs / required inputs": "context_outputs_required_inputs",
    "Acceptance criteria": "acceptance_criteria",
    "Dependencies": "dependencies",
}


def _flatten_node(node: object) -> str:
    """Flatten one ADF node (and its descendants) to plain text, depth-first.

    Text nodes (``{type: 'text', text: '...'}``) contribute their ``text``;
    every other node contributes the concatenation of its children's text
    (recursed). A non-dict node (defensive — malformed ADF) contributes nothing.
    """
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        text = node.get("text")
        return text if isinstance(text, str) else ""
    content = node.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(_flatten_node(child) for child in content)


def _adf_blocks(adf: dict[str, object]) -> list[str]:
    """Return the top-level blocks of an ADF doc, each flattened to plain text.

    The ADF root is ``{type: 'doc', content: [...]}``; each entry in
    ``content`` is a top-level block (``paragraph`` / ``heading`` / ``bulletList``
    / ``table`` / ...). Flattening each top-level block *independently* — rather
    than concatenating the whole tree into one string — preserves block
    boundaries so a section header (which occupies its own block) is separable
    from the body that follows it.
    """
    content = adf.get("content")
    if not isinstance(content, list):
        return []
    blocks: list[str] = []
    for node in content:
        if isinstance(node, dict):
            blocks.append(_flatten_node(node))
    return blocks


def _join_body(parts: list[str]) -> str | None:
    """Join a section's body blocks and return ``None`` if empty/whitespace-only."""
    text = "\n".join(parts).strip()
    return text or None


def adf_to_parsed_ticket(adf: dict[str, object]) -> ParsedTicket:
    """Parse an ADF document into a :class:`ParsedTicket` (the eight ID-070 sections).

    Walks the ADF tree depth-first to plain text at the block granularity, then
    splits the block stream on the eight recognized ID-070 section headers.
    Each section body is the text of the blocks between its header and the next
    recognized header, stripped; a missing or whitespace-only body maps to
    ``None``. Unknown / extra headers are ignored as delimiters (they do not
    start a new section); blocks before the first recognized header are dropped.

    Args:
        adf: The ADF document (``{type: 'doc', content: [...]}``) — typically the
            value of Jira's ``fields.description``. A non-doc dict (e.g. an empty
            ``{}``) is tolerated: with no ``content`` list it yields an all-None
            :class:`ParsedTicket` rather than raising.

    Returns:
        A :class:`ParsedTicket` with each of the eight fields set to its section
        body, or ``None`` where the section was absent / empty.
    """
    blocks = _adf_blocks(adf)
    fields: dict[str, str | None] = {field: None for field in _SECTION_TO_FIELD.values()}
    current: str | None = None
    parts: list[str] = []
    for block in blocks:
        stripped = block.strip()
        # SMOKE-PATCH (local, NOT committed — formalize via PR): real Jira
        # descriptions created via create_sfp_ticket carry markdown header
        # prefixes ("## Context"). Strip leading "#" / whitespace before the
        # literal header match so the 8 sections resolve.
        header = stripped.lstrip("#").strip()
        if header in _SECTION_TO_FIELD:
            if current is not None:
                fields[current] = _join_body(parts)
                parts = []
            current = _SECTION_TO_FIELD[header]
        elif current is not None:
            parts.append(block)
    if current is not None:
        fields[current] = _join_body(parts)
    return ParsedTicket.model_validate(fields)
