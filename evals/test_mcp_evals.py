"""MCP surface eval tests — Real LLM agents driving Sweet via MCP tools.

These tests make live API calls to Anthropic/OpenAI and require:
- ANTHROPIC_API_KEY in .env
- Network access

Run with: pytest evals/test_mcp_evals.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.conftest import SCENARIOS_DIR, requires_anthropic
from evals.framework import EvalResult, Scenario, print_summary, save_results
from evals.surfaces.mcp_client import MCPAgentClient

# ---------------------------------------------------------------------------
# Load scenarios
# ---------------------------------------------------------------------------


def _load_scenarios(category: str | None = None) -> list[Scenario]:
    """Load all scenarios, optionally filtered by category."""
    scenarios = []
    for yaml_file in SCENARIOS_DIR.glob("*.yaml"):
        loaded = Scenario.from_yaml(yaml_file)
        for s in loaded:
            if category is None or s.category == category:
                scenarios.append(s)
    return scenarios


# ---------------------------------------------------------------------------
# Parametrized test: one test per scenario
# ---------------------------------------------------------------------------


def _scenario_ids() -> list[str]:
    """Generate test IDs from scenario names."""
    return [s.name.replace(" ", "_").lower() for s in _load_scenarios()]


def _all_scenarios() -> list[Scenario]:
    return _load_scenarios()


@pytest.mark.eval
@pytest.mark.slow
@requires_anthropic
@pytest.mark.parametrize("scenario", _all_scenarios(), ids=_scenario_ids())
def test_mcp_scenario(scenario: Scenario, datasets_dir: Path, results_dir: Path):
    """Run a single eval scenario via MCP surface."""
    client = MCPAgentClient(
        model=scenario.models[0],
        max_turns=scenario.max_turns,
    )

    result = client.run_scenario(scenario, datasets_dir)

    # Save individual result
    save_results([result], results_dir)

    # Print summary for visibility
    print_summary([result])

    # Assert: all hard assertions must pass
    assert result.passed, (
        f"Scenario '{scenario.name}' FAILED.\n"
        f"Assertions:\n"
        + "\n".join(f"  {'✅' if p else '❌'} {msg}" for p, msg in result.assertion_results)
        + f"\nTool calls: {result.total_turns}"
        + f"\nDuration: {result.total_duration_s}s"
        + (f"\nError: {result.error}" if result.error else "")
    )


# ---------------------------------------------------------------------------
# Category-specific test functions (for targeted runs with -k)
# ---------------------------------------------------------------------------


@pytest.mark.eval
@pytest.mark.slow
@requires_anthropic
class TestCleaningEvals:
    """Data cleaning scenarios."""

    @pytest.mark.parametrize(
        "scenario",
        _load_scenarios("cleaning"),
        ids=[s.name.replace(" ", "_").lower() for s in _load_scenarios("cleaning")],
    )
    def test_cleaning(self, scenario: Scenario, datasets_dir: Path, results_dir: Path):
        client = MCPAgentClient(model=scenario.models[0], max_turns=scenario.max_turns)
        result = client.run_scenario(scenario, datasets_dir)
        save_results([result], results_dir)
        assert result.passed, f"FAILED: {scenario.name}"


@pytest.mark.eval
@pytest.mark.slow
@requires_anthropic
class TestEDAEvals:
    """Exploratory data analysis scenarios."""

    @pytest.mark.parametrize(
        "scenario",
        _load_scenarios("eda"),
        ids=[s.name.replace(" ", "_").lower() for s in _load_scenarios("eda")],
    )
    def test_eda(self, scenario: Scenario, datasets_dir: Path, results_dir: Path):
        client = MCPAgentClient(model=scenario.models[0], max_turns=scenario.max_turns)
        result = client.run_scenario(scenario, datasets_dir)
        save_results([result], results_dir)
        assert result.passed, f"FAILED: {scenario.name}"


@pytest.mark.eval
@pytest.mark.slow
@requires_anthropic
class TestPipelineEvals:
    """Pipeline building scenarios."""

    @pytest.mark.parametrize(
        "scenario",
        _load_scenarios("pipeline"),
        ids=[s.name.replace(" ", "_").lower() for s in _load_scenarios("pipeline")],
    )
    def test_pipeline(self, scenario: Scenario, datasets_dir: Path, results_dir: Path):
        client = MCPAgentClient(model=scenario.models[0], max_turns=scenario.max_turns)
        result = client.run_scenario(scenario, datasets_dir)
        save_results([result], results_dir)
        assert result.passed, f"FAILED: {scenario.name}"


@pytest.mark.eval
@pytest.mark.slow
@requires_anthropic
class TestErrorRecoveryEvals:
    """Error recovery and ambiguous instruction scenarios."""

    @pytest.mark.parametrize(
        "scenario",
        _load_scenarios("error_recovery"),
        ids=[s.name.replace(" ", "_").lower() for s in _load_scenarios("error_recovery")],
    )
    def test_error_recovery(self, scenario: Scenario, datasets_dir: Path, results_dir: Path):
        client = MCPAgentClient(model=scenario.models[0], max_turns=scenario.max_turns)
        result = client.run_scenario(scenario, datasets_dir)
        save_results([result], results_dir)
        assert result.passed, f"FAILED: {scenario.name}"


@pytest.mark.eval
@pytest.mark.slow
@requires_anthropic
class TestAdvancedEvals:
    """Advanced multi-step workflow scenarios."""

    @pytest.mark.parametrize(
        "scenario",
        _load_scenarios("advanced"),
        ids=[s.name.replace(" ", "_").lower() for s in _load_scenarios("advanced")],
    )
    def test_advanced(self, scenario: Scenario, datasets_dir: Path, results_dir: Path):
        client = MCPAgentClient(model=scenario.models[0], max_turns=scenario.max_turns)
        result = client.run_scenario(scenario, datasets_dir)
        save_results([result], results_dir)
        assert result.passed, f"FAILED: {scenario.name}"


# ---------------------------------------------------------------------------
# Full suite runner (for `make evals`)
# ---------------------------------------------------------------------------


@pytest.mark.eval
@pytest.mark.slow
@requires_anthropic
def test_full_eval_suite(datasets_dir: Path, results_dir: Path):
    """Run ALL scenarios and produce aggregate report."""
    scenarios = _load_scenarios()
    results: list[EvalResult] = []

    for scenario in scenarios:
        client = MCPAgentClient(model=scenario.models[0], max_turns=scenario.max_turns)
        result = client.run_scenario(scenario, datasets_dir)
        results.append(result)

    # Save all results
    save_results(results, results_dir)
    print_summary(results)

    # Aggregate assertion: >= 60% pass rate (will raise to 80% once stable)
    pass_rate = sum(1 for r in results if r.passed) / len(results) if results else 0.0
    assert pass_rate >= 0.6, (
        f"Pass rate {pass_rate:.0%} below threshold (60%). "
        f"Passed: {sum(1 for r in results if r.passed)}/{len(results)}"
    )
