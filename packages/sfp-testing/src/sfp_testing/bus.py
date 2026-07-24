"""The in-memory :class:`FakeBus` test double for sfp-testing (SFP-49).

A thin facade that COMPOSES (wraps) an
:class:`~sfp_messaging.transport.in_memory.InMemoryTransport` and layers the
pinned assertion helpers (``assert_published`` / ``assert_not_published`` /
``published_count`` / ``messages_of``) on top of its ``published_messages``
record. It is the Message Bus fake tests use INSTEAD of an AWS/SNS/SQS
transport: handlers run for real (dispatched through the default registry,
SFP-43/46) while the published envelopes stay available for type-filtered
assertion — no LocalStack, no boto3.

Grounded in:
- MAS §4.5 — the Message Bus dispatches commands/events to handlers.
- MAS §4.7 — the envelope dissolves into a MessageContext; the handler receives
  ``(payload, context)``.
- SFP-42 — the MessageBus Protocol (the shape this fake satisfies structurally).
- SFP-43 — the type-keyed registry the composed transport dispatches through.
- SFP-44 — MessageContext bound around the handler invocation.
- SFP-46 — InMemoryTransport (the composed transport + ``published_messages``).
- ID-052 — exact-key payload-type matching; a subclass does not match its
  parent's filter (the helpers mirror the registry's ``type(...) is`` identity).

Design choices:
- FakeBus COMPOSES InMemoryTransport; it does NOT reimplement the MessageBus
  protocol or duplicate record/dispatch. ``publish`` delegates to
  ``self._transport.publish`` (which records the message BEFORE dispatch and
  dispatches its payload to the registered handler, SFP-46);
  ``published_messages`` is a read-only delegated view. T7 enforces the
  delegation: a ``@command_handler``-registered handler IS dispatched with
  ``(payload, ctx)`` when publishing through the fake.
- The assertion helpers filter by ``type(envelope.payload) is message_type``
  (identity, NOT ``isinstance``) — mirrors the registry's exact-key lookup
  (ID-052): a subclass does not match its parent's filter, and two sibling
  payload classes never cross-match.
- No AWS/SNS/SQS/boto3/moto/aiobotocore import lives in or transitively through
  this module (T9): the only runtime dependency is ``sfp-messaging``, which
  itself imports no transport SDK.
- The assert API is a PIN: ``assert_published`` (``times=`` keyword-only),
  ``assert_not_published``, ``published_count``, ``messages_of`` — do NOT widen,
  rename, or reshape it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sfp_messaging.transport.in_memory import InMemoryTransport

if TYPE_CHECKING:
    from sfp_contracts.commands.envelope import CommandEnvelope
    from sfp_contracts.events.envelope import EventEnvelope

# A published message is either a command or event envelope (SFP-42 / SFP-46).
# Typed for clarity only; the helpers never branch on the envelope type — they
# filter on ``type(envelope.payload)`` (the registry key, ID-052).
type _Message = CommandEnvelope | EventEnvelope


class FakeBus:
    """An in-memory Message Bus fake with pinned assertion helpers (SFP-49).

    A test facade that COMPOSES an
    :class:`~sfp_messaging.transport.in_memory.InMemoryTransport`: ``publish``
    delegates to the transport (which records the message AND dispatches its
    payload to the handler registered for its type via the default registry,
    SFP-43/46, inside a bound :class:`~sfp_messaging.context.MessageContext`),
    and :attr:`published_messages` is a read-only view of the transport's
    record. The assertion helpers filter that record by payload type using
    identity (``type(...) is``, ID-052).

    This is NOT a production transport (SFP-101): it performs no fan-out,
    retry, idempotency, or durability, and it never touches AWS/SNS/SQS.

    Example:
        >>> bus = FakeBus()
        >>> await bus.publish(envelope)        # records + dispatches
        >>> bus.assert_published(ExecuteCodingJob)        # >=1 match
        >>> bus.assert_published(ExecuteCodingJob, times=2)  # exact count
        >>> bus.published_count(ExecuteCodingJob)
        2
    """

    def __init__(self) -> None:
        self._transport: InMemoryTransport = InMemoryTransport()

    async def publish(self, message: _Message) -> None:
        """Delegate to the composed transport's ``publish`` (record + dispatch).

        The message is recorded on :attr:`published_messages` (record-before)
        and its payload is dispatched to the handler registered for
        ``type(message.payload)`` via the default registry (SFP-43/46), inside a
        bound :class:`~sfp_messaging.context.MessageContext`.

        Args:
            message: The command or event envelope to publish.
        """
        await self._transport.publish(message)

    @property
    def published_messages(self) -> list[_Message]:
        """Every published envelope in publish order (read-only delegated view).

        Identity-preserving: the exact envelope objects passed to
        :meth:`publish`, in publish order. Delegated straight from the composed
        transport so the record and the assertions can never drift apart.
        """
        return self._transport.published_messages

    def _matching_payloads(self, message_type: type) -> list[Any]:
        """Return the payloads whose ``type(...) is message_type`` (identity).

        Identity (``is``), NOT ``isinstance`` — mirrors the registry's exact-key
        lookup (ID-052) so a subclass does not match its parent's filter and two
        sibling payload classes never cross-match. Preserves publish order; the
        returned payload objects are the exact instances that were published.
        """
        return [
            envelope.payload
            for envelope in self._transport.published_messages
            if type(envelope.payload) is message_type  # noqa: E721
        ]

    def assert_published(self, message_type: type, *, times: int | None = None) -> None:
        """Assert that a message with payload of ``message_type`` was published.

        Filters :attr:`published_messages` by ``type(envelope.payload) is
        message_type`` (identity, ID-052).

        Args:
            message_type: The concrete payload class to filter by.
            times: When ``None`` (default), assert at least one match (raising
                ``AssertionError`` listing the actual published payload types
                when none match). When an int, assert EXACTLY that many matches.

        Raises:
            AssertionError: if no match (``times=None``) or the count differs
                from ``times``.
        """
        count = len(self._matching_payloads(message_type))
        if times is None:
            if count == 0:
                raise AssertionError(
                    f"Expected at least one published message with payload of type "
                    f"{message_type.__name__}, but none was found. "
                    f"Published payload types: {self._published_payload_type_names()}"
                )
        elif count != times:
            raise AssertionError(
                f"Expected exactly {times} published message(s) with payload of "
                f"type {message_type.__name__}, but found {count}. "
                f"Published payload types: {self._published_payload_type_names()}"
            )

    def assert_not_published(self, message_type: type) -> None:
        """Assert that NO message with payload of ``message_type`` was published.

        Args:
            message_type: The concrete payload class to filter by.

        Raises:
            AssertionError: if at least one matching message was published.
        """
        count = len(self._matching_payloads(message_type))
        if count:
            raise AssertionError(
                f"Expected no published message with payload of type "
                f"{message_type.__name__}, but found {count}."
            )

    def published_count(self, message_type: type) -> int:
        """Return the number of messages published with payload of ``message_type``.

        Args:
            message_type: The concrete payload class to filter by.

        Returns:
            The count of matching messages (0 if none).
        """
        return len(self._matching_payloads(message_type))

    def messages_of(self, message_type: type) -> list[Any]:
        """Return the payloads of type ``message_type`` in publish order.

        Identity-preserving: the exact payload instances that were published
        (``is``-equal to each envelope's ``.payload``), in publish order.

        Args:
            message_type: The concrete payload class to filter by.

        Returns:
            The matching payloads (empty list if none).
        """
        return self._matching_payloads(message_type)

    def _published_payload_type_names(self) -> list[str]:
        """Return the ``__name__`` of each published payload, in publish order."""
        return [type(envelope.payload).__name__ for envelope in self._transport.published_messages]
