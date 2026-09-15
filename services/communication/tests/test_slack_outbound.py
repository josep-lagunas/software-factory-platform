"""Tests for the outbound port + Slack adapter (SFP-133, ID-027, ID-051).

Two partitions, per the ticket's absolute error semantics:

1. **HTTP-200 → receipt, never raises.** ``ok:true`` and every ``ok:false``
   body (``channel_not_found``, ``invalid_auth``, ``rate_limited``) return a
   ``DeliveryReceipt`` carrying the slack error code in ``error``.
2. **Transport failure → ProviderError, no receipt.** 5xx, timeout, and
   connection error raise ``ProviderError``.

All network interaction is stubbed via ``httpx.MockTransport``. The single
opt-in live test posts a real message and is skipped unless ``SLACK_LIVE=1``.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest
from communication.interfaces import (
    DeliveryReceipt,
    OutboundMessagePort,
    ProviderError,
    SlackOutboundClient,
)
from sfp_config import SecretRef

POST_URL = "https://slack.com/api/chat.postMessage"
TOKEN = "xoxb-test-token-123"


class FakeSecretProvider:
    """In-memory ``SecretProvider``: fixed token for SLACK_BOT_TOKEN only."""

    def resolve(self, ref: SecretRef) -> str:
        if ref.name == "SLACK_BOT_TOKEN":
            return TOKEN
        raise AssertionError(f"unexpected secret ref: {ref.name}")


def make_client(handler: Any) -> SlackOutboundClient:
    """Build a ``SlackOutboundClient`` on a stubbed ``httpx.MockTransport``."""
    transport = httpx.MockTransport(handler)
    return SlackOutboundClient(
        FakeSecretProvider(),
        client=httpx.Client(transport=transport),
    )


def ok_response(channel: str = "C123", ts: str = "1712345678.123456") -> httpx.Response:
    return httpx.Response(
        200,
        json={"ok": True, "channel": channel, "ts": ts, "message": {"ts": ts}},
    )


def error_response(error: str) -> httpx.Response:
    return httpx.Response(200, json={"ok": False, "error": error})


# ---------------------------------------------------------------------------
# Port + receipt contract
# ---------------------------------------------------------------------------


def test_slack_client_is_an_outbound_message_port() -> None:
    assert isinstance(SlackOutboundClient(FakeSecretProvider()), OutboundMessagePort)


def test_outbound_port_is_abstract() -> None:
    with pytest.raises(TypeError):
        OutboundMessagePort()  # type: ignore[abstract]


def test_delivery_receipt_has_exactly_the_six_specified_fields() -> None:
    fields = set(DeliveryReceipt.model_fields)
    assert fields == {
        "provider_message_id",
        "channel_ref",
        "thread_ref",
        "provider_reference",
        "ok",
        "error",
    }


def test_delivery_receipt_rejects_unknown_fields() -> None:
    with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
        DeliveryReceipt(  # type: ignore[call-arg]
            provider_message_id="ts",
            channel_ref="C1",
            thread_ref=None,
            provider_reference="slack://channel/C1",
            ok=True,
            error=None,
            unexpected_field="boom",
        )


# ---------------------------------------------------------------------------
# Partition 1a — HTTP-200 ok:true → receipt
# ---------------------------------------------------------------------------


def test_ok_true_returns_receipt_with_canonical_reference() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok_response(channel="C123", ts="1712345678.123456")

    receipt = make_client(handler).send_message("hello", channel_ref="C123")

    assert isinstance(receipt, DeliveryReceipt)
    assert receipt.ok is True
    assert receipt.error is None
    assert receipt.provider_message_id == "1712345678.123456"
    assert receipt.channel_ref == "C123"
    assert receipt.thread_ref is None
    assert receipt.provider_reference == "slack://channel/C123"


def test_request_carries_bearer_token_channel_and_thread_ts() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode())
        captured["url"] = str(request.url)
        return ok_response()

    make_client(handler).send_message(
        "threaded hello", channel_ref="C123", thread_ref="1712340000.000100"
    )

    assert captured["auth"] == f"Bearer {TOKEN}"
    assert captured["url"] == POST_URL
    assert captured["body"] == {
        "channel": "C123",
        "text": "threaded hello",
        "thread_ts": "1712340000.000100",
    }


def test_threaded_send_reference_includes_thread_segment() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok_response(channel="C123", ts="1712345678.999")

    receipt = make_client(handler).send_message(
        "reply", channel_ref="C123", thread_ref="1712340000.000100"
    )

    assert receipt.thread_ref == "1712340000.000100"
    assert receipt.provider_reference == "slack://channel/C123/thread/1712340000.000100"


def test_no_thread_key_when_top_level() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert "thread_ts" not in body
        return ok_response()

    make_client(handler).send_message("top-level", channel_ref="C123")


def test_channel_defaults_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_CHANNEL_ID", "CDEFAULT")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["channel"] == "CDEFAULT"
        return ok_response(channel="CDEFAULT")

    receipt = make_client(handler).send_message("to default")
    assert receipt.channel_ref == "CDEFAULT"


def test_missing_channel_and_no_default_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_CHANNEL_ID", raising=False)
    with pytest.raises(ValueError, match="SLACK_CHANNEL_ID"):
        make_client(lambda request: ok_response()).send_message("orphan")


def test_missing_channel_env_ignored_when_channel_ref_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_CHANNEL_ID", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["channel"] == "CEXPLICIT"
        return ok_response(channel="CEXPLICIT")

    receipt = make_client(handler).send_message("explicit", channel_ref="CEXPLICIT")
    assert receipt.channel_ref == "CEXPLICIT"


# ---------------------------------------------------------------------------
# Partition 1b — HTTP-200 ok:false → receipt (never raises)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slack_error",
    ["channel_not_found", "invalid_auth", "rate_limited"],
)
def test_ok_false_returns_receipt_with_slack_error_code(slack_error: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return error_response(slack_error)

    receipt = make_client(handler).send_message("hello", channel_ref="CBAD")

    assert isinstance(receipt, DeliveryReceipt)
    assert receipt.ok is False
    assert receipt.error == slack_error
    # Anchored to the requested channel; no ts echoed by Slack on failure.
    assert receipt.channel_ref == "CBAD"
    assert receipt.provider_message_id == ""
    assert receipt.provider_reference == "slack://channel/CBAD"


def test_ok_false_threaded_receipt_keeps_thread_reference() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return error_response("rate_limited")

    receipt = make_client(handler).send_message(
        "retry me", channel_ref="C123", thread_ref="1712340000.000100"
    )
    assert receipt.ok is False
    assert receipt.error == "rate_limited"
    assert receipt.provider_reference == "slack://channel/C123/thread/1712340000.000100"


# ---------------------------------------------------------------------------
# Partition 2 — transport failure → ProviderError (no receipt)
# ---------------------------------------------------------------------------


def test_http_500_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(ProviderError, match="HTTP 500"):
        make_client(handler).send_message("hello", channel_ref="C123")


def test_http_503_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    with pytest.raises(ProviderError, match="HTTP 503"):
        make_client(handler).send_message("hello", channel_ref="C123")


def test_timeout_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out reading response")

    with pytest.raises(ProviderError, match="ReadTimeout"):
        make_client(handler).send_message("hello", channel_ref="C123")


def test_connect_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect")

    with pytest.raises(ProviderError, match="ConnectError"):
        make_client(handler).send_message("hello", channel_ref="C123")


def test_provider_error_message_never_contains_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(ProviderError) as excinfo:
        make_client(handler).send_message("hello", channel_ref="C123")
    assert TOKEN not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Response-shape resilience (canonical reference built from the response)
# ---------------------------------------------------------------------------


def test_receipt_falls_back_to_message_ts_when_top_level_ts_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "channel": "C123", "message": {"ts": "1712999999.000001"}},
        )

    receipt = make_client(handler).send_message("hello", channel_ref="C123")
    assert receipt.provider_message_id == "1712999999.000001"


def test_receipt_survives_minimal_ok_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    receipt = make_client(handler).send_message("hello", channel_ref="C123")
    assert receipt.ok is True
    assert receipt.channel_ref == "C123"
    assert receipt.provider_message_id == ""
    assert receipt.provider_reference == "slack://channel/C123"


# ---------------------------------------------------------------------------
# Block Kit + chat.update (SFP-258 — Block Kit summaries, button teardown)
# ---------------------------------------------------------------------------


UPDATE_URL = "https://slack.com/api/chat.update"


def test_send_message_without_blocks_posts_plain_text() -> None:
    """``blocks=None`` (the default) keeps the pre-SFP-258 payload shape."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return ok_response()

    receipt = make_client(handler).send_message("hello", channel_ref="C123")

    assert receipt.ok is True
    assert seen["url"] == POST_URL
    assert seen["payload"] == {"channel": "C123", "text": "hello"}
    assert "blocks" not in seen["payload"]


def test_send_message_with_blocks_carries_the_block_kit_layout() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return ok_response()

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "summary"}}]
    receipt = make_client(handler).send_message(
        "fallback", channel_ref="C123", thread_ref="1712345678.000001", blocks=blocks
    )

    assert receipt.ok is True
    assert seen["payload"]["blocks"] == blocks
    assert seen["payload"]["text"] == "fallback"
    assert seen["payload"]["thread_ts"] == "1712345678.000001"


def test_update_message_posts_to_chat_update_with_channel_and_ts() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "channel": "C123", "ts": "1712345678.123456"})

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "confirmed summary"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":white_check_mark:"}]},
    ]
    receipt = make_client(handler).update_message(
        channel_ref="C123",
        ts="1712345678.123456",
        text="confirmed summary",
        blocks=blocks,
        thread_ref="1712345678.000001",
    )

    assert receipt.ok is True
    assert seen["url"] == UPDATE_URL
    assert seen["payload"]["channel"] == "C123"
    assert seen["payload"]["ts"] == "1712345678.123456"
    assert seen["payload"]["blocks"] == blocks
    # The receipt reference anchors to the thread, per the port contract.
    assert receipt.provider_reference == "slack://channel/C123/thread/1712345678.000001"


@pytest.mark.parametrize("missing", ["channel_ref", "ts"])
def test_update_message_requires_channel_and_ts(missing: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP call expected")

    kwargs: dict[str, Any] = {"channel_ref": "C123", "ts": "1712345678.123456"}
    kwargs[missing] = ""
    with pytest.raises(ValueError, match="channel_ref and ts"):
        make_client(handler).update_message(**kwargs)


def test_update_message_5xx_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(ProviderError, match="chat|transport|HTTP"):
        make_client(handler).update_message(channel_ref="C123", ts="1712345678.123456", text="x")


def test_update_message_ok_false_returns_receipt_with_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "message_not_found"})

    receipt = make_client(handler).update_message(
        channel_ref="C123", ts="1712345678.123456", text="x"
    )
    assert receipt.ok is False
    assert receipt.error == "message_not_found"


# ---------------------------------------------------------------------------
# Opt-in live verification (SLACK_LIVE=1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("SLACK_LIVE") != "1",
    reason="live Slack post — set SLACK_LIVE=1 (plus SLACK_BOT_TOKEN and SLACK_CHANNEL_ID)",
)
def test_live_post_to_configured_channel() -> None:
    """Post 'SFP outbound verification' to the real channel (SFP-86 creds).

    Requires ``SLACK_BOT_TOKEN`` and ``SLACK_CHANNEL_ID`` in the environment
    (the local secret provider resolves the token from env). This is the one
    un-stubbed path; skipped in CI by default.
    """
    from sfp_config import LocalSecretProvider

    client = SlackOutboundClient(LocalSecretProvider())
    receipt = client.send_message("SFP outbound verification")

    assert receipt.ok is True, f"live post failed: {receipt.error}"
    assert receipt.provider_message_id != ""
    assert receipt.provider_reference.startswith("slack://channel/")
