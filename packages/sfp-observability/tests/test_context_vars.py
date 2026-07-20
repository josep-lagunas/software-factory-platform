"""Tests for ``sfp_observability.context_vars`` (SFP-48 / ID-031).

Covers the acceptance criteria:
- (T01) after :func:`bind_context`, an emitted log line's parsed JSON contains
  both ``correlation_id`` and ``causation_id`` with the bound values;
- (T02) after :func:`clear_context`, a subsequent emitted line carries neither
  key;
- (T03) :func:`bound_context` carries both IDs inside the ``with`` block and
  neither after a normal exit;
- (T04) :func:`bound_context` clears on an exceptional exit too;
- (T05) re-binding overwrites prior values.

The hard-gate tests (T01/T02/T03/T04) drive the *live* JSON renderer against a
fresh ``StringIO`` (mirroring ``tests/test_logging.py``) rather than relying on
``capture_logs`` alone. ``capture_logs`` replaces the processor chain and so
would not catch the R1 trap of binding via a raw ``contextvars.ContextVar`` —
only the real ``merge_contextvars`` + ``JSONRenderer`` path proves the IDs reach
JSON output (ID-050).
"""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
import structlog
from sfp_observability.context_vars import (
    CAUSATION_ID_KEY,
    CORRELATION_ID_KEY,
    bind_context,
    bound_context,
    clear_context,
)
from sfp_observability.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _pristine_context() -> Any:
    """Ensure a clean structlog contextvar context around every test (R2).

    Contextvars survive across tests in the same process; clearing before AND
    after each test prevents cross-test leakage that would otherwise produce
    flaky 'key present' assertions.
    """
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def _emit_and_parse(stream: StringIO) -> dict[str, Any]:
    """Configure structlog onto ``stream``, emit one event, parse it as JSON.

    The package's full processor chain (incl. ``merge_contextvars`` and
    ``JSONRenderer``) runs, so the returned dict reflects exactly what the
    ``awslogs`` driver would collect (ID-050).
    """
    configure_logging(stream=stream)
    try:
        get_logger(__name__).info("event")
    finally:
        configure_logging()  # restore the package default (stdout)
    line = stream.getvalue().strip().splitlines()[-1]
    return json.loads(line)


# --------------------------------------------------------------------------- #
# (T01) bind -> emit -> JSON contains BOTH ids
# --------------------------------------------------------------------------- #


def test_bind_context_makes_both_ids_appear_in_json() -> None:
    """(T01) After bind, the emitted JSON carries both ids with bound values."""
    bind_context("corr-1", "caus-1")
    record = _emit_and_parse(StringIO())
    assert record[CORRELATION_ID_KEY] == "corr-1"
    assert record[CAUSATION_ID_KEY] == "caus-1"


def test_bind_context_renders_none_as_json_null() -> None:
    """None is bound unconditionally and renders as JSON null (ID-031 / R3)."""
    bind_context(None, None)
    record = _emit_and_parse(StringIO())
    assert record[CORRELATION_ID_KEY] is None
    assert record[CAUSATION_ID_KEY] is None


# --------------------------------------------------------------------------- #
# (T05) re-bind overwrites prior values
# --------------------------------------------------------------------------- #


def test_rebind_overwrites_prior_values() -> None:
    """(T05) Binding again with new values replaces the prior ids."""
    bind_context("corr-old", "caus-old")
    bind_context("corr-new", "caus-new")
    record = _emit_and_parse(StringIO())
    assert record[CORRELATION_ID_KEY] == "corr-new"
    assert record[CAUSATION_ID_KEY] == "caus-new"


# --------------------------------------------------------------------------- #
# (T02) clear removes both keys
# --------------------------------------------------------------------------- #


def test_clear_context_removes_both_keys() -> None:
    """(T02) After clear, a fresh emitted line carries neither key."""
    bind_context("corr-2", "caus-2")
    clear_context()
    record = _emit_and_parse(StringIO())
    assert CORRELATION_ID_KEY not in record
    assert CAUSATION_ID_KEY not in record


# --------------------------------------------------------------------------- #
# (T03) bound_context — normal exit
# --------------------------------------------------------------------------- #


def test_bound_context_carries_ids_inside_and_clears_after_normal_exit() -> None:
    """(T03) Inside the block both ids are present; after exit neither is."""
    inside = StringIO()
    with bound_context("corr-3", "caus-3"):
        record_inside = _emit_and_parse(inside)
    assert record_inside[CORRELATION_ID_KEY] == "corr-3"
    assert record_inside[CAUSATION_ID_KEY] == "caus-3"

    record_after = _emit_and_parse(StringIO())
    assert CORRELATION_ID_KEY not in record_after
    assert CAUSATION_ID_KEY not in record_after


# --------------------------------------------------------------------------- #
# (T04) bound_context — exceptional exit (clears in finally, no suppression)
# --------------------------------------------------------------------------- #


def test_bound_context_clears_on_exception_and_propagates() -> None:
    """(T04) clear_context runs in the finally even when the block raises, and
    the exception is not suppressed."""

    def _boom() -> None:
        with bound_context("corr-4", "caus-4"):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _boom()

    # The finally ran: a fresh emitted line carries neither id.
    record_after = _emit_and_parse(StringIO())
    assert CORRELATION_ID_KEY not in record_after
    assert CAUSATION_ID_KEY not in record_after


# --------------------------------------------------------------------------- #
# module surface
# --------------------------------------------------------------------------- #


def test_module_key_constants_are_the_rendered_json_keys() -> None:
    """The exported key-name constants match the literal JSON keys (no magic)."""
    assert CORRELATION_ID_KEY == "correlation_id"
    assert CAUSATION_ID_KEY == "causation_id"
