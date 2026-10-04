"""Session Agent Client — the eval loop over the slim `sweet mcp` tool surface.

Same two-model loop as `MCPAgentClient`, but the agent works through the tools
agents get from `sweet mcp` (a headless `Session` in-process), under the
scenario's policy (agent mode, masks, PII masking). Records surface metrics:
tool schema size and the characters returned by tools, for comparing token
efficiency with the legacy surface.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from chatlas import Tool

from sweet.core.policy import Policy
from sweet.core.session import Session
from sweet.session.mcp import TOOLS, LocalBackend, SweetMCP

from ..framework import EvalResult, Scenario, ToolCall
from .mcp_client import MCPAgentClient

# No viewer is attached in evals, so UI-only tools are left out
SESSION_EVAL_TOOLS = {t.name for t in TOOLS} - {
    "launch_session",
    "screen",
    "command",
    "demo",
    "get_selection",
}


class SessionAgentClient(MCPAgentClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("tool_subset", SESSION_EVAL_TOOLS)
        super().__init__(*args, **kwargs)
        self._mcp: SweetMCP | None = None
        self._scenario: Scenario | None = None
        self._result_chars = 0

    def _reset_workspace(self):
        spec = dict(self._scenario.policy) if self._scenario else {}
        policy = Policy.from_dict({"mode": spec.get("mode", "auto"), **spec})
        session = Session(policy=policy)
        self._mcp = SweetMCP(LocalBackend(session, "agent:eval"))
        self._workspace = session.workspace
        self._result_chars = 0
        return self._workspace

    def _make_tool_func(self, tool_name: str) -> Any:
        def tool_func(**kwargs: Any) -> str:
            start = time.time()
            try:
                result = asyncio.run(self._mcp.call_tool(tool_name, kwargs))
                text = result[0].text if result else "No result"
            except Exception as e:
                text = f"Error: {e}"
            self._result_chars += len(text)
            # Keep full results: no_leak assertions scan everything the agent saw
            self._tool_calls.append(
                ToolCall(
                    tool_name=tool_name,
                    arguments=kwargs,
                    result=text,
                    duration_s=round(time.time() - start, 3),
                )
            )
            return text

        return tool_func

    def _register_tools(self, chat: Any) -> None:
        for mcp_tool in TOOLS:
            if mcp_tool.name not in self.tool_subset:
                continue
            chat.set_tools(
                chat.get_tools()
                + [
                    Tool(
                        func=self._make_tool_func(mcp_tool.name),
                        name=mcp_tool.name,
                        description=mcp_tool.description,
                        parameters=mcp_tool.inputSchema,
                    )
                ]
            )

    def run_scenario(self, scenario: Scenario, dataset_dir: Path) -> EvalResult:
        self._scenario = scenario
        result = super().run_scenario(scenario, dataset_dir)
        result.surface = "session"
        schema = [t.model_dump() for t in TOOLS if t.name in self.tool_subset]
        result.metrics = {
            "tool_schema_chars": len(json.dumps(schema)),
            "tool_result_chars": self._result_chars,
            "tool_calls": len(result.tool_calls),
        }
        return result

    def _build_system_prompt(self, scenario: Scenario, dataset_dir: Path) -> str:
        dataset_path = dataset_dir / scenario.dataset
        return f"""You are a data engineer working in Sweet, a data workspace, through its tools.

- The dataset is at: {dataset_path}. Start with `status`, then `open` it.
- Look before changing anything: `sheets`, `profile`, `view`, and read-only `query`.
- Change data only with `propose_step`, using structured steps. Prefer SQL expressions,
  e.g. {{"kind": "filter", "params": {{"sql": "amount > 0"}}}}.
- Check each step's preview. If a result is wrong, use `undo` (or `steps` to toggle or
  remove a step) and try again.
- Masked columns show as •••, h:... hashes, or partial values. Never try to reveal or infer
  masked values. If asked to protect personal data, use `set_policy` before looking.
- When you're done, reply with a brief summary of what you did and found.
"""
