"""The :class:`TestDesignerOutput` schema — the structured test plan (ID-066).

Grounded in:
- ID-066 — Test Designer returns a structured test plan as its deterministic
  output; every agent emits a strict JSON contract, and unknown fields are
  rejected (``extra='forbid'``).
- SFP-34 (Jira) / SFP-17 (doc) — the implementation ticket.

Design choices:
- ``extra='forbid'`` rejects unknown fields immediately, not silently.
- ``test_plan`` is a nested model whose seven fields are all ``list[str]`` —
  each entry is a *description string* (what to test), never code or code
  bodies (the "judgments + references, not artifacts" rule of ID-066).
- ``required_validation_commands`` entries are shell-command strings (e.g.
  ``uv run pytest -q``) the gates run; they live alongside the plan so the
  Test Designer owns the exact validation surface.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class TestPlan(BaseModel):
    """The Test Designer's structured test plan — seven ``list[str]`` buckets.

    Each field is a list of short description strings (what to test / check),
    not executable code. Every field is required; a bucket with no entries is
    conveyed as an empty list (``min_length`` is a workflow policy, not a
    schema concern — matching :class:`~sfp_contracts.agents.planner.PrSpec`).
    Unknown fields are rejected (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    # pytest must not collect this ``Test*``-named model as a test class.
    __test__: ClassVar[bool] = False

    unit_tests: list[str]
    integration_tests: list[str]
    e2e_or_smoke_tests: list[str]
    negative_tests: list[str]
    edge_cases: list[str]
    regression_risks: list[str]
    required_validation_commands: list[str]


class TestDesignerOutput(BaseModel):
    """The Test Designer's output schema (the test plan for one PR-spec).

    Fields:
        pr_spec_id: The PR-spec the test plan targets.
        test_plan: Seven ``list[str]`` buckets of description strings, plus the
            shell commands the gates must run.

    Constraints (ID-066):
        - No executable code bodies — only descriptions and command strings.
        - Unknown fields are rejected (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    # pytest must not collect this ``Test*``-named model as a test class.
    __test__: ClassVar[bool] = False

    pr_spec_id: str
    test_plan: TestPlan

    def to_json(self) -> str:
        """Serialize to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "TestDesignerOutput":
        """Deserialize from a JSON string or bytes."""
        return cls.model_validate_json(data)
