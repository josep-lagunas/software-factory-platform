"""Compose a system prompt from Markdown fragments on disk.

This module delivers the concrete :class:`PromptBuilder` for the agent runtime
(SFP-52). A builder loads Markdown fragments from an injectable ``base_dir`` and
composes them deterministically into a single system-prompt string, in the order
*shared base -> role -> task*. ``PromptBuilder`` is a structural implementation
of the :class:`sfp_agent_runtime.interfaces.PromptProvider` Protocol defined by
SFP-51: it satisfies the Protocol by duck typing (it has the matching
``get_prompt`` signature) and is deliberately *not* coupled to that interface by
import — this module imports nothing from the interfaces package, so the seam
stays one-directional.

Grounded in:
- SFP-52 (Jira) — this implementation ticket.
- ID-059 — all prompt text comes from fragment files on disk; no prompt string
  is ever inlined in source. The only string literals here are filenames and
  the inter-fragment separator, which are not prompt content.
- SFP-51 (Jira) — defines the ``PromptProvider`` Protocol this class implements.
- MAS §9.6 — the agent runtime is the vendor-neutral seam; prompt resolution
  here imports and names no vendor SDK.

Design choices:
- ``base_dir`` is wrapped in :class:`pathlib.Path` at construction so a ``str``
  or :class:`~pathlib.Path` may be passed. No IO happens in ``__init__``;
  fragments are read lazily on each :meth:`PromptBuilder.get_prompt` call.
- Reads are synchronous stdlib :mod:`pathlib` only — no caching, no templating,
  no async IO (out of scope for SFP-52; fragments are concatenated verbatim).
- All reads use ``encoding="utf-8"`` so fragment loading is deterministic across
  platforms.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["PromptBuilder"]

# Fragment filenames (ID-059: these are filenames, not prompt strings).
_SHARED_FILENAME = "shared.md"
_BASE_FILENAME = "base.md"
_FILE_SUFFIX = ".md"
# Deterministic separator between adjacent fragments (not prompt content).
_FRAGMENT_SEPARATOR = "\n\n"


class PromptBuilder:
    """Build a system prompt by composing Markdown fragments from ``base_dir``.

    Fragments are resolved under ``base_dir`` and composed in this order:

    1. **Shared base** — ``shared.md`` is *preferred*; if absent, ``base.md`` is
       the *fallback*. Exactly one is used; they are never concatenated together
       (``shared.md`` wins when both exist).
    2. **Role** — ``<agent>.md``.
    3. **Task** — ``<agent>/<task>.md``.

    Every fragment is optional: a missing one is skipped gracefully. If none of
    the three resolve to an existing file, :meth:`get_prompt` raises
    :class:`FileNotFoundError` naming ``base_dir``, ``agent``, and ``task``.

    Each fragment has its leading/trailing whitespace stripped, adjacent
    fragments are joined with exactly one blank line (``"\\n\\n"``), and a
    single trailing ``"\\n"`` is appended.

    This class is a structural implementation of
    :class:`sfp_agent_runtime.interfaces.PromptProvider` (SFP-51): it is not
    imported from or registered against that Protocol here, but an
    ``isinstance(builder, PromptProvider)`` check succeeds because the Protocol
    is ``runtime_checkable`` and ``get_prompt`` matches its signature.

    Args:
        base_dir: Directory holding the Markdown fragments. A ``str`` or
            :class:`~pathlib.Path`; wrapped in ``Path()`` on construction. No
            IO is performed in ``__init__``.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    def _read_optional(self, path: Path) -> str | None:
        """Return the UTF-8 text of ``path`` if it is a file, else ``None``.

        Centralizes the missing-optional-fragment policy: every optional
        fragment read funnels through here so a missing file is a silent
        ``None`` rather than an exception.
        """
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return None

    def _shared_fragment(self) -> str | None:
        """Resolve the shared base fragment, preferring ``shared.md``.

        ``shared.md`` is preferred; ``base.md`` is the fallback. Only one is
        ever used — they are never concatenated. Returns ``None`` when neither
        exists.
        """
        shared = self._read_optional(self._base_dir / _SHARED_FILENAME)
        if shared is not None:
            return shared
        return self._read_optional(self._base_dir / _BASE_FILENAME)

    def get_prompt(self, agent: str, task: str) -> str:
        """Compose and return the system prompt for ``agent`` performing ``task``.

        Fragments are collected in order shared-base -> role -> task, each
        optional. Each fragment is stripped of surrounding whitespace, adjacent
        fragments are joined with exactly one blank line, and a single trailing
        newline is appended.

        Args:
            agent: The agent role (e.g. ``"planner"``); selects ``<agent>.md``
                and the ``<agent>/`` task directory.
            task: The task name; selects ``<agent>/<task>.md``.

        Returns:
            The deterministically composed prompt text.

        Raises:
            FileNotFoundError: If none of the shared-base, role, or task
                fragments resolve to an existing file. The message names
                ``base_dir``, ``agent``, and ``task``.
        """
        fragments: list[str] = []

        shared = self._shared_fragment()
        if shared is not None:
            fragments.append(shared)

        role = self._read_optional(self._base_dir / f"{agent}{_FILE_SUFFIX}")
        if role is not None:
            fragments.append(role)

        task_text = self._read_optional(self._base_dir / agent / f"{task}{_FILE_SUFFIX}")
        if task_text is not None:
            fragments.append(task_text)

        if not fragments:
            raise FileNotFoundError(
                f"no prompt fragments found for agent={agent!r}, task={task!r} "
                f"under base_dir={str(self._base_dir)!r}"
            )

        return _FRAGMENT_SEPARATOR.join(f.strip() for f in fragments) + "\n"
