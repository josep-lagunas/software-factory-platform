"""Slack operational-command interpreter — the deterministic parse layer (SFP-244).

Turns the **raw** Slack message text (as published verbatim by SFP-132's
inbound receiver inside the ``ExternalEventReceived`` envelope) into a typed
:class:`OperationalCommand` or ``None``. Parse layer **only**:

- **No execution.** ``RUN_TICKET`` does not spawn a pipeline, ``STOP_RUN``
  does not kill anything, ``APPROVE_PR`` does not merge. Consumers of the
  returned command own all of that (follow-up ticket).
- **No replies.** No Slack posting, no ``help`` fallback messaging — that is
  the inbound wiring caller's job.
- **No NLP.** Free-form interpretation is deferred to the intake-funnel
  agent. Anything that does not match the pinned grammar is ``None`` —
  never an exception, never a guessed kind.
- **No I/O.** Pure text in, data out; imports only ``pydantic`` and stdlib
  ``re`` (asserted by the import-surface purity test).

Grammar (case-insensitive, anchored full match, after stripping at most ONE
leading raw Slack mention entity ``<@U123ABC>`` / ``<@U123ABC|display>``):

============================  =============  ==============================
Input                         Kind           Captures
============================  =============  ==============================
``run <SFP-\\d+>``            RUN_TICKET     ``ticket`` (normalized upper)
``status [<SFP-\\d+>]``       STATUS         ``ticket`` or ``None`` (= all)
``approve <\\d+>``            APPROVE_PR     ``pr_number``
``stop <SFP-\\d+>``           STOP_RUN       ``ticket`` (normalized upper)
``help``                      HELP           —
============================  =============  ==============================

Determinism (MAS §12.7): :meth:`OperationalCommandInterpreter.interpret` is a
pure function of its arguments — no clock, no network, no randomness, no
I/O; the same inputs always yield the same output (or ``None``).
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "OperationalCommand",
    "OperationalCommandKind",
    "OperationalCommandInterpreter",
]


class OperationalCommandKind(StrEnum):
    """The five operational commands a Slack message can request.

    A ``StrEnum`` so the member's plain name is its string value — the wire
    representation downstream consumers log/serialize is exactly ``RUN_TICKET``
    et al., with no mapping layer in between.
    """

    RUN_TICKET = "RUN_TICKET"
    STATUS = "STATUS"
    APPROVE_PR = "APPROVE_PR"
    STOP_RUN = "STOP_RUN"
    HELP = "HELP"


class OperationalCommand(BaseModel):
    """One successfully parsed operational command.

    Frozen: a parsed command is a fact about the parsed message, not a
    mutable working state — consumers branch on it, they never rewrite it.

    Attributes:
        kind: Which of the five grammar rows matched.
        ticket: Canonical upper ``SFP-<digits>`` capture, or ``None`` when
            the grammar row carries no ticket (``HELP``, ``APPROVE_PR``) or
            the optional ticket was absent (``status`` without an argument
            means "all").
        pr_number: The ``\\d+`` capture of ``approve``; ``None`` otherwise.
        reply_to: Where the reply must go — the thread ref when the message
            arrived in a thread, the channel ref otherwise.
    """

    model_config = ConfigDict(frozen=True)

    kind: OperationalCommandKind
    ticket: str | None = None
    pr_number: int | None = None
    reply_to: str


#: One leading raw Slack mention entity — the literal angle-bracket form
#: Slack puts in ``event.text``: ``<@U123ABC>`` or ``<@U123ABC|display>``
#: (uppercase-alphanumeric user id, optional ``|display-name`` suffix),
#: followed by the whitespace that separated it from the command. At most
#: ONE entity is stripped (``count=1``), and only a real entity matches: a
#: literal ``@name`` prefix does NOT satisfy this pattern and therefore
#: fails the grammar → ``None``.
_MENTION_PATTERN = re.compile(r"^<@[A-Z0-9]+(?:\|[^>]+)?>\s*")

#: The five grammar rows, anchored ``^…$`` and matched case-insensitively.
#: Exactly one may match; a ``$``-anchored pattern is the full-match gate
#: (a partial ``run`` without a ticket cannot match).
_RUN_TICKET_PATTERN = re.compile(r"^run sfp-(\d+)$", re.IGNORECASE)
_STATUS_PATTERN = re.compile(r"^status(?: sfp-(\d+))?$", re.IGNORECASE)
_APPROVE_PR_PATTERN = re.compile(r"^approve (\d+)$", re.IGNORECASE)
_STOP_RUN_PATTERN = re.compile(r"^stop sfp-(\d+)$", re.IGNORECASE)
_HELP_PATTERN = re.compile(r"^help$", re.IGNORECASE)


def _normalize_ticket(digits: str) -> str:
    """Canonicalize a ticket capture to upper ``SFP-<digits>``."""
    return f"SFP-{digits.upper()}"


class OperationalCommandInterpreter:
    """Interprets raw Slack text into an :class:`OperationalCommand` or ``None``.

    Stateless and side-effect free: constructing or calling it touches no
    Slack client, no bus, no filesystem. The caller supplies the routing
    refs (channel, and thread when the message is a thread reply) —
    ``reply_to`` is derived deterministically from them.
    """

    def interpret(
        self,
        text: str,
        channel_ref: str,
        thread_ref: str | None = None,
    ) -> OperationalCommand | None:
        """Parse ``text`` into a command; ``None`` for anything unmatched.

        Never raises on unrecognized input — a non-command message is the
        normal case, not an error condition.

        Args:
            text: The raw ``event.text`` as published by SFP-132 (mention
                entities in their literal ``<@…>`` form).
            channel_ref: The channel to reply to when no thread is present.
            thread_ref: The thread ref when the message is a thread reply.

        Returns:
            The parsed command, or ``None`` when the text does not match
            the grammar.
        """
        stripped = _MENTION_PATTERN.sub("", text, count=1)

        if match := _RUN_TICKET_PATTERN.match(stripped):
            return OperationalCommand(
                kind=OperationalCommandKind.RUN_TICKET,
                ticket=_normalize_ticket(match.group(1)),
                reply_to=self._reply_to(channel_ref, thread_ref),
            )

        if match := _STATUS_PATTERN.match(stripped):
            captured = match.group(1)
            return OperationalCommand(
                kind=OperationalCommandKind.STATUS,
                ticket=_normalize_ticket(captured) if captured is not None else None,
                reply_to=self._reply_to(channel_ref, thread_ref),
            )

        if match := _APPROVE_PR_PATTERN.match(stripped):
            return OperationalCommand(
                kind=OperationalCommandKind.APPROVE_PR,
                pr_number=int(match.group(1)),
                reply_to=self._reply_to(channel_ref, thread_ref),
            )

        if match := _STOP_RUN_PATTERN.match(stripped):
            return OperationalCommand(
                kind=OperationalCommandKind.STOP_RUN,
                ticket=_normalize_ticket(match.group(1)),
                reply_to=self._reply_to(channel_ref, thread_ref),
            )

        if _HELP_PATTERN.match(stripped):
            return OperationalCommand(
                kind=OperationalCommandKind.HELP,
                reply_to=self._reply_to(channel_ref, thread_ref),
            )

        return None

    @staticmethod
    def _reply_to(channel_ref: str, thread_ref: str | None) -> str:
        """The thread ref when present, the channel ref otherwise."""
        return thread_ref if thread_ref is not None else channel_ref
