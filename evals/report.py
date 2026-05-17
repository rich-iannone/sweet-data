"""Quarto report generator for eval results.

Produces a .qmd file that can be rendered to HTML with `quarto render`.
Includes:
- Summary table of all scenarios with scores
- Per-scenario detail panels showing the tool-call conversation
- Assertion results with pass/fail indicators
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .framework import EvalResult


def generate_qmd_report(
    results: list[EvalResult],
    output_path: Path | None = None,
    title: str = "Sweet Eval Report",
) -> Path:
    """Generate a Quarto .qmd report from eval results.

    Args:
        results: List of EvalResult objects from a run.
        output_path: Where to write the .qmd file. Defaults to evals/results/.
        title: Report title.

    Returns:
        Path to the generated .qmd file.
    """
    if output_path is None:
        output_dir = Path(__file__).parent / "results"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"eval_report_{timestamp}.qmd"

    lines: list[str] = []

    # --- YAML front matter ---
    lines.append("---")
    lines.append(f"title: \"{title}\"")
    lines.append(f"date: \"{time.strftime('%Y-%m-%d %H:%M:%S')}\"")
    lines.append("format:")
    lines.append("  html:")
    lines.append("    theme: cosmo")
    lines.append("    toc: true")
    lines.append("    toc-depth: 3")
    lines.append("    code-fold: true")
    lines.append("    self-contained: true")
    lines.append("---")
    lines.append("")

    # --- Overview ---
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    mean_score = sum(r.score for r in results) / total if total else 0.0
    total_time = sum(r.total_duration_s for r in results)
    total_tool_calls = sum(len(r.tool_calls) for r in results)

    lines.append("## Overview")
    lines.append("")
    lines.append("::: {.callout-note}")
    lines.append(f"**{passed}/{total}** scenarios passed")
    lines.append(f"({passed / total * 100:.0f}% pass rate) | "
                 f"Mean score: **{mean_score:.3f}** | "
                 f"Total time: {total_time:.1f}s | "
                 f"Total tool calls: {total_tool_calls}")
    lines.append(":::")
    lines.append("")

    # --- Summary table ---
    lines.append("## Summary")
    lines.append("")
    lines.append("| Scenario | Category | Pass | Score | Assertions | Turns | Time (s) |")
    lines.append("|:---------|:---------|:----:|------:|-----------:|------:|---------:|")

    for r in results:
        status = "✅" if r.passed else "❌"
        assertion_str = f"{sum(1 for p, _ in r.assertion_results if p)}/{len(r.assertion_results)}"
        # Get category from scenario name heuristic (or from stored data)
        lines.append(
            f"| {r.scenario_name} | {r.surface} | {status} | "
            f"{r.score:.3f} | {assertion_str} | "
            f"{r.total_turns} | {r.total_duration_s:.1f} |"
        )

    lines.append("")

    # --- Category breakdown ---
    categories: dict[str, list[EvalResult]] = {}
    for r in results:
        # We'll store category in the result dict if available
        cat = _infer_category(r)
        categories.setdefault(cat, []).append(r)

    if len(categories) > 1:
        lines.append("### By Category")
        lines.append("")
        lines.append("| Category | Passed | Failed | Mean Score |")
        lines.append("|:---------|-------:|-------:|-----------:|")
        for cat, cat_results in sorted(categories.items()):
            cp = sum(1 for r in cat_results if r.passed)
            cf = len(cat_results) - cp
            cm = sum(r.score for r in cat_results) / len(cat_results)
            lines.append(f"| {cat} | {cp} | {cf} | {cm:.3f} |")
        lines.append("")

    # --- Detail panels per scenario ---
    lines.append("## Scenario Details")
    lines.append("")

    for r in results:
        status_emoji = "✅" if r.passed else "❌"
        lines.append(f"### {status_emoji} {r.scenario_name}")
        lines.append("")

        # Metadata
        lines.append(f"**Model**: `{r.model}` | "
                     f"**Score**: {r.score:.3f} | "
                     f"**Turns**: {r.total_turns} | "
                     f"**Duration**: {r.total_duration_s:.1f}s")
        lines.append("")

        # Assertion results
        lines.append("#### Assertions")
        lines.append("")
        for passed_flag, msg in r.assertion_results:
            icon = "✅" if passed_flag else "❌"
            lines.append(f"- {icon} {msg}")
        lines.append("")

        # Tool call conversation
        if r.tool_calls:
            lines.append("#### Conversation (Tool Calls)")
            lines.append("")
            lines.append("::: {.callout-tip collapse=\"true\"}")
            lines.append("## Expand to see full tool-call trace")
            lines.append("")

            for i, tc in enumerate(r.tool_calls, 1):
                lines.append(f"**Turn {i}**: `{tc.tool_name}` ({tc.duration_s:.2f}s)")
                lines.append("")

                # Arguments
                lines.append("```json")
                args_str = json.dumps(tc.arguments, indent=2, default=str)
                # Truncate very long args
                if len(args_str) > 500:
                    args_str = args_str[:500] + "\n  // ... truncated"
                lines.append(args_str)
                lines.append("```")
                lines.append("")

                # Result (truncated)
                result_text = tc.result
                if len(result_text) > 300:
                    result_text = result_text[:300] + "..."
                lines.append(f"> **Result**: {_escape_markdown(result_text)}")
                lines.append("")

            lines.append(":::")
            lines.append("")

        # Final agent response
        if r.final_response:
            lines.append("#### Agent Summary")
            lines.append("")
            lines.append("::: {.callout-tip collapse=\"true\"}")
            lines.append("## Agent's final response")
            lines.append("")
            lines.append(_escape_markdown(r.final_response))
            lines.append("")
            lines.append(":::")
            lines.append("")

        # Error if present
        if r.error:
            lines.append("::: {.callout-warning}")
            lines.append(f"**Error**: {_escape_markdown(r.error)}")
            lines.append(":::")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Write file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    return output_path


def generate_qmd_from_json(json_path: Path, output_path: Path | None = None) -> Path:
    """Generate a .qmd report from a saved JSON results file."""
    data = json.loads(json_path.read_text())

    results = []
    for item in data.get("results", []):
        tool_calls = []
        for tc in item.get("tool_calls", []):
            from .framework import ToolCall

            tool_calls.append(
                ToolCall(
                    tool_name=tc["tool"],
                    arguments=tc.get("args", {}),
                    result=tc.get("result", ""),
                    duration_s=tc.get("duration_s", 0.0),
                )
            )

        result = EvalResult(
            scenario_name=item["scenario_name"],
            model=item["model"],
            surface=item["surface"],
            passed=item["passed"],
            assertion_results=[
                (a["passed"], a["message"]) for a in item.get("assertion_results", [])
            ],
            tool_calls=tool_calls,
            total_turns=item.get("total_turns", 0),
            total_duration_s=item.get("total_duration_s", 0.0),
            final_response=item.get("final_response", ""),
            error=item.get("error"),
        )
        results.append(result)

    return generate_qmd_report(results, output_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _escape_markdown(text: str) -> str:
    """Escape text for safe inclusion in markdown (minimal)."""
    # Replace pipe characters that would break tables
    text = text.replace("|", "\\|")
    # Ensure no unintended heading creation
    lines = text.split("\n")
    escaped_lines = []
    for line in lines:
        if line.startswith("#"):
            line = "\\" + line
        escaped_lines.append(line)
    return "\n".join(escaped_lines)


def _infer_category(result: EvalResult) -> str:
    """Infer category from scenario name."""
    name = result.scenario_name.lower()
    if any(kw in name for kw in ["clean", "fix", "validate", "currency"]):
        return "cleaning"
    elif any(kw in name for kw in ["profile", "outlier", "segment", "eda"]):
        return "eda"
    elif any(kw in name for kw in ["pipeline", "join", "aggregat", "expense"]):
        return "pipeline"
    elif any(kw in name for kw in ["recover", "ambiguous", "error", "missing"]):
        return "error_recovery"
    elif any(kw in name for kw in ["multi_sheet", "workflow", "advanced", "recipe"]):
        return "advanced"
    return "other"


# ---------------------------------------------------------------------------
# CLI entry point: python -m evals.report [json_file]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    results_dir = Path(__file__).parent / "results"

    if len(sys.argv) > 1:
        json_file = Path(sys.argv[1])
    else:
        # Find the most recent JSON file in results/
        json_files = sorted(results_dir.glob("eval_*.json"))
        if not json_files:
            print("No eval result JSON files found in evals/results/")
            sys.exit(1)
        json_file = json_files[-1]

    print(f"Generating report from: {json_file.name}")
    qmd_path = generate_qmd_from_json(json_file)
    print(f"Report written to: {qmd_path}")
    print(f"Render with: quarto render {qmd_path}")
