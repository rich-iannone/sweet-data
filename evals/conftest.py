"""Pytest configuration for Sweet evals.

These tests require:
- API keys in .env (ANTHROPIC_API_KEY and/or OPENAI_API_KEY)
- Network access for LLM calls
- The 'evals' optional dependency group

Run with: pytest evals/ -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


# ---------------------------------------------------------------------------
# Marks and skip conditions
# ---------------------------------------------------------------------------


# Skip all evals if no API keys are available
def _has_anthropic_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _has_openai_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


requires_anthropic = pytest.mark.skipif(
    not _has_anthropic_key(),
    reason="ANTHROPIC_API_KEY not set",
)

requires_openai = pytest.mark.skipif(
    not _has_openai_key(),
    reason="OPENAI_API_KEY not set",
)

requires_any_llm = pytest.mark.skipif(
    not (_has_anthropic_key() or _has_openai_key()),
    reason="No LLM API key available (need ANTHROPIC_API_KEY or OPENAI_API_KEY)",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EVALS_DIR = Path(__file__).parent
DATASETS_DIR = EVALS_DIR / "datasets"
SCENARIOS_DIR = EVALS_DIR / "scenarios"
RESULTS_DIR = EVALS_DIR / "results"


@pytest.fixture
def datasets_dir() -> Path:
    """Path to eval datasets directory."""
    return DATASETS_DIR


@pytest.fixture
def scenarios_dir() -> Path:
    """Path to eval scenarios directory."""
    return SCENARIOS_DIR


@pytest.fixture
def results_dir() -> Path:
    """Path to eval results directory."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


@pytest.fixture
def mcp_client():
    """Create a fresh MCP agent client."""
    from evals.surfaces.mcp_client import MCPAgentClient

    return MCPAgentClient(
        model="claude-sonnet-4-20250514",
        max_turns=20,
    )


# ---------------------------------------------------------------------------
# Pytest configuration
# ---------------------------------------------------------------------------


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "eval: mark test as an agent eval (requires API keys)")
    config.addinivalue_line("markers", "slow: mark test as slow (LLM round-trips)")
    config.addinivalue_line(
        "markers", "category(name): mark eval with a category (cleaning, eda, pipeline, etc.)"
    )
