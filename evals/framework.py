"""Sweet Eval Framework — Core classes for agent evaluation.

This module provides:
- Scenario: YAML-defined eval scenario with expectations
- EvalResult: Results from running a scenario
- Scorer: Evaluates workspace state against expectations
- EvalRunner: Orchestrates scenario execution
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yaml12 import read_yaml

# ---------------------------------------------------------------------------
# Scenario definition
# ---------------------------------------------------------------------------


@dataclass
class Assertion:
    """A hard assertion that must pass for a scenario to pass."""

    type: str  # row_count, column_values, no_duplicates, null_count, schema, etc.
    column: str | None = None
    value: Any = None
    contains: list[Any] | None = None
    not_contains: list[Any] | None = None
    columns: list[str] | None = None
    tool: str | None = None  # For tool_was_used assertions
    values: list[str] | None = None  # For response_mentions assertions

    def check(self, ws: Any, context: dict[str, Any] | None = None) -> tuple[bool, str]:
        """Check this assertion against workspace state. Returns (passed, message).

        context may contain:
          - tool_calls: list[ToolCall] from the eval run
          - final_response: str from the agent's last message
        """
        context = context or {}

        # --- Assertions that don't need the workspace ---
        if self.type == "tool_was_used":
            if not self.tool:
                return False, "tool_was_used assertion requires 'tool'"
            tool_calls = context.get("tool_calls", [])
            used_tools = {tc.tool_name for tc in tool_calls}
            if self.tool in used_tools:
                return True, f"Tool '{self.tool}' was used"
            return False, f"Tool '{self.tool}' was NOT used. Tools used: {sorted(used_tools)}"

        if self.type == "response_mentions":
            if not self.values:
                return False, "response_mentions assertion requires 'values'"
            response = context.get("final_response", "")
            response_lower = response.lower()
            missing = [v for v in self.values if v.lower() not in response_lower]
            if missing:
                return False, f"Response missing mentions: {missing}"
            return True, f"Response mentions all: {self.values}"
        if ws.df is None:
            return False, "No data in workspace"

        df = ws.df

        if self.type == "row_count":
            actual = df.height
            passed = actual == self.value
            return passed, f"Row count: expected {self.value}, got {actual}"

        elif self.type == "column_count":
            actual = df.width
            passed = actual == self.value
            return passed, f"Column count: expected {self.value}, got {actual}"

        elif self.type == "column_values":
            if self.column is None:
                return False, "column_values assertion requires 'column'"
            if self.column not in df.columns:
                return False, f"Column '{self.column}' not found in {df.columns}"
            actual = df[self.column].drop_nulls().to_list()
            # Cast actual values to strings for comparison (handles Date, bool, etc.)
            actual_str = [str(v) for v in actual]
            actual_str_lower = [s.lower() for s in actual_str]
            if self.contains:
                missing = [
                    v
                    for v in self.contains
                    if v not in actual
                    and str(v) not in actual_str
                    and str(v).lower() not in actual_str_lower
                ]
                if missing:
                    return False, f"Column '{self.column}' missing values: {missing}"
                return True, f"Column '{self.column}' contains all expected values"
            if self.not_contains:
                found = [v for v in self.not_contains if v in actual or str(v) in actual_str]
                if found:
                    return False, f"Column '{self.column}' unexpectedly contains: {found}"
                return True, f"Column '{self.column}' does not contain forbidden values"
            return True, "No contains/not_contains specified"

        elif self.type == "no_duplicates":
            dupes = df.height - df.unique().height
            passed = dupes == 0
            return passed, f"Duplicates: {dupes}"

        elif self.type == "null_count":
            if self.column is None:
                return False, "null_count assertion requires 'column'"
            if self.column not in df.columns:
                return False, f"Column '{self.column}' not found"
            actual = df[self.column].null_count()
            passed = actual == self.value
            return passed, f"Null count in '{self.column}': expected {self.value}, got {actual}"

        elif self.type == "no_nulls":
            if self.column:
                if self.column not in df.columns:
                    return False, f"Column '{self.column}' not found"
                actual = df[self.column].null_count()
                passed = actual == 0
                return passed, f"Nulls in '{self.column}': {actual}"
            else:
                total = sum(df[c].null_count() for c in df.columns)
                passed = total == 0
                return passed, f"Total nulls across all columns: {total}"

        elif self.type == "columns_exist":
            if not self.columns:
                return False, "columns_exist assertion requires 'columns'"
            missing = [c for c in self.columns if c not in df.columns]
            if missing:
                return False, f"Missing columns: {missing}"
            return True, "All expected columns present"

        elif self.type == "column_type":
            if self.column is None or self.value is None:
                return False, "column_type assertion requires 'column' and 'value'"
            if self.column not in df.columns:
                return False, f"Column '{self.column}' not found"
            actual = str(df[self.column].dtype)
            passed = self.value.lower() in actual.lower()
            return passed, f"Type of '{self.column}': expected '{self.value}', got '{actual}'"

        elif self.type == "min_row_count":
            actual = df.height
            passed = actual >= self.value
            return passed, f"Row count: expected >= {self.value}, got {actual}"

        elif self.type == "max_row_count":
            actual = df.height
            passed = actual <= self.value
            return passed, f"Row count: expected <= {self.value}, got {actual}"

        else:
            return False, f"Unknown assertion type: {self.type}"


@dataclass
class SoftMetric:
    """A soft scoring metric (0.0-1.0)."""

    metric: str
    description: str
    weight: float = 1.0


@dataclass
class Scenario:
    """A complete eval scenario loaded from YAML."""

    name: str
    description: str
    dataset: str  # Relative path to dataset file
    task_prompt: str
    assertions: list[Assertion] = field(default_factory=list)
    soft_metrics: list[SoftMetric] = field(default_factory=list)
    max_turns: int = 20
    timeout_s: int = 120
    models: list[str] = field(default_factory=lambda: ["claude-sonnet-4-20250514"])
    surface: str = "mcp"
    category: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> list["Scenario"]:
        """Load one or more scenarios from a YAML file."""
        data = read_yaml(path)

        # YAML can contain a single scenario or a list
        if isinstance(data, list):
            scenarios_data = data
        else:
            scenarios_data = [data]

        scenarios = []
        for item in scenarios_data:
            assertions = []
            # Support assertions under "expectations.assertions" (old) or
            # directly under "assertions" (new, flat format)
            raw_assertions = item.get("expectations", {}).get("assertions", [])
            if not raw_assertions:
                raw_assertions = item.get("assertions", [])
            for a in raw_assertions:
                assertions.append(
                    Assertion(
                        type=a["type"],
                        column=a.get("column"),
                        value=a.get("value", a.get("expected")),
                        contains=a.get("contains"),
                        not_contains=a.get("not_contains"),
                        columns=a.get("columns"),
                        tool=a.get("tool"),
                        values=a.get("values"),
                    )
                )

            soft_metrics = []
            for m in item.get("expectations", {}).get("scoring", []):
                soft_metrics.append(
                    SoftMetric(
                        metric=m["metric"],
                        description=m["description"],
                        weight=m.get("weight", 1.0),
                    )
                )

            scenarios.append(
                cls(
                    name=item["name"],
                    description=item.get("description", ""),
                    dataset=item["dataset"],
                    task_prompt=item["task_prompt"],
                    assertions=assertions,
                    soft_metrics=soft_metrics,
                    max_turns=item.get("max_turns", 20),
                    timeout_s=item.get("timeout_s", 120),
                    models=item.get("models", ["claude-sonnet-4-20250514"]),
                    surface=item.get("surface", "mcp"),
                    category=item.get("category", ""),
                    tags=item.get("tags", []),
                )
            )

        return scenarios


# ---------------------------------------------------------------------------
# Eval results
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """Record of a single tool call during an eval run."""

    tool_name: str
    arguments: dict[str, Any]
    result: str
    duration_s: float = 0.0


@dataclass
class ConversationMessage:
    """A single message in the eval conversation."""

    role: str  # "user", "assistant", "steering"
    content: str
    thinking: str | None = None  # Extended thinking text (assistant only)


@dataclass
class EvalResult:
    """Result of running a single scenario."""

    scenario_name: str
    model: str  # Kept for backward compat (= assistant_model)
    surface: str
    passed: bool  # All hard assertions passed
    assertion_results: list[tuple[bool, str]] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    total_turns: int = 0
    total_duration_s: float = 0.0
    final_response: str = ""
    error: str | None = None
    # Model matrix fields
    user_model: str = ""
    assistant_model: str = ""
    # Conversation capture
    conversation: list[ConversationMessage] = field(default_factory=list)
    steering_count: int = 0

    @property
    def assertion_pass_rate(self) -> float:
        if not self.assertion_results:
            return 0.0
        passed = sum(1 for p, _ in self.assertion_results if p)
        return passed / len(self.assertion_results)

    @property
    def score(self) -> float:
        """Compute overall score (0.0-1.0)."""
        if self.error:
            return 0.0

        # 60% hard assertions, 20% completion (no error), 20% efficiency
        assertion_score = self.assertion_pass_rate
        completion_score = 1.0 if not self.error else 0.0
        efficiency_score = max(0.0, 1.0 - (self.total_turns / 30.0))  # Penalize > 30 turns

        return assertion_score * 0.6 + completion_score * 0.2 + efficiency_score * 0.2

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON output."""
        return {
            "scenario_name": self.scenario_name,
            "model": self.model,
            "surface": self.surface,
            "passed": self.passed,
            "score": round(self.score, 3),
            "assertion_pass_rate": round(self.assertion_pass_rate, 3),
            "total_turns": self.total_turns,
            "total_duration_s": round(self.total_duration_s, 2),
            "tool_call_count": len(self.tool_calls),
            "tool_calls": [
                {
                    "tool": tc.tool_name,
                    "args": tc.arguments,
                    "result": tc.result,
                    "duration_s": tc.duration_s,
                }
                for tc in self.tool_calls
            ],
            "assertion_results": [{"passed": p, "message": m} for p, m in self.assertion_results],
            "final_response": self.final_response,
            "error": self.error,
            "user_model": self.user_model,
            "assistant_model": self.assistant_model,
            "conversation": [
                {
                    "role": m.role,
                    "content": m.content,
                    "thinking": m.thinking,
                }
                for m in self.conversation
            ],
            "steering_count": self.steering_count,
        }


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


class Scorer:
    """Evaluates workspace state against scenario expectations."""

    def score(
        self, ws: Any, scenario: Scenario, context: dict[str, Any] | None = None
    ) -> list[tuple[bool, str]]:
        """Run all assertions and return results."""
        results = []
        for assertion in scenario.assertions:
            results.append(assertion.check(ws, context))
        return results


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------


def save_results(results: list[EvalResult], output_dir: Path) -> Path:
    """Save eval results to a timestamped JSON file and generate a QMD report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"eval_{timestamp}.json"

    data = {
        "timestamp": timestamp,
        "total_scenarios": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "mean_score": round(sum(r.score for r in results) / len(results) if results else 0.0, 3),
        "results": [r.to_dict() for r in results],
    }

    path.write_text(json.dumps(data, indent=2))

    # Also generate QMD report
    from .report import generate_qmd_report

    qmd_path = output_dir / f"eval_report_{timestamp}.qmd"
    generate_qmd_report(results, qmd_path)

    return path


def print_summary(results: list[EvalResult]) -> None:
    """Print a summary table of results to stdout."""
    if not results:
        print("No results.")
        return

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    mean_score = sum(r.score for r in results) / total

    print(f"\n{'=' * 70}")
    print(f"EVAL RESULTS: {passed}/{total} passed ({passed / total * 100:.0f}%)")
    print(f"Mean score: {mean_score:.3f}")
    print(f"{'=' * 70}")
    print(f"{'Scenario':<40} {'Pass':>5} {'Score':>6} {'Turns':>6} {'Time':>7}")
    print(f"{'-' * 70}")

    for r in results:
        status = "✅" if r.passed else "❌"
        print(
            f"{r.scenario_name:<40} {status:>5} {r.score:>6.3f} "
            f"{r.total_turns:>6} {r.total_duration_s:>6.1f}s"
        )

    print(f"{'=' * 70}\n")
