"""Slack outbound adapter — the v0 ``OutboundMessagePort`` (ID-027, SFP-133).

Posts messages via Slack's ``chat.postMessage`` Web API using ``httpx``
(ID-051). Every Slack-specific concept lives here and only here: channel ids,
``thread_ts``, the ``slack://`` provider-reference URI scheme. Callers see the
provider-agnostic :class:`~communication.interfaces.outbound.OutboundMessagePort`
surface (``text`` / ``channel_ref`` / ``thread_ref``).

Secrets: the bot token is resolved from ``SecretRef(name="SLACK_BOT_TOKEN")``
through the injected :class:`~sfp_config.providers.SecretProvider` at call
time — never a literal, never logged (ID-016; SFP-86 verified credentials).

Error semantics (see :mod:`communication.interfaces.outbound`): transport
failure (``httpx.ConnectError`` / any ``httpx.TimeoutException`` / HTTP 5xx)
raises :class:`ProviderError`; every HTTP-200 response returns a
:class:`~communication.interfaces.outbound.DeliveryReceipt` — ``ok:false``
(including ``rate_limited``) is DATA (``ok=False``, ``error=<slack code>``),
and retrying is the caller's concern (explicitly out of scope).
"""

from __future__ import annotations

import os
from typing import Final

import httpx
from sfp_config import SecretProvider, SecretRef

from communication.interfaces.outbound import (
    DeliveryReceipt,
    OutboundMessagePort,
    ProviderError,
)

__all__ = ["SlackOutboundClient"]

#: The Slack Web API endpoint this adapter drives.
SLACK_POST_MESSAGE_URL: Final[str] = "https://slack.com/api/chat.postMessage"

#: Name of the env var carrying the default channel id (SFP-86).
_CHANNEL_ENV: Final[str] = "SLACK_CHANNEL_ID"


class SlackOutboundClient(OutboundMessagePort):
    """Posts outbound messages to Slack via ``chat.postMessage``.

    Args:
        secret_provider: Resolves ``SecretRef(name="SLACK_BOT_TOKEN")`` to the
            bot token at call time (local dev: env/``secrets.local`` via
            :class:`~sfp_config.LocalSecretProvider`; prod: SFP-78).
        client: Injectable :class:`httpx.Client` (tests inject one built on
            :class:`httpx.MockTransport`). When ``None``, a default client is
            constructed per call — the token is sent as a per-request
            ``Authorization: Bearer`` header and never outlives the call.
        timeout: Per-request timeout in seconds (default 10s, matching the
            SFP standard outbound budget).
    """

    def __init__(
        self,
        secret_provider: SecretProvider,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._secret_provider = secret_provider
        self._client = client
        self._timeout = timeout

    def send_message(
        self,
        text: str,
        *,
        channel_ref: str | None = None,
        thread_ref: str | None = None,
    ) -> DeliveryReceipt:
        """Post ``text`` to a Slack channel (optionally a thread).

        Args:
            text: Message body.
            channel_ref: Slack channel id. ``None`` falls back to the
                ``SLACK_CHANNEL_ID`` environment variable.
            thread_ref: Parent message ``ts`` to thread under
                (Slack ``thread_ts``). ``None`` posts top-level.

        Returns:
            A :class:`DeliveryReceipt` — built from the **response** body
            (channel + ``ts``), so a ``ok:false`` reply still yields a receipt
            addressed to the requested channel.

        Raises:
            ValueError: If ``channel_ref`` is ``None`` and no default
                ``SLACK_CHANNEL_ID`` is configured.
            ProviderError: On transport-level failure only — connection
                error, timeout, or HTTP 5xx. Never for an HTTP-200 response.
        """
        channel = channel_ref if channel_ref is not None else self._default_channel()
        token = self._secret_provider.resolve(SecretRef(name="SLACK_BOT_TOKEN"))

        payload: dict[str, str] = {"channel": channel, "text": text}
        if thread_ref is not None:
            # Native Slack threading: replies carry the parent's ts.
            payload["thread_ts"] = thread_ref

        try:
            if self._client is not None:
                response = self._client.post(
                    SLACK_POST_MESSAGE_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self._timeout,
                )
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(
                        SLACK_POST_MESSAGE_URL,
                        json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                    )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            # Transport failure → ProviderError. The message carries the
            # exception class name + carrier refs only — never the token.
            raise ProviderError(
                f"slack transport failure ({type(exc).__name__}) posting to channel={channel}"
            ) from exc

        if response.status_code >= 500:
            raise ProviderError(
                f"slack transport failure (HTTP {response.status_code}) posting"
                f" to channel={channel}"
            )

        return self._receipt_from_response(response, channel=channel, thread_ref=thread_ref)

    def _default_channel(self) -> str:
        """Return the configured default channel id.

        Raises:
            ValueError: When ``SLACK_CHANNEL_ID`` is unset/empty.
        """
        channel = os.environ.get(_CHANNEL_ENV, "")
        if not channel:
            raise ValueError(
                f"channel_ref is None and {_CHANNEL_ENV} is not set — no default channel configured"
            )
        return channel

    def _receipt_from_response(
        self,
        response: httpx.Response,
        *,
        channel: str,
        thread_ref: str | None,
    ) -> DeliveryReceipt:
        """Build a :class:`DeliveryReceipt` from an HTTP-200 Slack response.

        The receipt is anchored to the **response** where Slack echoes the
        delivered channel and assigns ``ts`` (falling back to the requested
        refs on an ``ok:false`` body, where Slack echoes no channel/ts), and
        ``provider_reference`` is the canonical URI built from those:

        - ``slack://channel/<channel_id>`` — top-level message
        - ``slack://channel/<channel_id>/thread/<thread_ts>`` — threaded

        Args:
            response: The HTTP-200 ``chat.postMessage`` response.
            channel: Requested channel id (fallback anchor).
            thread_ref: Requested thread ts, if any (fallback anchor).

        Returns:
            The receipt — success or provider-reported failure alike.
        """
        body = response.json()
        ok = bool(body.get("ok", False))
        message = body.get("message") if isinstance(body.get("message"), dict) else None

        delivered_channel = body.get("channel")
        if not isinstance(delivered_channel, str) or not delivered_channel:
            delivered_channel = channel

        provider_message_id = body.get("ts")
        if message is not None and (
            not isinstance(provider_message_id, str) or not provider_message_id
        ):
            message_ts = message.get("ts")
            if isinstance(message_ts, str) and message_ts:
                provider_message_id = message_ts
        if not isinstance(provider_message_id, str):
            provider_message_id = ""

        # The canonical reference identifies where the message *landed*:
        # for a threaded send, that is the thread under the parent ts.
        reference_channel = delivered_channel
        reference_thread = thread_ref
        provider_reference = f"slack://channel/{reference_channel}"
        if reference_thread:
            provider_reference += f"/thread/{reference_thread}"

        error = body.get("error") if not ok else None
        if not isinstance(error, str):
            error = None

        return DeliveryReceipt(
            provider_message_id=provider_message_id,
            channel_ref=delivered_channel,
            thread_ref=reference_thread,
            provider_reference=provider_reference,
            ok=ok,
            error=error,
        )
