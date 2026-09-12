"""Tests for the GitHub ``X-Hub-Signature-256`` strategy (SFP-123).

Hermetic and deterministic: GitHub's HMAC scheme carries no clock, so every
verdict is a pure function of ``(secret, raw_body, headers)`` — no pins
needed. The signature helper reproduces GitHub's scheme exactly over the
RAW body bytes.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from external_events.application import build_authentication_strategy
from external_events.application.strategies import GitHubHmacStrategy
from sfp_config.secrets import SecretRef

SECRET = "test-webhook-secret-abcdef"

#: A representative delivery body.
BODY = b'{"action":"opened","issue":{"number":1}}'

#: The secret ref the factory-integration provider answers to.
SECRET_REF_NAME = "op://vault/github-webhook"


class _FixedProvider:
    """Structural ``SecretProvider``: hands back the webhook secret."""

    def resolve(self, ref: SecretRef) -> str:
        assert ref.name == SECRET_REF_NAME
        return SECRET


def sign(body: bytes, secret: str = SECRET) -> str:
    """Compute GitHub's X-Hub-Signature-256 value exactly as expected."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def make_strategy(secret: str = SECRET) -> GitHubHmacStrategy:
    return GitHubHmacStrategy(secret)


def headers_for(body: bytes = BODY, signature: str | None = None) -> dict[str, str]:
    """Build a header set; ``signature=None`` derives a valid one."""
    return {"x-hub-signature-256": sign(body) if signature is None else signature}


# --- valid deliveries ------------------------------------------------------


def test_valid_signature_accepted() -> None:
    """A well-formed, correctly signed delivery returns True."""
    assert make_strategy().authenticate(BODY, headers_for()) is True


def test_signature_computed_over_raw_bytes() -> None:
    """The HMAC covers the exact delivered bytes — including bodies that are
    not valid UTF-8 (no decode step in this scheme)."""
    binary = b"\x89PNG\r\n\x1a\n\xff\x00binary"
    assert make_strategy().authenticate(binary, headers_for(body=binary)) is True


# --- rejections ------------------------------------------------------------


def test_tampered_body_rejected() -> None:
    """A signature computed over different bytes never verifies."""
    tampered = b'{"action":"closed","issue":{"number":1}}'
    assert make_strategy().authenticate(tampered, headers_for(BODY)) is False


def test_signature_signed_with_wrong_secret_rejected() -> None:
    """A validly formed signature from the wrong secret → False."""
    headers = {"x-hub-signature-256": sign(BODY, secret="a-different-secret")}
    assert make_strategy().authenticate(BODY, headers) is False


def test_wrong_digest_literal_rejected() -> None:
    """A well-formed but incorrect hex digest → False."""
    headers = headers_for(signature="sha256=" + "0" * 64)
    assert make_strategy().authenticate(BODY, headers) is False


@pytest.mark.parametrize(
    "headers",
    [
        {},  # header missing entirely
        {"x-hub-signature-256": ""},  # header present but empty
    ],
    ids=["header-missing", "header-empty"],
)
def test_missing_or_empty_header_rejected(headers: dict[str, str]) -> None:
    """A missing or empty signature header → False, never a raise."""
    assert make_strategy().authenticate(BODY, headers) is False


@pytest.mark.parametrize(
    "signature",
    [
        "SHA256=" + "0" * 64,  # uppercase scheme — the compare is exact
        "0" * 64,  # missing "sha256=" prefix entirely
        "sha1=" + "0" * 40,  # wrong algorithm
    ],
    ids=["uppercase-scheme", "missing-prefix", "wrong-algorithm"],
)
def test_wrong_scheme_format_rejected(signature: str) -> None:
    """Only the exact ``sha256=<hex>`` shape can match; anything else → False."""
    headers = {"x-hub-signature-256": signature}
    assert make_strategy().authenticate(BODY, headers) is False


def test_non_ascii_signature_rejected_without_raising() -> None:
    """``hmac.compare_digest(str, str)`` raises TypeError on non-ASCII; the
    strategy must turn that into False (SFP-122: authenticate never raises)."""
    headers = {"x-hub-signature-256": "sha256=déadbeef"}
    assert make_strategy().authenticate(BODY, headers) is False


# --- registry wiring ---------------------------------------------------------


def test_registry_builds_a_working_strategy_via_the_factory() -> None:
    """SFP-123 registration: ``build_authentication_strategy("github_hmac",
    ...)`` binds the provider-resolved secret and returns a working strategy."""
    built = build_authentication_strategy("github_hmac", SECRET_REF_NAME, _FixedProvider())

    assert isinstance(built, GitHubHmacStrategy)
    assert built.authenticate(BODY, headers_for()) is True
    assert built.authenticate(BODY, headers_for(signature="sha256=" + "0" * 64)) is False
