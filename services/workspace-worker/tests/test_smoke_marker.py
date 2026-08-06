"""Tests for the SFP-227 smoke marker module."""

from workspace_worker.smoke_marker import SMOKE_TICKET


def test_smoke_ticket_constant() -> None:
    """SMOKE_TICKET equals the SFP-227 ticket key."""
    assert SMOKE_TICKET == "SFP-227"
