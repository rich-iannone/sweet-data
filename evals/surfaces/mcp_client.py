"""MCP Agent Client — Drives an LLM agent through Sweet's MCP tools.

This client:
1. Loads Sweet's MCP tool definitions
2. Converts them to chatlas-compatible Tool objects
3. Registers them with a chatlas Chat instance
4. Runs the agent loop (chatlas handles tool-call cycling automatically)
5. Records all tool calls for scoring
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from chatlas import ChatAnthropic, ChatOpenAI, Tool

from sweet.core.workspace import Workspace
from sweet.mcp import call_tool, list_tools

from ..framework import EvalResult, Scenario, Scorer, ToolCall

# ---------------------------------------------------------------------------
# Core client
# ---------------------------------------------------------------------------

# Subset of tools relevant for eval scenarios (avoids overwhelming the model)
EVAL_TOOLS = {
    "sweet_load",
    "sweet_inspect",
    "sweet_transform",
    "sweet_query",
    "sweet_filter",
    "sweet_sort",
    "sweet_select",
    "sweet_branch",
    "sweet_switch",
    "sweet_export",
    "sweet_undo",
    "sweet_redo",
    "sweet_history",
    "sweet_sheets",
    "sweet_sample",
    "sweet_scan",
    "sweet_validate",
    "sweet_schema",
    "sweet_detect_types",
    "sweet_detect_outliers",
    "sweet_describe",
    "sweet_suggest_casts",
    "sweet_apply_casts",
    "sweet_run_recipe",
    "sweet_list_recipes",
    "sweet_run_steps",
    "sweet_correlations",
    "sweet_generate_code",
}


class MCPAgentClient:
    """Runs an LLM agent against Sweet's MCP tools in-process.

    Uses chatlas to handle the tool-call loop automatically:
    - Agent sends tool_use requests
    - chatlas invokes our wrapper functions
    - Results sent back to agent
    - Loop until agent produces final text response
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_turns: int = 20,
        tool_subset: set[str] | None = None,
    ):
        self.model = model
        self.max_turns = max_turns
        self.tool_subset = tool_subset or EVAL_TOOLS
        self._tool_calls: list[ToolCall] = []
        self._workspace: Workspace | None = None

    def _create_chat(self, system_prompt: str) -> Any:
        """Create a chatlas Chat instance for the given model."""
        if "claude" in self.model.lower() or "sonnet" in self.model.lower():
            return ChatAnthropic(
                system_prompt=system_prompt,
                model=self.model,
                max_tokens=4096,
            )
        elif "gpt" in self.model.lower() or "o1" in self.model.lower():
            return ChatOpenAI(
                system_prompt=system_prompt,
                model=self.model,
            )
        else:
            # Default to Anthropic
            return ChatAnthropic(
                system_prompt=system_prompt,
                model=self.model,
                max_tokens=4096,
            )

    def _reset_workspace(self) -> Workspace:
        """Reset the global MCP workspace to a fresh state."""
        import sweet.mcp as mcp_module

        ws = Workspace()
        mcp_module._workspace = ws
        self._workspace = ws
        return ws

    def _make_tool_func(self, tool_name: str) -> Any:
        """Create a sync wrapper function for an MCP tool."""

        def tool_func(**kwargs: Any) -> str:
            start = time.time()
            try:
                # call_tool is async, run it in the existing event loop or new one
                result = asyncio.run(call_tool(tool_name, kwargs))
                text = result[0].text if result else "No result"
            except Exception as e:
                text = f"Error: {e}"
            duration = time.time() - start

            self._tool_calls.append(
                ToolCall(
                    tool_name=tool_name,
                    arguments=kwargs,
                    result=text[:500],  # Truncate for storage
                    duration_s=round(duration, 3),
                )
            )
            return text

        return tool_func

    def _register_tools(self, chat: Any) -> None:
        """Register MCP tools with the chatlas Chat instance."""
        # Get tool definitions from MCP
        tools = asyncio.run(list_tools())

        for mcp_tool in tools:
            if mcp_tool.name not in self.tool_subset:
                continue

            # Create the wrapper function
            func = self._make_tool_func(mcp_tool.name)

            # Build the chatlas Tool with the MCP schema
            chatlas_tool = Tool(
                func=func,
                name=mcp_tool.name,
                description=mcp_tool.description,
                parameters=mcp_tool.inputSchema,
            )

            chat.set_tools(chat.get_tools() + [chatlas_tool])

    def run_scenario(self, scenario: Scenario, dataset_dir: Path) -> EvalResult:
        """Run a complete eval scenario and return results."""
        self._tool_calls = []
        start_time = time.time()

        # Reset workspace
        ws = self._reset_workspace()

        # Build system prompt
        system_prompt = self._build_system_prompt(scenario, dataset_dir)

        # Create chat and register tools
        chat = self._create_chat(system_prompt)
        self._register_tools(chat)

        try:
            # Run the agent — chatlas handles the tool-call loop
            response = chat.chat(
                scenario.task_prompt,
                echo="none",
                stream=False,
            )
            final_text = str(response)
        except Exception as e:
            total_time = time.time() - start_time
            return EvalResult(
                scenario_name=scenario.name,
                model=self.model,
                surface="mcp",
                passed=False,
                tool_calls=self._tool_calls,
                total_turns=len(self._tool_calls),
                total_duration_s=round(total_time, 2),
                error=str(e),
            )

        total_time = time.time() - start_time

        # Score against assertions
        scorer = Scorer()
        assertion_results = scorer.score(self._workspace, scenario)
        all_passed = all(p for p, _ in assertion_results)

        return EvalResult(
            scenario_name=scenario.name,
            model=self.model,
            surface="mcp",
            passed=all_passed,
            assertion_results=assertion_results,
            tool_calls=self._tool_calls,
            total_turns=len(self._tool_calls),
            total_duration_s=round(total_time, 2),
            final_response=final_text[:1000],
        )

    def _build_system_prompt(self, scenario: Scenario, dataset_dir: Path) -> str:
        """Build the system prompt for the agent."""
        # Resolve the dataset path so the agent knows where to load from
        dataset_path = dataset_dir / scenario.dataset

        return f"""You are a data engineer using Sweet, a data workspace tool.
You have access to tools for loading, inspecting, transforming, and exporting data.

IMPORTANT CONTEXT:
- The dataset file is located at: {dataset_path}
- Use the sweet_load tool with this exact path to load data.
- After loading, use sweet_inspect to understand the data before transforming.
- If a transform fails or produces unexpected results, use sweet_undo and try a different approach.
- When you have completed the task, respond with a brief summary of what you accomplished.

RULES:
- Always inspect data before transforming it.
- Use Polars expressions for transforms (the tool receives `df` and `pl`).
- Prefer simple, composable transforms over complex single expressions.
- If unsure about column names or types, use sweet_inspect or sweet_schema first.
"""

    @property
    def workspace(self) -> Workspace | None:
        """Access the workspace after a run (for assertions)."""
        return self._workspace
