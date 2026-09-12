"""GitHub webhook HMAC authentication strategy (SFP-123).

Authenticates GitHub webhook deliveries via the ``X-Hub-Signature-256``
scheme (the HMAC flavour of GitHub's webhook signatures): the expected
value is ``"sha256=" + hex(HMAC-SHA256(secret, raw_body))`` computed over
the **raw** request body bytes — no decoding, no re-serialization; GitHub
signs the exact bytes it delivered, so anything other than the verbatim
body is a mismatch — compared with :func:`hmac.compare_digest` (constant
time).

Implements the SFP-122
:class:`~external_events.application.auth_factory.AuthenticationStrategy`
Protocol: the secret arrives as constructor data (ID-029 — resolution
stays with the factory's injected ``SecretProvider``), and the strategy is
stateless afterwards. No freshness window exists in GitHub's scheme
(GitHub documents only the HMAC), so — unlike
:class:`~external_events.application.strategies.slack_signature.SlackSignatureStrategy`
— there is no clock and no replay check here; adding one would be
inventing provider behaviour, not implementing it.

Decision contract (SFP-122): ``authenticate`` **never raises** — a missing
or empty ``X-Hub-Signature-256`` header, a wrong scheme prefix, a tampered
body, a wrong secret, or any other mismatch is the boolean ``False``. The
one edge that could crash — :func:`hmac.compare_digest` raising
``TypeError`` when the attacker-supplied signature string is non-ASCII —
is guarded to return ``False``, mirroring the slack strategy's guard for
the Protocol's never-raise contract.

Header convention: the SFP-122 Protocol documents ``headers`` as a
lowercase-keyed ``Mapping[str, str]`` (the ASGI convention), so the lookup
below uses a lowercase key and adds no normalization of its own.

Determinism (MAS §12.7): the verdict is a pure function of
``(secret, raw_body, headers)`` — no clock, no network, no randomness.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Final

__all__ = ["GitHubHmacStrategy"]

#: GitHub's HMAC signature header, lowercase (SFP-122 headers are
#: lowercase-keyed).
_HEADER_SIGNATURE: Final[str] = "x-hub-signature-256"

#: GitHub's ``X-Hub-Signature-256`` value prefix.
_SCHEME_PREFIX: Final[str] = "sha256="


class GitHubHmacStrategy:
    """Authenticate inbound GitHub webhook deliveries (HMAC-SHA256).

    Implements the SFP-122 :class:`AuthenticationStrategy` Protocol exactly:
    one construction argument (the resolved secret VALUE, ID-029), then a
    pure ``authenticate(raw_body, headers) -> bool`` decision. Stateless
    after construction — the secret is bound (encoded once) at ``__init__``
    and nothing is cached between calls.
    """

    def __init__(self, secret_value: str) -> None:
        """Bind the resolved webhook secret.

        Args:
            secret_value: The endpoint's GitHub webhook secret, already
                resolved by the factory (never a reference — ID-029).
        """
        self._secret_bytes = secret_value.encode("utf-8")

    def authenticate(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        """Constant-time HMAC-SHA256 comparison over the raw body bytes.

        Any failure — missing or empty header, wrong scheme prefix, tampered
        body, wrong secret — is a single ``False``; this method never raises.
        """
        provided = headers.get(_HEADER_SIGNATURE)
        if not provided:
            return False

        expected = (
            _SCHEME_PREFIX + hmac.new(self._secret_bytes, raw_body, hashlib.sha256).hexdigest()
        )
        try:
            return hmac.compare_digest(expected, provided)
        except TypeError:
            # Non-ASCII attacker-supplied signature: compare_digest rejects
            # non-ASCII str. It can never equal the hex digest anyway — the
            # verdict is False (SFP-122: never raise).
            return False
