"""Tests for the outbound-leg notification handler (SFP-136, MAS §9.4).

Covers the PRSpec acceptance criteria deterministically (MAS §12.7):

- **NotifyUser** — destination resolved via the injected
  ``SlackDestinationResolver`` Protocol, message delivered verbatim through
  the injected ``OutboundMessagePort``, typed ``NotificationDelivered``
  carrying the ``DeliveryReceipt``.
- **RequestUserInput** — the interaction is find-or-created FIRST via
  ``InteractionService.create`` (``origin="outbound"``,
  ``response_required=True``, ``question = command.prompt``,
  ``provider_reference = command.session_id``), THEN the prompt delivered;
  the idempotent repeat (same session twice → exactly one interaction) is
  asserted against in-memory persistence.
- **Typed failures** — a raised ``ProviderError`` and a non-ok
  ``DeliveryReceipt`` both produce ``NotificationFailed`` carrying the
  error, with the interaction row intact (persisted-by-design, no
  corruption).
- **Module-import guards** — the module imports NO identity-service symbol,
  NO concrete resolver, NO scheduler/queue/admission symbol, and NO
  ``AgentRuntime``; it performs NO wall-clock read (no ``datetime.now`` /
  ``utcnow`` call).
- **≥90% coverage** of ``communication/application/notifications.py``.

Determinism: fixed clock literal, in-memory SQLite (StaticPool), fakes only
— no network, no sleep, no wall clock.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from communication.application.interaction_service import (
    InteractionService,
    SessionFactory,
    session_scope,
)
from communication.application.notifications import (
    NotificationDelivered,
    NotificationFailed,
    NotificationService,
    SlackDestination,
    SlackDestinationResolver,
)
from communication.infrastructure.persistence import Base, UserInteraction
from communication.interfaces.outbound import DeliveryReceipt, ProviderError
from sfp_contracts.commands import NotifyUser, RequestUserInput
from sfp_testing import FakeBus
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# --- Deterministic fixtures ---------------------------------------------------

#: The pinned start of the InteractionService clock (MAS §12.7 — a literal).
T0 = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

SESSION_ID = "C123/thread.456.789"
CHANNEL_REF = "C123"
THREAD_REF = "456.789"


def ok_receipt(**overrides: object) -> DeliveryReceipt:
    """A minimal ok ``DeliveryReceipt`` (deterministic, fixed values)."""
    fields: dict[str, object] = {
        "provider_message_id": "1700000000.000001",
        "channel_ref": CHANNEL_REF,
        "thread_ref": THREAD_REF,
        "provider_reference": f"slack://channel/{CHANNEL_REF}/thread/{THREAD_REF}",
        "ok": True,
        "error": None,
    }
    fields.update(overrides)
    return DeliveryReceipt(**fields)  # type: ignore[arg-type]


class FakeResolver:
    """A fake ``SlackDestinationResolver`` — one pinned destination."""

    def __init__(self, destination: SlackDestination | None = None) -> None:
        self.destination = destination or SlackDestination(
            channel_ref=CHANNEL_REF,
            thread_ref=THREAD_REF,
        )
        self.resolved: list[str] = []

    def resolve(self, session_id: str) -> SlackDestination:
        self.resolved.append(session_id)
        return self.destination


class FakeOutbound:
    """A fake ``OutboundMessagePort`` with a configurable outcome mode.

    ``mode`` selects what the next send produces: ``"ok"`` (an ok receipt),
    ``"provider_error"`` (raises :class:`ProviderError`), or
    ``"non_ok"`` (an HTTP-200-style non-ok receipt, never raising).
    """

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.sent: list[tuple[str, str | None, str | None]] = []

    def send_message(
        self,
        text: str,
        *,
        channel_ref: str | None = None,
        thread_ref: str | None = None,
    ) -> DeliveryReceipt:
        self.sent.append((text, channel_ref, thread_ref))
        if self.mode == "provider_error":
            raise ProviderError("connection reset — transport failure")
        if self.mode == "non_ok":
            return ok_receipt(ok=False, provider_message_id="", error="channel_not_found")
        return ok_receipt()


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    """In-memory SQLite with the ``business`` schema attached (StaticPool)."""
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.connect() as conn:
        conn.execute(sa.text("ATTACH DATABASE ':memory:' AS business"))
        conn.commit()
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: sa.Engine) -> SessionFactory:
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    return lambda: session_scope(maker)


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def interactions(bus: FakeBus, session_factory: SessionFactory) -> InteractionService:
    """The real InteractionService over in-memory persistence (fixed clock)."""
    return InteractionService(bus=bus, session_factory=session_factory, clock=lambda: T0)


@pytest.fixture
def outbound() -> FakeOutbound:
    return FakeOutbound()


@pytest.fixture
def resolver() -> FakeResolver:
    return FakeResolver()


@pytest.fixture
def service(
    interactions: InteractionService,
    outbound: FakeOutbound,
    resolver: FakeResolver,
) -> NotificationService:
    return NotificationService(
        outbound=outbound,
        interactions=interactions,
        resolver=resolver,
    )


# --- Module-import guards ------------------------------------------------------

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "communication"
    / "application"
    / "notifications.py"
)


def _module_source() -> str:
    return MODULE_PATH.read_text(encoding="utf-8")


def _imported_names() -> set[str]:
    """All names any import statement in the module pulls in, transitively."""
    tree = ast.parse(_module_source())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def test_module_imports_no_identity_service_symbol() -> None:
    """The module must not touch the identity service (SFP-128 owns it)."""
    names = _imported_names()
    forbidden = [n for n in names if "identity" in n.lower()]
    assert forbidden == []


def test_module_imports_no_concrete_resolver() -> None:
    """Only the Protocol — no concrete SlackDestinationResolver import."""
    assert "SlackDestinationResolver" in _module_source()
    assert not any(
        "resolver" in n.lower() and "application.notifications" not in n for n in _imported_names()
    )


def test_module_imports_no_scheduler_queue_or_admission_symbol() -> None:
    """MAS §11.8 — Communication commands bypass the Scheduler entirely."""
    names = _imported_names()
    forbidden = [
        n for n in names if any(k in n.lower() for k in ("scheduler", "queue", "admission"))
    ]
    assert forbidden == []


def test_module_imports_no_agent_runtime() -> None:
    """No LLM dependency — ``AgentRuntime`` is never imported."""
    assert not any("agent_runtime" in n or "AgentRuntime" in n for n in _imported_names())


def test_module_performs_no_wall_clock_read() -> None:
    """MAS §12.7 — no ``datetime.now`` / ``utcnow`` anywhere in the module."""
    tree = ast.parse(_module_source())
    for node in ast.walk(tree):
        assert not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"now", "utcnow"}
        ), f"wall-clock read at line {node.lineno}"


def test_resolver_is_runtime_checkable_protocol() -> None:
    """The deferral idiom (SFP-134): the Protocol is runtime-checkable and
    a plain fake satisfies it — no concrete implementation in this module."""
    assert isinstance(FakeResolver(), SlackDestinationResolver)


# --- NotifyUser ----------------------------------------------------------------


async def test_notify_user_resolves_and_delivers_verbatim(
    service: NotificationService,
    outbound: FakeOutbound,
    resolver: FakeResolver,
) -> None:
    message = "PR #42 is ready for review."
    outcome = await service.handle_notify_user(NotifyUser(session_id=SESSION_ID, message=message))

    assert resolver.resolved == [SESSION_ID]
    assert outbound.sent == [(message, CHANNEL_REF, THREAD_REF)]
    assert isinstance(outcome, NotificationDelivered)
    assert outcome.receipt.ok is True
    assert outcome.receipt.channel_ref == CHANNEL_REF


async def test_notify_user_threading_is_optional(
    service: NotificationService,
    outbound: FakeOutbound,
) -> None:
    """A destination without a thread posts a top-level message."""
    service._resolver.destination = SlackDestination(channel_ref=CHANNEL_REF)  # noqa: SLF001
    outcome = await service.handle_notify_user(NotifyUser(session_id=SESSION_ID, message="hello"))
    assert isinstance(outcome, NotificationDelivered)
    assert outbound.sent == [("hello", CHANNEL_REF, None)]


# --- RequestUserInput ----------------------------------------------------------


async def test_request_user_input_creates_interaction_then_delivers(
    service: NotificationService,
    outbound: FakeOutbound,
    engine: sa.Engine,
) -> None:
    prompt = "Approve the merge?"
    outcome = await service.handle_request_user_input(
        RequestUserInput(session_id=SESSION_ID, prompt=prompt)
    )

    assert isinstance(outcome, NotificationDelivered)
    assert outbound.sent == [(prompt, CHANNEL_REF, THREAD_REF)]

    persisted = engine.connect().execute(sa.select(UserInteraction.__table__)).fetchall()
    assert len(persisted) == 1
    row = persisted[0]
    assert row.provider_reference == SESSION_ID  # type: ignore[union-attr]
    assert row.origin == "outbound"  # type: ignore[union-attr]
    assert row.response_required is True  # type: ignore[union-attr]
    assert row.question == prompt  # type: ignore[union-attr]
    assert row.type == "user_input_request"  # type: ignore[union-attr]


async def test_request_user_input_repeat_is_idempotent(
    service: NotificationService,
    engine: sa.Engine,
) -> None:
    """Redelivery of the same command does NOT create a second interaction."""
    command = RequestUserInput(session_id=SESSION_ID, prompt="Approve the merge?")
    await service.handle_request_user_input(command)
    await service.handle_request_user_input(command)

    persisted = engine.connect().execute(sa.select(UserInteraction.__table__)).fetchall()
    assert len(persisted) == 1


# --- Typed failure paths -------------------------------------------------------


async def test_provider_error_yields_typed_failure_and_keeps_interaction(
    service: NotificationService,
    outbound: FakeOutbound,
    engine: sa.Engine,
) -> None:
    outbound.mode = "provider_error"
    outcome = await service.handle_request_user_input(
        RequestUserInput(session_id=SESSION_ID, prompt="Approve?")
    )

    assert isinstance(outcome, NotificationFailed)
    assert outcome.transport_error == "connection reset — transport failure"
    assert outcome.error is None

    # The interaction row persists — the thread the reply lands on; NOT
    # corruption, find-or-create makes redelivery idempotent.
    persisted = engine.connect().execute(sa.select(UserInteraction.__table__)).fetchall()
    assert len(persisted) == 1
    assert persisted[0].provider_reference == SESSION_ID  # type: ignore[union-attr]


async def test_non_ok_receipt_yields_typed_failure(
    service: NotificationService,
    outbound: FakeOutbound,
) -> None:
    outbound.mode = "non_ok"
    outcome = await service.handle_notify_user(NotifyUser(session_id=SESSION_ID, message="ping"))

    assert isinstance(outcome, NotificationFailed)
    assert outcome.error == "channel_not_found"
    assert outcome.transport_error is None


async def test_provider_error_on_notify_carries_no_interaction_side_effect(
    service: NotificationService,
    outbound: FakeOutbound,
    engine: sa.Engine,
) -> None:
    """NotifyUser never creates an interaction; a failure creates none either."""
    outbound.mode = "provider_error"
    outcome = await service.handle_notify_user(NotifyUser(session_id=SESSION_ID, message="ping"))
    assert isinstance(outcome, NotificationFailed)
    persisted = engine.connect().execute(sa.select(UserInteraction.__table__)).fetchall()
    assert persisted == []
