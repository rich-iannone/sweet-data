"""MCP surface eval tests — Real LLM agents driving Sweet via MCP tools.

Supports a model matrix architecture:
- User model (claude-sonnet-4-6): evaluates and steers the assistant
- Assistant model (varies): the model being evaluated

Run with:
  pytest evals/test_mcp_evals.py -v                    # Default model
  pytest evals/test_mcp_evals.py --models=claude-sonnet-4-20250514,claude-opus-4-20250514
  pytest evals/test_mcp_evals.py --no-steering         # Disable user-model steering
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.conftest import ASSISTANT_MODELS, SCENARIOS_DIR, USER_MODEL, requires_anthropic
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


def _get_models(config=None) -> list[str]:
    """Get assistant models from CLI option or defaults."""
    if config and config.getoption("--models", None):
        return [m.strip() for m in config.getoption("--models").split(",")]
    return ASSISTANT_MODELS


def _no_steering(config=None) -> bool:
    """Check if steering is disabled."""
    if config:
        return config.getoption("--no-steering", False)
    return False


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
def test_mcp_scenario(scenario: Scenario, datasets_dir: Path, results_dir: Path, request):
    """Run a single eval scenario via MCP surface."""
    models = _get_models(request.config)
    no_steering = _no_steering(request.config)

    for assistant_model in models:
        client = MCPAgentClient(
            assistant_model=assistant_model,
            user_model=USER_MODEL,
            max_turns=scenario.max_turns,
            max_steering_turns=0 if no_steering else 3,
        )

        result = client.run_scenario(scenario, datasets_dir)
        save_results([result], results_dir)
        print_summary([result])

        assert result.passed, (
            f"Scenario '{scenario.name}' FAILED (model: {assistant_model}).\n"
            f"Assertions:\n"
            + "\n".join(
                f"  {'✅' if p else '❌'} {msg}" for p, msg in result.assertion_results
            )
            + f"\nTool calls: {result.total_turns}"
            + f"\nDuration: {result.total_duration_s}s"
            + f"\nSteering count: {result.steering_count}"
            + (f"\nError: {result.error}" if result.error else "")
        )


# ---------------------------------------------------------------------------
# Category-specific test functions (for targeted runs with -k)
# ---------------------------------------------------------------------------


def _run_category_test(
    scenario: Scenario, datasets_dir: Path, results_dir: Path, request
):
    """Shared logic for category-specific tests."""
    models = _get_models(request.config)
    no_steering = _no_steering(request.config)

    for assistant_model in models:
        client = MCPAgentClient(
            assistant_model=assistant_model,
            user_model=USER_MODEL,
            max_turns=scenario.max_turns,
            max_steering_turns=0 if no_steering else 3,
        )
        result = client.run_scenario(scenario, datasets_dir)
        save_results([result], results_dir)
        assert result.passed, f"FAILED: {scenario.name} (model: {assistant_model})"


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
    def test_cleaning(self, scenario: Scenario, datasets_dir: Path, results_dir: Path, request):
        _run_category_test(scenario, datasets_dir, results_dir, request)


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
    def test_eda(self, scenario: Scenario, datasets_dir: Path, results_dir: Path, request):
        _run_category_test(scenario, datasets_dir, results_dir, request)


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
    def test_pipeline(self, scenario: Scenario, datasets_dir: Path, results_dir: Path, request):
        _run_category_test(scenario, datasets_dir, results_dir, request)


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
    def test_error_recovery(self, scenario: Scenario, datasets_dir: Path, results_dir: Path, request):
        _run_category_test(scenario, datasets_dir, results_dir, request)


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
    def test_advanced(self, scenario: Scenario, datasets_dir: Path, results_dir: Path, request):
        _run_category_test(scenario, datasets_dir, results_dir, request)


# ---------------------------------------------------------------------------
# Full suite runner (for `make evals`)
# ---------------------------------------------------------------------------


@pytest.mark.eval
@pytest.mark.slow
@requires_anthropic
def test_full_eval_suite(datasets_dir: Path, results_dir: Path, request):
    """Run ALL scenarios across the model matrix and produce aggregate report."""
    scenarios = _load_scenarios()
    models = _get_models(request.config)
    no_steering = _no_steering(request.config)
    results: list[EvalResult] = []

    for assistant_model in models:
        for scenario in scenarios:
            client = MCPAgentClient(
                assistant_model=assistant_model,
                user_model=USER_MODEL,
                max_turns=scenario.max_turns,
                max_steering_turns=0 if no_steering else 3,
            )
            result = client.run_scenario(scenario, datasets_dir)
            results.append(result)

    # Save all results
    save_results(results, results_dir)
    print_summary(results)

    # Aggregate assertion: >= 60% pass rate per model
    for model in models:
        model_results = [r for r in results if r.assistant_model == model]
        if not model_results:
            continue
        pass_rate = sum(1 for r in model_results if r.passed) / len(model_results)
        assert pass_rate >= 0.6, (
            f"Model {model}: pass rate {pass_rate:.0%} below threshold (60%). "
            f"Passed: {sum(1 for r in model_results if r.passed)}/{len(model_results)}"
        )
