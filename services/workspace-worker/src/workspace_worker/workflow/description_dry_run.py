"""Deterministic parser dry-run for enriched ticket descriptions (SFP-243).

The reusable "5-second check that saves a burned run" (per ticket): before an
enriched Jira description is written — and before any pipeline stage relaunches
off it — this module dry-runs the description through the landed parser
(:func:`workspace_worker.repo.jira.parser.adf_to_parsed_ticket`, SFP-225) and
reports, deterministically and outside the model, whether the description is
publishable: all eight ID-070 sections present AND non-empty.

Grounded in:
- SFP-243 (Jira) — the enrichment ticket. "Dry-run adf_to_parsed_ticket on the
  enriched description; refuse to publish/relaunch if any of the 8 sections is
  empty (parser dry-run exposed as a reusable helper importing
  workspace_worker.repo.jira.parser)" is the binding scope item.
- ID-070 — the eight mandatory sections. This module re-derives them from the
  parser's own ordered header->field table (:data:`parser._SECTION_TO_FIELD`)
  rather than re-declaring a list that could drift — the parser is the single
  source of truth for what a section *is*.
- MAS §12.7 — pure and deterministic: no network, no I/O, no clock; the same
  description always yields the same result.
- SFP-232 — the parser's presence/absence distinction (``""`` vs ``None``).
  The dry-run requires **non-empty** (stricter than the off-frontier rubric):
  an enriched ticket is being written specifically because the gate blocked
  the current description, so a present-but-empty section would relaunch into
  the same block — the dry-run rejects it.

Design choices:
- :class:`DryRunResult` is a frozen dataclass (not a pydantic model): the
  worker's workflow layer is plain-function/stdlib by convention
  (``readiness_rubric.py`` emits contracts, but this is an internal check
  result, not a cross-agent payload — no serialization seam is needed).
- The Markdown convenience wrapper :func:`dry_run_markdown` exists because the
  enrichment agent's model emits Markdown; it converts via
  :func:`workspace_worker.workflow.markdown_adf.markdown_to_adf` (a cited port
  of the tools helper) and delegates to :func:`dry_run_adf`. Both entry points
  share one verdict path — there is exactly one publish authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from workspace_worker.repo.jira import parser as _parser
from workspace_worker.workflow.markdown_adf import markdown_to_adf

__all__ = [
    "DryRunResult",
    "dry_run_adf",
    "dry_run_markdown",
    "empty_section_fields",
]

#: The eight ID-070 section field names, derived from the parser's own ordered
#: header table. Importing the parser's table (rather than re-declaring a
#: literal list) pins this module against the single source of truth: if the
#: parser's sections ever change, the dry-run changes with them — no drift.
#: The value order is the parser's header order; ``__all__`` field names below
#: are looked up from the parser mapping, never re-typed here.
_SECTION_FIELDS: tuple[str, ...] = tuple(_parser._SECTION_TO_FIELD.values())


@dataclass(frozen=True, slots=True)
class DryRunResult:
    """Outcome of a description dry-run.

    Attributes:
        ok: ``True`` iff the description parses and every one of the eight
            ID-070 sections is present AND non-empty — the sole condition under
            which a description may be published / a relaunch may proceed.
        parsed: The :class:`~sfp_contracts.agents.readiness.ParsedTicket` the
            parser produced (always populated; an unparseable input still
            yields the parser's tolerant all-``None`` ticket, and the failures
            list says which sections were empty/absent).
        empty_sections: The section field names that were absent (``None``) or
            present-but-empty (``""`` / whitespace-only), in parser order.
            Empty iff ``ok``.
        failures: Human-readable failure lines (one per empty section, plus a
            first line when any section was missing entirely) — deterministic,
            ordered, and intended for the enrichment loop's retry prompt and
            the MANUAL_REQUIRED report.
    """

    ok: bool
    parsed: object
    empty_sections: tuple[str, ...] = field(default=())
    failures: tuple[str, ...] = field(default=())


def empty_section_fields(adf: dict[str, object]) -> tuple[str, ...]:
    """Return the ID-070 section fields that are absent or empty in ``adf``.

    Runs :func:`adf_to_parsed_ticket` over ``adf`` and reports every section
    whose parsed value is not a non-empty, non-whitespace string — i.e. both
    the absent (``None``) and present-but-empty (``""``) cases. Order follows
    :data:`_SECTION_FIELDS` (the parser's header order), so the result is
    deterministic for a given input.

    Args:
        adf: The ADF document to check (typically the enriched description
            after :func:`~workspace_worker.workflow.markdown_adf.markdown_to_adf`).

    Returns:
        A tuple of section field names, empty iff all eight sections are
        present and non-empty.
    """
    parsed = _parser.adf_to_parsed_ticket(adf)
    return tuple(name for name in _SECTION_FIELDS if not (getattr(parsed, name) or "").strip())


def dry_run_adf(adf: dict[str, object]) -> DryRunResult:
    """Dry-run an ADF description through the parser (the publish gate).

    Converts ``adf`` via :func:`adf_to_parsed_ticket` and builds a
    :class:`DryRunResult` whose ``ok`` is ``True`` iff **every** one of the
    eight ID-070 sections parsed to a non-empty, non-whitespace value. This
    function is the sole publish authority for enriched descriptions: a
    ``False`` result means the description must NOT be written as final and
    the failure counts against the enrichment loop bound (SFP-243).

    Args:
        adf: The ADF document to check.

    Returns:
        The :class:`DryRunResult`. ``failures`` carries one deterministic line
        per empty/absent section (plus a summary line when any were missing
        entirely), so callers can feed the exact reasons back into a retry
        prompt or a MANUAL_REQUIRED report.
    """
    parsed = _parser.adf_to_parsed_ticket(adf)
    empty = empty_section_fields(adf)
    failures: list[str] = []
    if empty:
        for name in empty:
            value = getattr(parsed, name, None)
            if value is None:
                failures.append(f"section missing: {name}")
            else:
                failures.append(f"section empty: {name}")
    return DryRunResult(
        ok=not empty,
        parsed=parsed,
        empty_sections=empty,
        failures=tuple(failures),
    )


def dry_run_markdown(markdown: str) -> DryRunResult:
    """Dry-run a Markdown description: convert to ADF, then gate.

    Convenience wrapper for the enrichment agent's model output, which is
    Markdown. Delegates the conversion to
    :func:`~workspace_worker.workflow.markdown_adf.markdown_to_adf` and the
    verdict to :func:`dry_run_adf` — one conversion, one verdict path, no
    duplicated logic.

    Args:
        markdown: The enriched description as Markdown.

    Returns:
        The :class:`DryRunResult` from :func:`dry_run_adf` on the converted
        document.
    """
    return dry_run_adf(markdown_to_adf(markdown))
