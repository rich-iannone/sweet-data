"""Sweet Agent Runtime — multi-step autonomous data workflows.

This module provides:
- DataAgent: Orchestrates multi-step data tasks with validation and rollback
- Recipe: YAML-defined reusable workflow definitions
- Step / StepResult: Execution units and their outcomes
"""

from .agent import DataAgent, StepResult
from .recipes import Recipe, RecipeRegistry

__all__ = ["DataAgent", "Recipe", "RecipeRegistry", "StepResult"]
