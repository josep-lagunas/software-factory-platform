"""Tests for the Slack v0 signature strategy (SFP-123).

Mirrors the SFP-132 endpoint's authentication tests (the porting source):
the clock is injected and fixed, every timestamp is chosen relative to that
fixed ``NOW``, and the signature helper reproduces Slack's v0 scheme
exactly — freshness and verdicts never depend on wall-clock coincidence.
Fully hermetic: no environment, no network, no bus.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable

import pytest
from external_events.application import build_authentication_strategy
from external_events.application.strategies import SlackSignatureStrategy
from sfp_config.secrets import SecretRef

SIGNING_SECRET = "test-signing-secret-abcdef"

#: Fixed "now" for the injected clock — the SFP-132 convention; timestamps
#: are chosen relative to it, so freshness never depends on wall-clock
#: coincidence.
NOW = 1_800_000_000.0

#: A representative delivery body.
BODY = b'{"type":"event_callback","event":{"type":"app_mention","ts":"1712345678.123456"}}'

#: The secret ref the factory-integration provider answers to.
SECRET_REF_NAME = "op://vault/slack-signing"


class _FixedProvider:
    """Structural ``SecretProvider``: hands back the signing secret."""

    def resolve(self, ref: SecretRef) -> str:
        assert ref.name == SECRET_REF_NAME
        return SIGNING_SECRET


def sign(timestamp: str | int, body: bytes | str, secret: str = SIGNING_SECRET) -> str:
    """Compute Slack's v0 signature exactly as the strategy expects it."""
    if isinstance(body, bytes):
        # The strategy's basestring decode (SFP-132 verbatim).
        body = body.decode("utf-8", errors="replace")
    basestring = f"v0:{timestamp}:{body}"
    digest = hmac.new(
        secret.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v0={digest}"


def make_strategy(clock: Callable[[], float] | None = None) -> SlackSignatureStrategy:
    """Build the strategy with the test secret and (by default) a pinned clock."""
    return SlackSignatureStrategy(
        SIGNING_SECRET, clock=clock if clock is not None else (lambda: NOW)
    )


def headers_for(
    body: bytes | str = BODY,
    timestamp: str | int | None = None,
    signature: str | None = None,
) -> dict[str, str]:
    """Build a header set; ``None`` parts are derived to be valid."""
    ts = int(NOW) if timestamp is None else timestamp
    sig = sign(ts, body) if signature is None else signature
    return {"x-slack-signature": sig, "x-slack-request-timestamp": str(ts)}


# --- valid deliveries ------------------------------------------------------


def test_valid_signature_accepted() -> None:
    """A well-formed, correctly signed, fresh delivery returns True."""
    assert make_strategy().authenticate(BODY, headers_for()) is True


@pytest.mark.parametrize("age_seconds", [0, 1, 60, 299, 300])
def test_fresh_timestamp_accepted(age_seconds: int) -> None:
    """Inside the 300 s replay window (inclusive at the boundary) → True."""
    headers = headers_for(timestamp=int(NOW) - age_seconds)
    assert make_strategy().authenticate(BODY, headers) is True


def test_default_clock_is_the_real_clock() -> None:
    """Without an injected clock the strategy reads wall time, so a
    signature stamped 'now' verifies. Deterministic: both clock reads occur
    within the same process moments apart; the window is 300 s."""
    strategy = SlackSignatureStrategy(SIGNING_SECRET)
    headers = headers_for(timestamp=int(time.time()))
    assert strategy.authenticate(BODY, headers) is True


# --- rejections ------------------------------------------------------------


def test_tampered_body_rejected() -> None:
    """A signature computed over a different body never verifies."""
    tampered = b'{"type":"event_callback","event":{}}'
    assert make_strategy().authenticate(tampered, headers_for(BODY)) is False


def test_signature_signed_with_wrong_secret_rejected() -> None:
    """A validly formed signature from the wrong secret → False."""
    headers = headers_for(signature=sign(int(NOW), BODY, secret="a-different-secret"))
    assert make_strategy().authenticate(BODY, headers) is False


def test_wrong_signature_literal_rejected() -> None:
    """A well-formed but incorrect hex digest → False."""
    headers = headers_for(signature="v0=" + "0" * 64)
    assert make_strategy().authenticate(BODY, headers) is False


@pytest.mark.parametrize(
    "headers",
    [
        {},  # both missing
        {"x-slack-request-timestamp": str(int(NOW))},  # signature missing
        {"x-slack-signature": sign(int(NOW), BODY)},  # timestamp missing
        {"x-slack-signature": "", "x-slack-request-timestamp": str(int(NOW))},
        {"x-slack-signature": sign(int(NOW), BODY), "x-slack-request-timestamp": ""},
    ],
    ids=[
        "both-missing",
        "signature-missing",
        "timestamp-missing",
        "signature-empty",
        "timestamp-empty",
    ],
)
def test_missing_or_empty_headers_rejected(headers: dict[str, str]) -> None:
    """Missing or empty header values → False, never a raise (SFP-132's
    ``not signature or not timestamp`` edge, ported)."""
    assert make_strategy().authenticate(BODY, headers) is False


def test_non_numeric_timestamp_rejected_without_raising() -> None:
    """A malformed timestamp string is a False verdict, not an exception."""
    headers = headers_for(timestamp="not-a-number")
    assert make_strategy().authenticate(BODY, headers) is False


@pytest.mark.parametrize("age_seconds", [301, 3600, 86_400])
def test_stale_timestamp_rejected(age_seconds: int) -> None:
    """A correctly signed delivery older than the replay window → False."""
    headers = headers_for(timestamp=int(NOW) - age_seconds)
    assert make_strategy().authenticate(BODY, headers) is False


def test_future_timestamp_beyond_window_rejected() -> None:
    """The window is symmetric (SFP-132's ``abs()``): far-future → False."""
    headers = headers_for(timestamp=int(NOW) + 600)
    assert make_strategy().authenticate(BODY, headers) is False


def test_non_ascii_signature_rejected_without_raising() -> None:
    """``hmac.compare_digest(str, str)`` raises TypeError on non-ASCII; the
    strategy must turn that into False (SFP-122: authenticate never raises)."""
    headers = {"x-slack-signature": "v0=déadbeef", "x-slack-request-timestamp": str(int(NOW))}
    assert make_strategy().authenticate(BODY, headers) is False


# --- basestring edge (ported verbatim from SFP-132) ------------------------


def test_basestring_uses_replacement_decoding() -> None:
    """SFP-132 decodes the body UTF-8 with ``errors='replace'``; a signature
    computed over the same replacement string verifies (invalid-UTF-8 edge)."""
    body = b'{"text":"\xff\xfe"}'
    assert make_strategy().authenticate(body, headers_for(body=body)) is True


# --- purity + registry wiring ----------------------------------------------


def test_authenticate_leaves_the_headers_mapping_untouched() -> None:
    """The Protocol's read-only input contract: headers mutate nothing."""
    headers = headers_for()
    snapshot = dict(headers)

    make_strategy().authenticate(BODY, headers)

    assert headers == snapshot


def test_registry_builds_a_working_strategy_via_the_factory() -> None:
    """SFP-123 registration: ``build_authentication_strategy("slack_signature",
    ...)`` binds the provider-resolved secret and returns a working strategy
    (the factory default uses the real clock, so stamp 'now')."""
    built = build_authentication_strategy("slack_signature", SECRET_REF_NAME, _FixedProvider())

    assert isinstance(built, SlackSignatureStrategy)
    assert built.authenticate(BODY, headers_for(timestamp=int(time.time()))) is True
    assert built.authenticate(BODY, headers_for(timestamp=int(time.time()) - 3600)) is False
