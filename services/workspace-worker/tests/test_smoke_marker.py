"""Tests for :data:`SMOKE_TICKET` (SFP-227 smoke marker)."""

from workspace_worker.smoke_marker import SMOKE_TICKET


def test_smoke_ticket_value() -> None:
    assert SMOKE_TICKET == "SFP-227"
