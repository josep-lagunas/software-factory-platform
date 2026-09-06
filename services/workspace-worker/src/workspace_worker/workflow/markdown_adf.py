"""Markdown -> ADF conversion for enriched ticket descriptions (SFP-243).

The enrichment agent's model emits the enriched description as **Markdown**
(the shape a prompt can steer reliably); Jira's ``fields.description`` is
**ADF** (Atlassian Document Format). This module is the deterministic bridge:
a pure function, no network / no I/O, ported from the landed
:func:`tools.create_jira_tickets.markdown_to_adf` helper (cited, not
forked-then-drifted — SFP-243's dependency list names that helper as LANDED;
``tools/`` is a script directory, not an importable package, so the worker
carries a cited port rather than an undeclared path hack).

Grounded in:
- SFP-243 (Jira) — the enrichment ticket; "markdown_to_adf helpers
  (tools/create_jira_tickets.py)" is a named LANDED dependency.
- SFP-225 — the ADF node model this converter targets (``{type, content}``
  blocks whose leaves are ``{type: 'text', text: '...'}``).
- ID-070 — the eight literal section headers. A **heading** line
  (``# .. ###### Text``) or the tools helper's ``**Text:**`` form emits a real
  ADF ``heading`` block whose text is the *stripped literal* — so ``##
  Context`` becomes a block whose flattened text is exactly ``Context``, which
  :func:`workspace_worker.repo.jira.parser.adf_to_parsed_ticket` matches
  literally. A *decorated* header (``## 1. Context``, ``Context —``) survives
  as its decorated text and therefore does NOT match — the dry-run gate
  rejects it (SFP-243 acceptance criterion).

Design choices:
- Line-oriented, single pass, deterministic ordering — the same input always
  yields the same ADF (MAS §12.7). No templating engine, no extensions.
- Consecutive ``- `` / ``* `` lines buffer into ONE ``bulletList`` block
  (mirroring the tools helper's ``bullet_buf``); every other non-empty line
  becomes a ``paragraph`` block. Blank lines are structural separators only.
- Inline formatting (code spans, bold) is emitted as plain text nodes: the
  downstream consumer (:func:`adf_to_parsed_ticket`) flattens to plain text
  anyway, so marks would add surface without changing the parsed sections.
"""

from __future__ import annotations

import re

__all__ = ["markdown_to_adf"]

#: ATX heading: one to six ``#`` characters, whitespace, then the header text.
#: The emitted ADF heading block carries the header text WITHOUT the ``#``
#: prefix so the parser's literal section match sees the exact ID-070 string.
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

#: The tools helper's bold-heading form: a line that is exactly ``**Text:**``.
#: Emits a heading whose text is ``Text`` (the colon and markers dropped).
_BOLD_HEADING = re.compile(r"^\*\*([^*]+):\*\*$")

#: Bullet item: a line starting ``- `` or ``* `` followed by item text.
_BULLET = re.compile(r"^[-*]\s+(.*)$")


def _text_node(text: str) -> dict[str, object]:
    """Return an ADF text leaf carrying ``text`` verbatim."""
    return {"type": "text", "text": text}


def _paragraph(line: str) -> dict[str, object]:
    """Return an ADF paragraph block carrying ``line`` as its single text child."""
    return {"type": "paragraph", "content": [_text_node(line)]}


def _heading(text: str, level: int) -> dict[str, object]:
    """Return an ADF heading block carrying ``text`` at ``level`` (clamped 1..6)."""
    return {
        "type": "heading",
        "attrs": {"level": max(1, min(6, level))},
        "content": [_text_node(text)],
    }


def _bullet_list(items: list[str]) -> dict[str, object]:
    """Return an ADF ``bulletList`` block with one ``listItem`` per buffered line."""
    return {
        "type": "bulletList",
        "content": [{"type": "listItem", "content": [_paragraph(item)]} for item in items],
    }


def markdown_to_adf(markdown: str) -> dict[str, object]:
    """Convert a Markdown ticket body into a full ADF document.

    Line grammar (deterministic, single pass):

    - ``#`` .. ``######`` heading — ``heading`` block whose text is the header
      WITHOUT the ``#`` prefix (so ``## Context`` flattens to the literal
      ``Context`` the parser matches).
    - ``**Text:**`` (exactly) — ``heading`` block whose text is ``Text``
      (the tools helper's convention).
    - ``- item`` / ``* item`` — buffered into one ``bulletList`` block per run
      of consecutive bullet lines.
    - blank line — flushes the bullet buffer (structural separator only).
    - anything else — a ``paragraph`` block carrying the line verbatim.

    Args:
        markdown: The Markdown text of the enriched description. An empty /
            whitespace-only string yields an empty document
            (``{"type": "doc", "version": 1, "content": []}``) rather than
            raising — emptiness is the dry-run gate's rejection to make
            (:func:`workspace_worker.workflow.description_dry_run.dry_run_parse`),
            not this converter's.

    Returns:
        The ADF document (``{"type": "doc", "version": 1, "content": [...]}``)
        suitable for Jira's ``fields.description``.
    """
    content: list[dict[str, object]] = []
    bullet_buf: list[str] = []

    def _flush_bullets() -> None:
        if bullet_buf:
            content.append(_bullet_list(bullet_buf))
            bullet_buf.clear()

    for raw_line in markdown.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            _flush_bullets()
            continue
        bold = _BOLD_HEADING.match(stripped)
        if bold is not None:
            _flush_bullets()
            content.append(_heading(bold.group(1).strip(), 3))
            continue
        atx = _ATX_HEADING.match(stripped)
        if atx is not None:
            _flush_bullets()
            content.append(_heading(atx.group(2).strip(), len(atx.group(1))))
            continue
        bullet = _BULLET.match(stripped)
        if bullet is not None:
            bullet_buf.append(bullet.group(1).strip())
            continue
        _flush_bullets()
        content.append(_paragraph(stripped))

    _flush_bullets()
    return {"type": "doc", "version": 1, "content": content}
