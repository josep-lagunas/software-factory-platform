"""Slack ``v0`` signature authentication strategy (SFP-123).

Verbatim port of the proven Slack Events-API verification landed in SFP-132
(``SlackEventsEndpoint._signature_valid`` in
:mod:`communication.entrypoints.slack_events_endpoint`) onto the SFP-122
:class:`~external_events.application.auth_factory.AuthenticationStrategy`
Protocol: the secret arrives as constructor data (ID-029 — resolution stays
with the factory's injected ``SecretProvider``), and the clock stays
injectable exactly as in SFP-132, so replay-window tests pin time instead
of trusting wall-clock coincidence.

Security contract (Slack's v0 signing scheme, unchanged from SFP-132):

1. **Freshness first** — an ``X-Slack-Request-Timestamp`` whose distance
   from ``now`` exceeds 300 s in *either* direction is rejected (replay
   protection; the ``abs()`` also rejects far-future timestamps). The
   timestamp is read from the header, never re-derived, so the signed
   basestring and the verified basestring are the same string.
2. **Signature** — ``basestring = "v0:{timestamp}:{body}"`` with the body
   decoded UTF-8 using ``errors="replace"`` (SFP-132 verbatim),
   ``expected = "v0=" + hex(HMAC-SHA256(secret, basestring))``, compared
   with :func:`hmac.compare_digest` (constant time).

Decision contract (SFP-122): ``authenticate`` **never raises** — every
failure path (missing header, empty header, non-numeric timestamp, stale
timestamp, signature mismatch) is the boolean ``False``, because an auth
decision is a verdict, not an error. The one SFP-132 edge that could crash
— :func:`hmac.compare_digest` raising ``TypeError`` when the
attacker-supplied signature string is non-ASCII — is guarded here to
return ``False``. That guard is the only deliberate delta from the ported
code, required by the Protocol's never-raise contract; for every ASCII
signature the verdict is bit-identical to SFP-132's.

Header convention: the SFP-122 Protocol documents ``headers`` as a
lowercase-keyed ``Mapping[str, str]`` (the ASGI convention SFP-132's
``_header_map`` produces), so the lookups below use lowercase keys and add
no normalization of their own.

Determinism (MAS §12.7): with the clock injected, the verdict is a pure
function of ``(secret, raw_body, headers, now)`` — no network, no
randomness, no ordering.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping
from typing import Final

__all__ = ["SlackSignatureStrategy"]

#: Slack's signing-scheme version prefix (v0) — SFP-132 verbatim.
_SIGNATURE_VERSION: Final[str] = "v0"

#: Slack signature header, lowercase (SFP-122 headers are lowercase-keyed).
_HEADER_SIGNATURE: Final[str] = "x-slack-signature"

#: Slack request-timestamp header, lowercase.
_HEADER_TIMESTAMP: Final[str] = "x-slack-request-timestamp"

#: Replay window: timestamps further than this from ``now`` are rejected
#: (Slack's documented recommendation: 5 minutes) — SFP-132 verbatim.
_MAX_AGE_SECONDS: Final[int] = 300


class SlackSignatureStrategy:
    """Authenticate inbound Slack deliveries via the v0 signing scheme.

    Implements the SFP-122 :class:`AuthenticationStrategy` Protocol. The
    construction contract (one argument — the resolved secret VALUE, ID-029)
    is preserved positionally; the SFP-132 clock seam rides along as an
    optional keyword so tests can pin the replay window without touching
    the factory call shape (``SlackSignatureStrategy(secret)`` builds a
    fully working strategy on the real clock).

    Stateless after construction: the secret is bound (encoded once) at
    ``__init__`` and nothing is cached between ``authenticate`` calls.
    """

    def __init__(
        self,
        secret_value: str,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Bind the resolved signing secret and the clock seam.

        Args:
            secret_value: The endpoint's Slack signing secret, already
                resolved by the factory (never a reference — ID-029).
            clock: Injectable time source (Unix seconds). Defaults to
                :func:`time.time`. Tests inject a fixed clock so the
                freshness check is deterministic (SFP-132 seam, ported).
        """
        self._secret_bytes = secret_value.encode("utf-8")
        self._clock = clock or time.time

    def authenticate(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        """Decide one delivery: freshness first, then constant-time HMAC.

        Any failure — missing headers, empty headers, non-numeric or stale
        timestamp, signature mismatch — is a single ``False``; this method
        never raises.
        """
        signature = headers.get(_HEADER_SIGNATURE)
        timestamp = headers.get(_HEADER_TIMESTAMP)
        if not signature or not timestamp:
            return False

        try:
            ts = int(timestamp)
        except ValueError:
            return False

        if abs(self._clock() - ts) > _MAX_AGE_SECONDS:
            return False

        basestring = (
            f"{_SIGNATURE_VERSION}:{timestamp}:{raw_body.decode('utf-8', errors='replace')}"
        )
        expected = (
            _SIGNATURE_VERSION
            + "="
            + hmac.new(
                self._secret_bytes,
                basestring.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        )
        try:
            return hmac.compare_digest(expected, signature)
        except TypeError:
            # Non-ASCII attacker-supplied signature: compare_digest rejects
            # non-ASCII str. It can never equal the hex digest anyway — the
            # verdict is False (SFP-122: never raise), the only delta from
            # SFP-132, whose ASGI layer did not carry this contract.
            return False
