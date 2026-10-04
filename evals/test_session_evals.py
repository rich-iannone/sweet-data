"""Session surface eval tests: real LLM agents working through `sweet mcp`'s slim tools.

Scenarios with `surface: session` run here, each under its own policy (agent mode,
masks). Results include surface metrics (tool schema size, tool result characters)
for comparing token efficiency with the legacy tool surface.

Run with:
  pytest evals/test_session_evals.py -v
  pytest evals/test_session_evals.py --models=claude-sonnet-4-6 --no-steering
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.conftest import ASSISTANT_MODELS, SCENARIOS_DIR, USER_MODEL, requires_anthropic
from evals.framework import Scenario, print_summary, save_results
from evals.surfaces.session_client import SessionAgentClient


def _session_scenarios() -> list[Scenario]:
    scenarios = []
    for yaml_file in sorted(SCENARIOS_DIR.glob("*.yaml")):
        scenarios += [s for s in Scenario.from_yaml(yaml_file) if s.surface == "session"]
    return scenarios


def _models(config) -> list[str]:
    if config.getoption("--models", None):
        return [m.strip() for m in config.getoption("--models").split(",")]
    return ASSISTANT_MODELS


@pytest.mark.eval
@pytest.mark.slow
@requires_anthropic
@pytest.mark.parametrize(
    "scenario",
    _session_scenarios(),
    ids=[s.name.replace(" ", "_").lower() for s in _session_scenarios()],
)
def test_session_scenario(scenario: Scenario, datasets_dir: Path, results_dir: Path, request):
    no_steering = request.config.getoption("--no-steering", False)
    for model in _models(request.config):
        client = SessionAgentClient(
            assistant_model=model,
            user_model=USER_MODEL,
            max_turns=scenario.max_turns,
            max_steering_turns=0 if no_steering else 3,
        )
        result = client.run_scenario(scenario, datasets_dir)
        save_results([result], results_dir)
        print_summary([result])
        print(f"  metrics: {result.metrics}")
        assert result.passed, "\n".join(
            [f"Scenario '{scenario.name}' FAILED (model: {model})"]
            + [f"  {'✅' if p else '❌'} {msg}" for p, msg in result.assertion_results]
            + ([f"Error: {result.error}"] if result.error else [])
        )
