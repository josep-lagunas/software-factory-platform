"""Concrete v0 authentication strategies for webhook ingress (SFP-123).

Residents: the two v0 strategies named by the SFP-113 endpoint model —
``slack_signature`` (Slack's v0 signing scheme, ported verbatim from
SFP-132) and ``github_hmac`` (GitHub's ``X-Hub-Signature-256``).

Importing this package **populates** the SFP-122
:data:`~external_events.application.auth_factory.AUTH_STRATEGY_REGISTRY`:
the ``application`` package imports it (see
:mod:`external_events.application`), so the registry is populated whenever
``external_events.application`` — and therefore the factory's default
registry — is used. No consumer needs an extra import; the registry is
never populated with instances, only classes (constructed per build).

Fences: this package registers strategies and nothing else — no selection
logic (that is the factory's, SFP-122) and no secret resolution (SFP-86,
reached only through the factory's provider seam).
"""

from __future__ import annotations

from external_events.application.auth_factory import AUTH_STRATEGY_REGISTRY
from external_events.application.strategies.github_hmac import GitHubHmacStrategy
from external_events.application.strategies.slack_signature import SlackSignatureStrategy

__all__ = [
    "GitHubHmacStrategy",
    "SlackSignatureStrategy",
]

#: SFP-123 registration under the exact ``auth_strategy`` keys of the
#: SFP-113 endpoint model. Classes, not instances — the factory constructs
#: one strategy per build with the freshly resolved secret (ID-029).
AUTH_STRATEGY_REGISTRY["slack_signature"] = SlackSignatureStrategy
AUTH_STRATEGY_REGISTRY["github_hmac"] = GitHubHmacStrategy
