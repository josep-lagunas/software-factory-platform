"""Provider-agnostic outbound-message port (ID-051, ID-027, SFP-133).

This module defines the *port* half of the outbound-message seam: an abstract
``OutboundMessagePort`` plus its result and failure contracts. It contains no
provider specifics — no channel ids, no ``thread_ts``, no ``slack://`` URIs;
those live exclusively in the concrete Slack adapter
(:mod:`communication.interfaces.slack_outbound`). Future providers (email,
etc.) implement the same port (ID-051 — provider adapters wrap the client
library; AP-007 — vendor independence).

Error semantics (the absolute partition; SFP-133):

- **Transport-level failure** — connection error, HTTP 5xx, timeout — raises
  :class:`ProviderError`. No receipt is produced.
- **Any HTTP-200 response from the provider** — including a provider-reported
  failure such as ``ok:false`` (``channel_not_found``, ``invalid_auth``,
  ``rate_limited`` …) — returns a :class:`DeliveryReceipt` and **never**
  raises. A provider-reported failure is *data* (``ok=False``,
  ``error=<provider error code>``); retry policy is the caller's concern
  (out of scope — no retry logic here).

There is no code path that both returns a receipt and raises.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

__all__ = ["DeliveryReceipt", "OutboundMessagePort", "ProviderError"]


class ProviderError(Exception):
    """A transport-level failure while talking to the outbound provider.

    Raised **only** for failures of the transport itself — connection errors,
    timeouts, and HTTP 5xx responses. It is NEVER raised for a
    provider-reported failure carried in an HTTP-200 body (those arrive as a
    :class:`DeliveryReceipt` with ``ok=False`` and ``error`` set), nor for
    secret-resolution failures (the caller's provider raises its own
    :class:`~sfp_config.providers.SecretResolutionError`).

    The message MUST NOT contain secret material — carrier identifiers
    (channel refs, thread refs) and status codes only (ID-016).
    """


class DeliveryReceipt(BaseModel):
    """The outcome of one outbound message delivery attempt.

    Returned for every HTTP-200 provider response — success and
    provider-reported failure alike (a provider ``ok:false`` is data, not an
    exception). Exactly the six fields below; unknown fields are rejected.

    Attributes:
        provider_message_id: The provider-assigned id of the posted message
            (Slack: ``ts``). Empty string when the provider did not create a
            message (``ok=False`` without a ``ts``).
        channel_ref: The channel the message was addressed to (Slack: channel
            id).
        thread_ref: The thread the message was addressed to, when threading
            (Slack: parent ``thread_ts``). ``None`` for a top-level message.
        provider_reference: Canonical, provider-specific URI identifying where
            the message landed — durable enough to resolve the channel/thread
            later (Slack: ``slack://channel/<id>`` or
            ``slack://channel/<id>/thread/<ts>``). Built from the provider
            response, not the request.
        ok: Whether the provider accepted and delivered the message.
        error: The provider's error code when ``ok`` is ``False`` (e.g.
            ``channel_not_found``, ``invalid_auth``, ``rate_limited``);
            ``None`` on success.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    provider_message_id: str
    channel_ref: str
    thread_ref: str | None
    provider_reference: str
    ok: bool
    error: str | None


class OutboundMessagePort(ABC):
    """Sends an outbound message through a communication provider (ID-051).

    The provider-agnostic seam: callers address a channel (and optionally a
    thread) by opaque *ref* strings. The mapping from ref to provider-native
    identifiers is entirely the implementation's concern.

    Implementations MUST uphold the error partition documented at module
    scope: transport failure → :class:`ProviderError`; HTTP-200 (any provider
    ``ok`` value) → :class:`DeliveryReceipt`, never both.
    """

    @abstractmethod
    def send_message(
        self,
        text: str,
        *,
        channel_ref: str | None = None,
        thread_ref: str | None = None,
    ) -> DeliveryReceipt:
        """Send ``text`` to ``channel_ref`` (optionally in ``thread_ref``).

        Args:
            text: The message body to deliver.
            channel_ref: The channel to post to. ``None`` means "the
                implementation's configured default channel" (Slack:
                ``SLACK_CHANNEL_ID``).
            thread_ref: The thread to reply in, when threading. ``None`` posts
                a top-level message.

        Returns:
            The :class:`DeliveryReceipt` describing the delivery attempt.

        Raises:
            ProviderError: On a transport-level failure only (connection
                error, timeout, HTTP 5xx) — never for an HTTP-200 response,
                whatever its ``ok`` value.
        """
        raise NotImplementedError
