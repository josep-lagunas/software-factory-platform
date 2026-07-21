"""Tests for :class:`sfp_agent_runtime.prompt_builder.PromptBuilder` (SFP-52).

Covers the acceptance criteria:
- (1) ``PromptBuilder`` satisfies the ``PromptProvider`` Protocol structurally
      (``isinstance`` against the runtime_checkable Protocol from SFP-51);
- (2) ``get_prompt`` composes shared-base -> role -> task fragments in order
      with exact deterministic whitespace;
- (3) ``shared.md`` is preferred over ``base.md`` (precedence hard negative),
      and ``base.md`` is the fallback when ``shared.md`` is absent;
- (4) missing optional fragments are skipped gracefully (single-fragment cases);
- (5) when NO fragments resolve, ``FileNotFoundError`` is raised naming agent,
      task, and base_dir;
- (6) no prompt text is inlined in the builder source (ID-059);
- (7) UTF-8 non-ASCII fragment content is preserved, and ``str``/``Path``
      ``base_dir`` are both accepted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sfp_agent_runtime.prompt_builder as _pb_module
from sfp_agent_runtime.interfaces import PromptProvider
from sfp_agent_runtime.prompt_builder import PromptBuilder

_AGENT = "planner"
_TASK = "plan"

# Source path to the builder module, for the ID-059 no-inlined-prompts guard.
_BUILDER_PATH = Path(_pb_module.__file__)

# Sentinel prompt phrases that must NEVER appear in the builder source (ID-059).
_INLINED_PROMPT_SENTINELS = ("You are a", "As an AI", "You are an AI assistant")


def _write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` (UTF-8), creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- (1) Protocol isinstance ------------------------------------------------


def test_protocol_isinstance(tmp_path: Path) -> None:
    """(1) PromptBuilder satisfies PromptProvider (runtime_checkable Protocol)."""
    pb = PromptBuilder(tmp_path)
    assert isinstance(pb, PromptProvider)


def test_accepts_str_base_dir(tmp_path: Path) -> None:
    """(7) A str base_dir is accepted (wrapped in Path on construction)."""
    _write(tmp_path / "shared.md", "SHARED")
    pb = PromptBuilder(str(tmp_path))
    assert isinstance(pb, PromptProvider)
    assert "SHARED" in pb.get_prompt(_AGENT, _TASK)


def test_accepts_path_base_dir(tmp_path: Path) -> None:
    """(7) A Path base_dir is accepted."""
    _write(tmp_path / "shared.md", "SHARED")
    pb = PromptBuilder(tmp_path)
    assert "SHARED" in pb.get_prompt(_AGENT, _TASK)


# --- (2) composition order + deterministic whitespace ----------------------


def test_composes_shared_role_task_in_order(tmp_path: Path) -> None:
    """(2) Fragments compose shared->role->task with exact whitespace."""
    _write(tmp_path / "shared.md", "SHARED")
    _write(tmp_path / f"{_AGENT}.md", "ROLE")
    _write(tmp_path / _AGENT / f"{_TASK}.md", "TASK")
    pb = PromptBuilder(tmp_path)
    assert pb.get_prompt(_AGENT, _TASK) == "SHARED\n\nROLE\n\nTASK\n"


def test_deterministic_whitespace_repeated(tmp_path: Path) -> None:
    """(2/7) Two reads of the same fragments are byte-identical."""
    _write(tmp_path / "shared.md", "SHARED")
    _write(tmp_path / f"{_AGENT}.md", "ROLE")
    _write(tmp_path / _AGENT / f"{_TASK}.md", "TASK")
    pb = PromptBuilder(tmp_path)
    first = pb.get_prompt(_AGENT, _TASK)
    second = pb.get_prompt(_AGENT, _TASK)
    assert first == second == "SHARED\n\nROLE\n\nTASK\n"


def test_strips_surrounding_whitespace(tmp_path: Path) -> None:
    """(2) Each fragment has its surrounding whitespace stripped before join."""
    _write(tmp_path / "shared.md", "\n\n  SHARED  \n\n")
    _write(tmp_path / f"{_AGENT}.md", "\tROLE\t")
    pb = PromptBuilder(tmp_path)
    assert pb.get_prompt(_AGENT, _TASK) == "SHARED\n\nROLE\n"


def test_single_trailing_newline_only(tmp_path: Path) -> None:
    """(2) The output ends with exactly one trailing newline, no extra blanks."""
    _write(tmp_path / "shared.md", "SHARED")
    pb = PromptBuilder(tmp_path)
    result = pb.get_prompt(_AGENT, _TASK)
    assert result.endswith("\n")
    assert not result.endswith("\n\n")


# --- (3) shared.md preferred over base.md / base.md fallback ----------------


def test_shared_md_preferred_over_base_md(tmp_path: Path) -> None:
    """(3) shared.md wins when both shared.md and base.md exist (hard negative)."""
    _write(tmp_path / "shared.md", "SHARED")
    _write(tmp_path / "base.md", "BASE")
    _write(tmp_path / f"{_AGENT}.md", "ROLE")
    pb = PromptBuilder(tmp_path)
    result = pb.get_prompt(_AGENT, _TASK)
    assert "SHARED" in result
    # Hard negative: base.md content must NOT leak when shared.md is present.
    assert "BASE" not in result


def test_base_md_fallback_when_no_shared(tmp_path: Path) -> None:
    """(3) base.md is used as the fallback when shared.md is absent."""
    _write(tmp_path / "base.md", "BASE")
    _write(tmp_path / f"{_AGENT}.md", "ROLE")
    pb = PromptBuilder(tmp_path)
    result = pb.get_prompt(_AGENT, _TASK)
    assert "BASE" in result
    assert "shared.md" not in result  # shared.md simply absent


# --- (4) single-fragment cases (missing optional skipped gracefully) -------


def test_shared_only(tmp_path: Path) -> None:
    """(4) Only the shared base present -> just the shared content."""
    _write(tmp_path / "shared.md", "SHARED")
    pb = PromptBuilder(tmp_path)
    assert pb.get_prompt(_AGENT, _TASK) == "SHARED\n"


def test_role_only(tmp_path: Path) -> None:
    """(4) Only the role fragment present -> just the role content."""
    _write(tmp_path / f"{_AGENT}.md", "ROLE")
    pb = PromptBuilder(tmp_path)
    assert pb.get_prompt(_AGENT, _TASK) == "ROLE\n"


def test_task_only(tmp_path: Path) -> None:
    """(4) Only the task fragment present -> just the task content."""
    _write(tmp_path / _AGENT / f"{_TASK}.md", "TASK")
    pb = PromptBuilder(tmp_path)
    assert pb.get_prompt(_AGENT, _TASK) == "TASK\n"


def test_shared_and_task_without_role(tmp_path: Path) -> None:
    """(4) Shared + task (no role) compose in order with the role skipped."""
    _write(tmp_path / "shared.md", "SHARED")
    _write(tmp_path / _AGENT / f"{_TASK}.md", "TASK")
    pb = PromptBuilder(tmp_path)
    assert pb.get_prompt(_AGENT, _TASK) == "SHARED\n\nTASK\n"


def test_unrelated_files_are_ignored(tmp_path: Path) -> None:
    """(4) Files that don't match a fragment slot are ignored, not composed."""
    _write(tmp_path / "README.md", "README")
    _write(tmp_path / "notes.txt", "NOTES")
    _write(tmp_path / f"{_AGENT}.md", "ROLE")
    pb = PromptBuilder(tmp_path)
    result = pb.get_prompt(_AGENT, _TASK)
    assert result == "ROLE\n"
    assert "README" not in result
    assert "NOTES" not in result


# --- (5) no fragments -> FileNotFoundError ----------------------------------


def test_no_fragments_raises_file_not_found(tmp_path: Path) -> None:
    """(5) An empty base_dir raises FileNotFoundError."""
    pb = PromptBuilder(tmp_path)
    with pytest.raises(FileNotFoundError):
        pb.get_prompt(_AGENT, _TASK)


def test_no_fragments_error_names_agent_task_base_dir(tmp_path: Path) -> None:
    """(5) The error message names agent, task, and base_dir."""
    pb = PromptBuilder(tmp_path)
    with pytest.raises(FileNotFoundError) as exc_info:
        pb.get_prompt(_AGENT, _TASK)
    message = str(exc_info.value)
    assert _AGENT in message
    assert _TASK in message
    assert str(tmp_path) in message


# --- (6) no inlined prompt strings (ID-059) ---------------------------------


def test_no_inlined_prompt_strings() -> None:
    """(6) The builder source contains no inlined prompt phrases (ID-059)."""
    source = _BUILDER_PATH.read_text(encoding="utf-8")
    for sentinel in _INLINED_PROMPT_SENTINELS:
        assert sentinel not in source, (
            f"inlined prompt phrase {sentinel!r} found in builder source (ID-059)"
        )


# --- (7) UTF-8 non-ASCII preserved ------------------------------------------


def test_utf8_non_ascii_preserved(tmp_path: Path) -> None:
    """(7) Non-ASCII UTF-8 fragment content is preserved verbatim."""
    shared_text = "Rôle — café — naïve résumé"
    role_text = "Plánner — ünïcode"
    _write(tmp_path / "shared.md", shared_text)
    _write(tmp_path / f"{_AGENT}.md", role_text)
    pb = PromptBuilder(tmp_path)
    result = pb.get_prompt(_AGENT, _TASK)
    assert shared_text in result
    assert role_text in result
    assert result == f"{shared_text}\n\n{role_text}\n"
