"""MCP Agent Client — Drives an LLM agent through Sweet's MCP tools.

This client supports a two-model architecture:
- Assistant model: Receives the task, uses tools, generates responses
- User model: Evaluates progress and steers the assistant if needed

Flow:
1. Loads Sweet's MCP tool definitions
2. Converts them to chatlas-compatible Tool objects
3. Registers them with a chatlas Chat instance (assistant)
4. Runs the agent loop (chatlas handles tool-call cycling automatically)
5. User model evaluates the result and optionally steers
6. Records all tool calls, conversation, and thinking for scoring
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from chatlas import ChatAnthropic, ChatOpenAI, Tool

from sweet.core.workspace import Workspace
from sweet.mcp import call_tool, list_tools

from ..framework import ConversationMessage, EvalResult, Scenario, Scorer, ToolCall

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
    "sweet_detect_pii",
    "sweet_relationships",
    "sweet_infer_contract",
    "sweet_enforce_contract",
    "sweet_suggest_casts",
    "sweet_apply_casts",
    "sweet_suggest",
    "sweet_sundered",
    "sweet_generate_code",
    "sweet_generate_pipeline",
    "sweet_commit",
    "sweet_version_log",
    "sweet_to_great_table",
    "sweet_run_recipe",
    "sweet_list_recipes",
    "sweet_run_steps",
    "sweet_correlations",
}


class MCPAgentClient:
    """Runs an LLM agent against Sweet's MCP tools in-process.

    Supports a two-model architecture:
    - assistant_model: The model being evaluated (uses tools, generates data ops)
    - user_model: Evaluates progress and steers the assistant if things go wrong

    Uses chatlas to handle the tool-call loop automatically:
    - Agent sends tool_use requests
    - chatlas invokes our wrapper functions
    - Results sent back to agent
    - Loop until agent produces final text response
    """

    # Default models
    DEFAULT_ASSISTANT_MODEL = "claude-opus-4-6"
    DEFAULT_USER_MODEL = "claude-sonnet-4-6"

    def __init__(
        self,
        model: str | None = None,  # Backward compat — sets assistant_model
        assistant_model: str | None = None,
        user_model: str | None = None,
        max_turns: int = 20,
        max_steering_turns: int = 3,
        tool_subset: set[str] | None = None,
        enable_thinking: bool = True,
        thinking_budget: int = 10000,
    ):
        self.assistant_model = assistant_model or model or self.DEFAULT_ASSISTANT_MODEL
        self.user_model = user_model or self.DEFAULT_USER_MODEL
        self.max_turns = max_turns
        self.max_steering_turns = max_steering_turns
        self.tool_subset = tool_subset or EVAL_TOOLS
        self.enable_thinking = enable_thinking
        self.thinking_budget = thinking_budget
        self._tool_calls: list[ToolCall] = []
        self._conversation: list[ConversationMessage] = []
        self._workspace: Workspace | None = None

    # Backward compat property
    @property
    def model(self) -> str:
        return self.assistant_model

    def _create_chat(self, system_prompt: str, model: str, with_thinking: bool = False) -> Any:
        """Create a chatlas Chat instance for the given model."""
        if "claude" in model.lower() or "sonnet" in model.lower() or "opus" in model.lower():
            chat = ChatAnthropic(
                system_prompt=system_prompt,
                model=model,
                max_tokens=16384 if with_thinking else 4096,
            )
            if with_thinking and self.enable_thinking:
                chat.set_model_params(
                    kwargs={
                        "thinking": {
                            "type": "enabled",
                            "budget_tokens": self.thinking_budget,
                        }
                    }
                )
            return chat
        elif "gpt" in model.lower() or "o1" in model.lower():
            return ChatOpenAI(
                system_prompt=system_prompt,
                model=model,
            )
        else:
            # Default to Anthropic
            return ChatAnthropic(
                system_prompt=system_prompt,
                model=model,
                max_tokens=16384 if with_thinking else 4096,
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
                result = asyncio.run(call_tool(tool_name, kwargs))
                text = result[0].text if result else "No result"
            except Exception as e:
                text = f"Error: {e}"
            duration = time.time() - start

            self._tool_calls.append(
                ToolCall(
                    tool_name=tool_name,
                    arguments=kwargs,
                    result=text[:500],
                    duration_s=round(duration, 3),
                )
            )
            return text

        return tool_func

    def _register_tools(self, chat: Any) -> None:
        """Register MCP tools with the chatlas Chat instance."""
        tools = asyncio.run(list_tools())

        for mcp_tool in tools:
            if mcp_tool.name not in self.tool_subset:
                continue

            func = self._make_tool_func(mcp_tool.name)

            chatlas_tool = Tool(
                func=func,
                name=mcp_tool.name,
                description=mcp_tool.description,
                parameters=mcp_tool.inputSchema,
            )

            chat.set_tools(chat.get_tools() + [chatlas_tool])

    def _extract_thinking(self, chat: Any) -> str | None:
        """Extract thinking text from the last assistant turn."""
        turns = chat.get_turns()
        if not turns:
            return None

        # Find the last assistant turn
        for turn in reversed(turns):
            if turn.role == "assistant" and turn.completion is not None:
                # The raw Anthropic Message has content blocks
                completion = turn.completion
                if hasattr(completion, "content"):
                    thinking_parts = []
                    for block in completion.content:
                        if hasattr(block, "type") and block.type == "thinking":
                            thinking_parts.append(block.thinking)
                    if thinking_parts:
                        return "\n".join(thinking_parts)
                break
        return None

    def _extract_conversation(self, chat: Any) -> list[ConversationMessage]:
        """Extract the full conversation from chat turns (excluding tool-result turns)."""
        messages = []
        turns = chat.get_turns()

        for turn in turns:
            if turn.role == "user":
                # Skip tool-result turns (they have ContentToolResult but no meaningful text)
                text = turn.text if hasattr(turn, "text") else ""
                if not text or not text.strip():
                    continue
                messages.append(ConversationMessage(role="user", content=text))
            elif turn.role == "assistant":
                text = turn.text if hasattr(turn, "text") else ""
                # Extract thinking from this specific turn
                thinking = None
                if turn.completion and hasattr(turn.completion, "content"):
                    for block in turn.completion.content:
                        if hasattr(block, "type") and block.type == "thinking":
                            thinking = block.thinking
                            break
                if text:  # Only record turns with actual text content
                    messages.append(
                        ConversationMessage(
                            role="assistant", content=text, thinking=thinking
                        )
                    )

        return messages

    def _evaluate_with_user_model(
        self, scenario: Scenario, assistant_response: str
    ) -> str | None:
        """Ask the user model if steering is needed. Returns steering message or None."""
        user_chat = self._create_chat(
            system_prompt=(
                "You are evaluating whether an AI assistant has correctly completed "
                "a data task. You will be given the original task and the assistant's "
                "response. Decide if the task appears to be done correctly.\n\n"
                "If the assistant has completed the task correctly, respond with exactly: DONE\n"
                "If the assistant needs correction or the task is incomplete, respond with "
                "a brief, specific instruction to guide the assistant. Be direct and actionable."
            ),
            model=self.user_model,
            with_thinking=False,
        )

        eval_prompt = (
            f"TASK: {scenario.task_prompt}\n\n"
            f"ASSISTANT'S RESPONSE:\n{assistant_response}\n\n"
            "Is this task complete and correct? Reply DONE or provide a correction."
        )

        response = user_chat.chat(eval_prompt, echo="none", stream=False)
        result = str(response).strip()

        if result.upper().startswith("DONE"):
            return None  # No steering needed
        return result  # Steering message

    def run_scenario(self, scenario: Scenario, dataset_dir: Path) -> EvalResult:
        """Run a complete eval scenario and return results."""
        self._tool_calls = []
        self._conversation = []
        start_time = time.time()
        steering_count = 0

        # Reset workspace
        ws = self._reset_workspace()

        # Build system prompt
        system_prompt = self._build_system_prompt(scenario, dataset_dir)

        # Create assistant chat and register tools
        chat = self._create_chat(
            system_prompt, self.assistant_model, with_thinking=self.enable_thinking
        )
        self._register_tools(chat)

        try:
            # Initial task prompt
            response = chat.chat(
                scenario.task_prompt,
                echo="none",
                stream=False,
            )
            final_text = str(response)

            # User-model steering loop
            for _ in range(self.max_steering_turns):
                steering_msg = self._evaluate_with_user_model(scenario, final_text)
                if steering_msg is None:
                    break  # User model says task is done

                # Record the steering intervention
                steering_count += 1
                self._conversation.append(
                    ConversationMessage(role="steering", content=steering_msg)
                )

                # Send steering message to assistant
                response = chat.chat(
                    steering_msg,
                    echo="none",
                    stream=False,
                )
                final_text = str(response)

        except Exception as e:
            total_time = time.time() - start_time
            conversation = self._extract_conversation(chat) if chat else []
            return EvalResult(
                scenario_name=scenario.name,
                model=self.assistant_model,
                surface="mcp",
                passed=False,
                tool_calls=self._tool_calls,
                total_turns=len(self._tool_calls),
                total_duration_s=round(total_time, 2),
                error=str(e),
                user_model=self.user_model,
                assistant_model=self.assistant_model,
                conversation=conversation,
                steering_count=steering_count,
            )

        total_time = time.time() - start_time

        # Extract conversation and thinking from all turns
        conversation = self._extract_conversation(chat)

        # Score against assertions
        scorer = Scorer()
        context = {
            "tool_calls": self._tool_calls,
            "final_response": final_text,
        }
        assertion_results = scorer.score(self._workspace, scenario, context)
        all_passed = all(p for p, _ in assertion_results)

        return EvalResult(
            scenario_name=scenario.name,
            model=self.assistant_model,
            surface="mcp",
            passed=all_passed,
            assertion_results=assertion_results,
            tool_calls=self._tool_calls,
            total_turns=len(self._tool_calls),
            total_duration_s=round(total_time, 2),
            final_response=final_text,
            user_model=self.user_model,
            assistant_model=self.assistant_model,
            conversation=conversation,
            steering_count=steering_count,
        )

    def _build_system_prompt(self, scenario: Scenario, dataset_dir: Path) -> str:
        """Build the system prompt for the agent."""
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

TOOL GUIDE — use the most specific tool for each task:
- PII/sensitive data scanning → sweet_detect_pii (not sweet_detect_types)
- Schema contracts (infer rules, then enforce them) → sweet_infer_contract + sweet_enforce_contract
- Split valid/invalid rows after validation → sweet_sundered
- Get automated cleaning suggestions → sweet_suggest
- Detect join keys across tables → sweet_relationships (requires 2+ sheets)
- Generate a reusable pipeline script → sweet_generate_pipeline (not sweet_generate_code)
- Save a versioned snapshot → sweet_commit; view snapshots → sweet_version_log
- Create formatted HTML table → sweet_to_great_table
- Redo a previously undone operation → sweet_redo
"""

    @property
    def workspace(self) -> Workspace | None:
        """Access the workspace after a run (for assertions)."""
        return self._workspace
