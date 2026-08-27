"""Shared purity-scanning helpers for the policy tests (SFP-143).

The acceptance criteria require the policies to never touch the bus, never
execute work, and never read the clock or any source of randomness. A raw
substring scan of module source false-positives on *prose* — the docstrings
legitimately say "no randomness" — so these helpers walk the **AST** instead,
which skips comments and docstrings by construction and inspects only names
the code actually references.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Iterable

#: Names no policy module may reference in code (docstrings are exempt).
BANNED_REFERENCES: tuple[str, ...] = (
    "sfp_messaging",
    "MessageBus",
    "publish",
    "socket",
    "random",
    "datetime",
    "subprocess",
    "merge_pull_request",
)


def code_referenced_names(module_name: str) -> set[str]:
    """Every identifier and attribute *code* references in ``module_name``.

    Comments and docstrings are excluded (they are not AST nodes), so
    "randomness" in prose can never trip the scan.
    """
    module = importlib.import_module(module_name)
    path = module.__file__
    assert path is not None, f"{module_name} has no source file"
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {alias.asname or alias.name for alias in node.names}
    return names


def assert_module_references_none_of(
    module_name: str,
    banned: Iterable[str] = BANNED_REFERENCES,
) -> None:
    """Assert no banned name appears in ``module_name``'s code (not prose)."""
    referenced = code_referenced_names(module_name)
    for name in banned:
        assert name not in referenced, f"{module_name} must not reference {name}"
