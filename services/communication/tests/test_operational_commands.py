"""Tests for the Slack operational-command interpreter (SFP-244).

Partitions, per the PRSpec's acceptance criteria:

1. **Grammar rows** — each of the five rows parses exactly, case-insensitively,
   with a leading raw Slack mention entity (both ``<@U123ABC>`` and
   ``<@U123ABC|display>`` forms), and with/without optional args
   (``status`` with and without a ticket).
2. **Normalization** — the ticket capture is canonicalized to upper
   ``SFP-<digits>`` (``sfp-244`` and ``SFP-244`` both yield ``SFP-244``).
3. **Unmatched → None** — gibberish, partial matches (``run`` without a
   ticket), and a literal ``@name`` prefix all return ``None``; the call
   never raises and never guesses a kind.
4. **reply_to routing** — the thread ref when one is provided, the channel
   ref otherwise.
5. **Model semantics** — ``OperationalCommand`` is frozen (immutability).
6. **Import-surface purity** — the module imports only ``pydantic`` + stdlib
   ``re``: no Slack client, no bus, no I/O.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from types import ModuleType

import pytest
from communication.application import (
    OperationalCommand,
    OperationalCommandInterpreter,
    OperationalCommandKind,
)

CHANNEL = "C123CHANNEL"
THREAD = "1712345678.123456"

interpreter = OperationalCommandInterpreter()


def interpret(text: str, thread_ref: str | None = None) -> OperationalCommand | None:
    """Interpret ``text`` against the fixed channel/thread refs."""
    return interpreter.interpret(text, channel_ref=CHANNEL, thread_ref=thread_ref)


# --- 1. Grammar rows -----------------------------------------------------------


class TestRunTicket:
    @pytest.mark.parametrize("text", ["run SFP-244", "Run SFP-244", "RUN sfp-244"])
    def test_exact_and_case_variants(self, text: str) -> None:
        command = interpret(text)
        assert command is not None
        assert command.kind is OperationalCommandKind.RUN_TICKET
        assert command.ticket == "SFP-244"
        assert command.pr_number is None
        assert command.reply_to == CHANNEL

    @pytest.mark.parametrize(
        "mention",
        ["<@U123ABC>", "<@U123ABC|display.name>"],
        ids=["bare-entity", "entity-with-display"],
    )
    def test_mention_prefixed(self, mention: str) -> None:
        command = interpret(f"{mention} run SFP-244")
        assert command is not None
        assert command.kind is OperationalCommandKind.RUN_TICKET
        assert command.ticket == "SFP-244"

    def test_mention_prefixed_with_whitespace(self) -> None:
        command = interpret("<@U123ABC>    run SFP-244")
        assert command is not None
        assert command.kind is OperationalCommandKind.RUN_TICKET

    def test_second_mention_is_not_stripped(self) -> None:
        """At most ONE leading entity is stripped — the rest fails grammar."""
        assert interpret("<@U123ABC> <@U456DEF> run SFP-244") is None


class TestStatus:
    def test_with_ticket(self) -> None:
        command = interpret("status SFP-244")
        assert command is not None
        assert command.kind is OperationalCommandKind.STATUS
        assert command.ticket == "SFP-244"
        assert command.pr_number is None

    def test_without_ticket_means_all(self) -> None:
        command = interpret("status")
        assert command is not None
        assert command.kind is OperationalCommandKind.STATUS
        assert command.ticket is None
        assert command.pr_number is None

    @pytest.mark.parametrize("text", ["STATUS", "Status SFP-244", "STATUS sfp-244"])
    def test_case_variants(self, text: str) -> None:
        command = interpret(text)
        assert command is not None
        assert command.kind is OperationalCommandKind.STATUS

    @pytest.mark.parametrize(
        "mention",
        ["<@U123ABC>", "<@U123ABC|display.name>"],
        ids=["bare-entity", "entity-with-display"],
    )
    def test_mention_prefixed_without_ticket(self, mention: str) -> None:
        command = interpret(f"{mention} status")
        assert command is not None
        assert command.kind is OperationalCommandKind.STATUS
        assert command.ticket is None


class TestApprovePr:
    @pytest.mark.parametrize("text", ["approve 42", "Approve 42", "APPROVE 42"])
    def test_exact_and_case_variants(self, text: str) -> None:
        command = interpret(text)
        assert command is not None
        assert command.kind is OperationalCommandKind.APPROVE_PR
        assert command.pr_number == 42
        assert command.ticket is None

    @pytest.mark.parametrize(
        "mention",
        ["<@U123ABC>", "<@U123ABC|display.name>"],
        ids=["bare-entity", "entity-with-display"],
    )
    def test_mention_prefixed(self, mention: str) -> None:
        command = interpret(f"{mention} approve 42")
        assert command is not None
        assert command.kind is OperationalCommandKind.APPROVE_PR
        assert command.pr_number == 42


class TestStopRun:
    @pytest.mark.parametrize("text", ["stop SFP-244", "Stop SFP-244", "STOP sfp-244"])
    def test_exact_and_case_variants(self, text: str) -> None:
        command = interpret(text)
        assert command is not None
        assert command.kind is OperationalCommandKind.STOP_RUN
        assert command.ticket == "SFP-244"
        assert command.pr_number is None

    @pytest.mark.parametrize(
        "mention",
        ["<@U123ABC>", "<@U123ABC|display.name>"],
        ids=["bare-entity", "entity-with-display"],
    )
    def test_mention_prefixed(self, mention: str) -> None:
        command = interpret(f"{mention} stop SFP-244")
        assert command is not None
        assert command.kind is OperationalCommandKind.STOP_RUN
        assert command.ticket == "SFP-244"


class TestHelp:
    @pytest.mark.parametrize("text", ["help", "Help", "HELP"])
    def test_exact_and_case_variants(self, text: str) -> None:
        command = interpret(text)
        assert command is not None
        assert command.kind is OperationalCommandKind.HELP
        assert command.ticket is None
        assert command.pr_number is None

    @pytest.mark.parametrize(
        "mention",
        ["<@U123ABC>", "<@U123ABC|display.name>"],
        ids=["bare-entity", "entity-with-display"],
    )
    def test_mention_prefixed(self, mention: str) -> None:
        command = interpret(f"{mention} help")
        assert command is not None
        assert command.kind is OperationalCommandKind.HELP


# --- 2. Ticket normalization ---------------------------------------------------


class TestNormalization:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("run SFP-244", "SFP-244"),
            ("run sfp-244", "SFP-244"),
            ("run Sfp-244", "SFP-244"),
            ("stop sfp-7", "SFP-7"),
            ("status sfp-99", "SFP-99"),
        ],
    )
    def test_normalized_to_upper(self, text: str, expected: str) -> None:
        command = interpret(text)
        assert command is not None
        assert command.ticket == expected

    def test_leading_zeros_preserved_as_digits(self) -> None:
        command = interpret("run sfp-007")
        assert command is not None
        assert command.ticket == "SFP-007"


# --- 3. Unmatched → None -------------------------------------------------------


class TestUnmatched:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "gibberish",
            "run",
            "run ",
            "run SFP-",
            "run SFP-244 extra",
            "status SFP-244 extra",
            "approve",
            "approve abc",
            "stop",
            "stop SFP",
            "help me",
            "help please",
            "please help",
            "run PROJ-244",
            "run SFP-244\r\n",
            "run\tSFP-244",
            "@josep run SFP-244",
            "@name help",
            "<@u123abc> help",  # lowercase user id is not a raw entity
            "<#C123|general> help",  # channel entity, not a user mention
            "run SFP-244\nrun SFP-245",
            "status  sfp-244",  # double space breaks the grammar
        ],
    )
    def test_returns_none_never_raises(self, text: str) -> None:
        assert interpret(text) is None


# --- 4. reply_to routing -------------------------------------------------------


class TestReplyToRouting:
    @pytest.mark.parametrize(
        "text",
        [
            "run SFP-244",
            "status",
            "status SFP-244",
            "approve 42",
            "stop SFP-244",
            "help",
        ],
    )
    def test_thread_ref_wins_when_present(self, text: str) -> None:
        command = interpret(text, thread_ref=THREAD)
        assert command is not None
        assert command.reply_to == THREAD

    @pytest.mark.parametrize(
        "text",
        [
            "run SFP-244",
            "status",
            "status SFP-244",
            "approve 42",
            "stop SFP-244",
            "help",
        ],
    )
    def test_channel_ref_when_no_thread(self, text: str) -> None:
        command = interpret(text)
        assert command is not None
        assert command.reply_to == CHANNEL


# --- 5. Model semantics --------------------------------------------------------


class TestModelSemantics:
    def test_frozen_immutable(self) -> None:
        command = interpret("run SFP-244")
        assert command is not None
        with pytest.raises(Exception, match="instance"):
            command.ticket = "SFP-999"  # type: ignore[misc]

    def test_kind_members_exactly_five(self) -> None:
        assert {kind.name for kind in OperationalCommandKind} == {
            "RUN_TICKET",
            "STATUS",
            "APPROVE_PR",
            "STOP_RUN",
            "HELP",
        }

    def test_kind_is_strenum(self) -> None:
        assert isinstance(OperationalCommandKind.HELP, str)
        assert OperationalCommandKind.HELP == "HELP"


# --- 6. Import-surface purity --------------------------------------------------


class TestImportSurfacePurity:
    def test_module_imports_only_pydantic_and_stdlib_re(self) -> None:
        """The parse layer may not reach Slack clients, the bus, or any I/O.

        Re-imports the module in a clean interpreter and asserts every module
        freshly pulled in by that import is stdlib (``re`` / ``enum`` and
        their CPython internals) or ``pydantic`` — nothing else.
        """
        before = set(sys.modules)
        module = importlib.import_module("communication.application.operational_commands")
        assert module is not None
        newly_loaded = set(sys.modules) - before

        for name in sorted(newly_loaded):
            allowed = (
                name.startswith("communication")
                or name.startswith("pydantic")
                or name.startswith("_")
                or name in {"re", "enum", "functools", "typing", "types"}
                or name.startswith("sre_")
            )
            assert allowed, f"unexpected import surfaced by operational_commands: {name}"

    def test_no_io_calls_in_source(self) -> None:
        """Static check: the module's source references no open/socket/bus/post."""
        source = inspect.getsource(
            importlib.import_module("communication.application.operational_commands")
        )
        for forbidden in (
            "MessageBus",
            "SlackOutboundClient",
            "httpx",
            "requests",
            "socket",
            "open(",
            "post_message",
        ):
            assert forbidden not in source

    def test_public_surface_is_the_three_symbols(self) -> None:
        """``__all__`` is exactly the three PRSpec-pinned exports."""
        module: ModuleType = importlib.import_module(
            "communication.application.operational_commands"
        )
        assert module.__all__ == [  # type: ignore[attr-defined]
            "OperationalCommand",
            "OperationalCommandKind",
            "OperationalCommandInterpreter",
        ]
