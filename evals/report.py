"""Quarto report generator for eval results.

Produces a .qmd file that can be rendered to HTML with `quarto render`.
Uses Great Tables for structured data presentation.

Includes:
- Model comparison table (Great Tables)
- Results grouped by category, then by model
- Per-scenario detail panels showing conversation, thinking, and steering
- Full agent Markdown responses rendered natively
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .framework import ConversationMessage, EvalResult


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
    lines.append(f'title: "{title}"')
    lines.append(f'date: "{time.strftime("%Y-%m-%d %H:%M:%S")}"')
    lines.append("format:")
    lines.append("  html:")
    lines.append("    theme: cosmo")
    lines.append("    toc: true")
    lines.append("    toc-depth: 3")
    lines.append("    code-fold: true")
    lines.append("    self-contained: true")
    lines.append("jupyter: python3")
    lines.append("---")
    lines.append("")

    # --- Overview ---
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    mean_score = sum(r.score for r in results) / total if total else 0.0
    total_time = sum(r.total_duration_s for r in results)
    total_tool_calls = sum(len(r.tool_calls) for r in results)
    total_steerings = sum(r.steering_count for r in results)

    # Detect model matrix
    models_used = sorted(set(r.assistant_model or r.model for r in results))
    is_matrix = len(models_used) > 1

    lines.append("## Overview")
    lines.append("")
    lines.append("::: {.callout-note}")
    lines.append(f"**{passed}/{total}** scenarios passed")
    lines.append(
        f"({passed / total * 100:.0f}% pass rate) | "
        f"Mean score: **{mean_score:.3f}** | "
        f"Total time: {total_time:.1f}s | "
        f"Total tool calls: {total_tool_calls}"
    )
    if total_steerings > 0:
        lines.append(
            f"| User-model steering interventions: {total_steerings}"
        )
    lines.append(":::")
    lines.append("")

    # --- Model Comparison Table (Great Tables) ---
    if is_matrix:
        lines.append("## Model Comparison")
        lines.append("")
        lines.append("```{python}")
        lines.append("#| echo: false")
        lines.append("import polars as pl")
        lines.append("from great_tables import GT")
        lines.append("")

        # Build model comparison data
        model_rows = []
        for model in models_used:
            mr = [r for r in results if (r.assistant_model or r.model) == model]
            mp = sum(1 for r in mr if r.passed)
            mf = len(mr) - mp
            ms = sum(r.score for r in mr) / len(mr) if mr else 0.0
            mt = sum(r.total_turns for r in mr) / len(mr) if mr else 0
            md_time = sum(r.total_duration_s for r in mr) / len(mr) if mr else 0.0
            mst = sum(r.steering_count for r in mr)
            model_rows.append({
                "model": model,
                "passed": mp,
                "failed": mf,
                "pass_rate": mp / (mp + mf) if (mp + mf) else 0.0,
                "mean_score": round(ms, 3),
                "avg_turns": round(mt, 1),
                "avg_time_s": round(md_time, 1),
                "steerings": mst,
            })

        # Determine user model for source note
        user_models = sorted(set(r.user_model for r in results if r.user_model))
        user_model_note = user_models[0] if user_models else "N/A"

        lines.append(f"data = {json.dumps(model_rows)}")
        lines.append("df = pl.DataFrame(data)")
        lines.append("")
        lines.append("(")
        lines.append("    GT(df)")
        lines.append('    .tab_header(title="Assistant Model Comparison")')
        lines.append("    .cols_label(")
        lines.append('        model="Model",')
        lines.append('        passed="Passed",')
        lines.append('        failed="Failed",')
        lines.append('        pass_rate="Pass Rate",')
        lines.append('        mean_score="Mean Score",')
        lines.append('        avg_turns="Avg Turns",')
        lines.append('        avg_time_s="Avg Time (s)",')
        lines.append('        steerings="Steerings",')
        lines.append("    )")
        lines.append('    .fmt_number("mean_score", decimals=3)')
        lines.append('    .fmt_number("avg_turns", decimals=1)')
        lines.append('    .fmt_number("avg_time_s", decimals=1)')
        lines.append('    .fmt_percent("pass_rate", decimals=0)')
        lines.append(f'    .tab_source_note("User model: {user_model_note}")')
        lines.append(")")
        lines.append("```")
        lines.append("")

    # --- Category Summary Table (Great Tables) ---
    lines.append("## Results by Category")
    lines.append("")

    # Group by category
    categories: dict[str, list[EvalResult]] = {}
    for r in results:
        cat = _infer_category(r)
        categories.setdefault(cat, []).append(r)

    lines.append("```{python}")
    lines.append("#| echo: false")
    lines.append("import polars as pl")
    lines.append("from great_tables import GT")
    lines.append("")

    cat_rows = []
    for cat, cat_results in sorted(categories.items()):
        cp = sum(1 for r in cat_results if r.passed)
        cf = len(cat_results) - cp
        cm = sum(r.score for r in cat_results) / len(cat_results)
        cat_rows.append({
            "category": cat.replace("_", " ").title(),
            "scenarios": len(cat_results),
            "passed": cp,
            "failed": cf,
            "mean_score": round(cm, 3),
        })

    lines.append(f"data = {json.dumps(cat_rows)}")
    lines.append("df = pl.DataFrame(data)")
    lines.append("")
    lines.append("(")
    lines.append("    GT(df)")
    lines.append('    .tab_header(title="Performance by Category")')
    lines.append("    .cols_label(")
    lines.append('        category="Category",')
    lines.append('        scenarios="Scenarios",')
    lines.append('        passed="Passed",')
    lines.append('        failed="Failed",')
    lines.append('        mean_score="Mean Score",')
    lines.append("    )")
    lines.append('    .fmt_number("mean_score", decimals=3)')
    lines.append(")")
    lines.append("```")
    lines.append("")

    # --- Detailed results table per category (Great Tables) ---
    for cat in sorted(categories.keys()):
        cat_results = categories[cat]
        cat_title = cat.replace("_", " ").title()

        lines.append(f"### {cat_title}")
        lines.append("")
        lines.append("```{python}")
        lines.append("#| echo: false")
        lines.append("import polars as pl")
        lines.append("from great_tables import GT")
        lines.append("")

        detail_rows = []
        scenario_names = sorted(set(r.scenario_name for r in cat_results))
        for sname in scenario_names:
            for model in models_used:
                matching = [
                    r for r in cat_results
                    if r.scenario_name == sname
                    and (r.assistant_model or r.model) == model
                ]
                if matching:
                    r = matching[0]
                    a_pass = sum(1 for p, _ in r.assertion_results if p)
                    a_total = len(r.assertion_results)
                    detail_rows.append({
                        "scenario": r.scenario_name,
                        "model": model.split("-")[-1] if is_matrix else model,
                        "status": "PASS" if r.passed else "FAIL",
                        "score": round(r.score, 3),
                        "assertions": f"{a_pass}/{a_total}",
                        "turns": r.total_turns,
                        "time_s": round(r.total_duration_s, 1),
                        "steering": r.steering_count,
                    })

        lines.append(f"data = {json.dumps(detail_rows)}")
        lines.append("df = pl.DataFrame(data)")
        lines.append("")
        lines.append("(")
        lines.append("    GT(df)")
        lines.append(f'    .tab_header(title="{cat_title} Scenarios")')
        lines.append("    .cols_label(")
        lines.append('        scenario="Scenario",')
        if is_matrix:
            lines.append('        model="Model",')
        lines.append('        status="Pass",')
        lines.append('        score="Score",')
        lines.append('        assertions="Assertions",')
        lines.append('        turns="Turns",')
        lines.append('        time_s="Time (s)",')
        lines.append('        steering="Steering",')
        lines.append("    )")
        lines.append('    .fmt_number("score", decimals=3)')
        lines.append('    .fmt_number("time_s", decimals=1)')
        if not is_matrix:
            lines.append('    .cols_hide("model")')
        lines.append(")")
        lines.append("```")
        lines.append("")

    # --- Detail panels per scenario, grouped by category then model ---
    lines.append("## Scenario Details")
    lines.append("")

    for cat in sorted(categories.keys()):
        cat_results = categories[cat]
        cat_title = cat.replace("_", " ").title()
        lines.append(f"### {cat_title}")
        lines.append("")

        scenario_names = sorted(set(r.scenario_name for r in cat_results))
        for sname in scenario_names:
            scenario_results = [
                r for r in cat_results if r.scenario_name == sname
            ]
            scenario_results.sort(key=lambda r: r.assistant_model or r.model)

            for r in scenario_results:
                status_emoji = "✅" if r.passed else "❌"
                model_label = r.assistant_model or r.model

                if is_matrix:
                    lines.append(
                        f"#### {status_emoji} {r.scenario_name} — `{model_label}`"
                    )
                else:
                    lines.append(f"#### {status_emoji} {r.scenario_name}")
                lines.append("")

                # Metadata
                lines.append(
                    f"**Assistant**: `{model_label}` | "
                    f"**User**: `{r.user_model or 'N/A'}` | "
                    f"**Score**: {r.score:.3f} | "
                    f"**Turns**: {r.total_turns} | "
                    f"**Duration**: {r.total_duration_s:.1f}s"
                )
                if r.steering_count > 0:
                    lines.append(
                        f" | **Steering interventions**: {r.steering_count}"
                    )
                lines.append("")

                # Assertion results
                lines.append("**Assertions:**")
                lines.append("")
                for passed_flag, msg in r.assertion_results:
                    icon = "✅" if passed_flag else "❌"
                    lines.append(f"- {icon} {msg}")
                lines.append("")

                # Conversation with thinking
                if r.conversation:
                    lines.append('::: {.callout-tip collapse="true"}')
                    lines.append("## Conversation")
                    lines.append("")

                    for msg in r.conversation:
                        if msg.role == "user":
                            lines.append("**🧑 User (task prompt):**")
                            lines.append("")
                            lines.append(f"> {_blockquote(msg.content)}")
                            lines.append("")
                        elif msg.role == "steering":
                            lines.append("**🔄 User (steering):**")
                            lines.append("")
                            lines.append(f"> ⚠️ {_blockquote(msg.content)}")
                            lines.append("")
                        elif msg.role == "assistant":
                            lines.append("**🤖 Assistant:**")
                            lines.append("")
                            if msg.thinking:
                                lines.append(
                                    '::: {.callout-note collapse="true"}'
                                )
                                lines.append("## 💭 Thinking")
                                lines.append("")
                                lines.append(msg.thinking)
                                lines.append("")
                                lines.append(":::")
                                lines.append("")
                            # Render assistant content as native Markdown
                            lines.append(msg.content)
                            lines.append("")

                    lines.append(":::")
                    lines.append("")

                # Tool call trace
                if r.tool_calls:
                    lines.append('::: {.callout-tip collapse="true"}')
                    lines.append("## Tool Calls")
                    lines.append("")

                    for i, tc in enumerate(r.tool_calls, 1):
                        lines.append(
                            f"**Turn {i}**: `{tc.tool_name}` "
                            f"({tc.duration_s:.2f}s)"
                        )
                        lines.append("")
                        lines.append("```json")
                        args_str = json.dumps(
                            tc.arguments, indent=2, default=str
                        )
                        if len(args_str) > 500:
                            args_str = args_str[:500] + "\n  // ... truncated"
                        lines.append(args_str)
                        lines.append("```")
                        lines.append("")
                        result_text = tc.result
                        if len(result_text) > 400:
                            result_text = result_text[:400] + "..."
                        lines.append(f"> {_blockquote(result_text)}")
                        lines.append("")

                    lines.append(":::")
                    lines.append("")

                # Final agent response (full, rendered as Markdown)
                # Use last assistant conversation message if final_response
                # was truncated (exactly 1000 chars from old runs)
                final_text = r.final_response
                if final_text and len(final_text) == 1000 and r.conversation:
                    last_asst = [
                        m for m in r.conversation if m.role == "assistant"
                    ]
                    if last_asst:
                        final_text = last_asst[-1].content

                if final_text:
                    lines.append('::: {.callout-tip collapse="true"}')
                    lines.append("## Agent's Final Response")
                    lines.append("")
                    lines.append(final_text)
                    lines.append("")
                    lines.append(":::")
                    lines.append("")

                # Error if present
                if r.error:
                    lines.append("::: {.callout-warning}")
                    lines.append(f"**Error**: {r.error}")
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

        # Reconstruct conversation messages
        conversation = []
        for msg in item.get("conversation", []):
            conversation.append(
                ConversationMessage(
                    role=msg["role"],
                    content=msg["content"],
                    thinking=msg.get("thinking"),
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
            user_model=item.get("user_model", ""),
            assistant_model=item.get("assistant_model", ""),
            conversation=conversation,
            steering_count=item.get("steering_count", 0),
        )
        results.append(result)

    return generate_qmd_report(results, output_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blockquote(text: str) -> str:
    """Format text for blockquote (handle multi-line)."""
    lines = text.split("\n")
    return "\n> ".join(lines)


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
