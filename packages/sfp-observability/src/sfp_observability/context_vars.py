"""Correlation/causation contextvar binding for SFP logging (SFP-48 / ID-031).

Provides a deterministic, structlog-idiomatic mechanism for binding and clearing
``correlation_id`` and ``causation_id`` onto the structlog contextvar context,
so that any log event emitted after :func:`bind_context` carries both IDs as JSON
fields via the already-installed ``merge_contextvars`` processor (ID-050).

SFP-47 wired :func:`structlog.contextvars.merge_contextvars` as the **first**
processor in the shared chain (``logging.py``), so the contextvars bound here are
already merged into every emitted event dict and rendered as JSON keys. This
module therefore only manages the contextual state; it never touches the
processor chain.

Design note (R1 / ID-031): the bind helpers call
:func:`structlog.contextvars.bind_contextvars` and
:func:`structlog.contextvars.clear_contextvars` exclusively — they do **not** use
a raw ``contextvars.ContextVar.set``. ``merge_contextvars`` only merges values
bound through the structlog API; a raw ``ContextVar`` would be silently ignored
and the IDs would never reach JSON output.

The key-name constants are exported so callers and tests can reference the exact
rendered JSON keys without magic strings (ID-031).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import structlog

#: The JSON key under which ``correlation_id`` is rendered (ID-031).
CORRELATION_ID_KEY: str = "correlation_id"
#: The JSON key under which ``causation_id`` is rendered (ID-031).
CAUSATION_ID_KEY: str = "causation_id"


def bind_context(correlation_id: str | None, causation_id: str | None) -> None:
    """Bind ``correlation_id`` and ``causation_id`` onto the structlog context.

    Both keys are bound unconditionally so they appear in every event emitted
    after this call (until :func:`clear_context` runs). A ``None`` value is bound
    as-is and renders as JSON ``null`` (ID-031 does not pin None-handling, so
    binding None rather than skipping the key is acceptable).

    Args:
        correlation_id: The correlation identifier for the current causal chain,
            or ``None``.
        causation_id: The identifier of the event that caused the current one,
            or ``None``.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def clear_context() -> None:
    """Clear all structlog-bound contextvars.

    Removes every key bound by :func:`bind_context` (including a prior
    ``correlation_id`` / ``causation_id``), restoring the context to a pristine
    state. Subsequent emitted events will carry neither ID unless rebound.
    """
    structlog.contextvars.clear_contextvars()


@contextlib.contextmanager
def bound_context(correlation_id: str | None, causation_id: str | None) -> Iterator[None]:
    """Bind both IDs for the duration of a ``with`` block, then clear.

    Binds on enter and clears on exit via :func:`clear_context`, including when
    the block exits by raising. Exceptions are **not** suppressed — they
    propagate after the context has been cleared.

    Args:
        correlation_id: The correlation identifier for the current causal chain,
            or ``None``.
        causation_id: The identifier of the event that caused the current one,
            or ``None``.

    Yields:
        ``None``. The context is bound for the lifetime of the ``with`` block.
    """
    bind_context(correlation_id, causation_id)
    try:
        yield
    finally:
        clear_context()


__all__: list[str] = [
    "CORRELATION_ID_KEY",
    "CAUSATION_ID_KEY",
    "bind_context",
    "bound_context",
    "clear_context",
]
