"""Recipe system for reusable agent workflows.

Recipes are YAML-defined sequences of steps that can be shared,
versioned, and parameterized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Recipe:
    """A reusable workflow definition.

    Attributes:
        name: Human-readable recipe name.
        description: What this recipe does.
        steps: Ordered list of step names to execute.
        default_params: Default parameter values.
        checkpoints: Step indices where the agent should pause.
        tags: Categorization tags.
    """

    name: str
    description: str = ""
    steps: list[str] = field(default_factory=list)
    default_params: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[int] | None = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Recipe":
        """Load a recipe from a YAML file.

        Args:
            path: Path to the YAML recipe file.

        Returns:
            Parsed Recipe instance.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            ValueError: If the YAML is invalid.
        """
        from yaml12 import read_yaml

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Recipe file not found: {path}")

        data = read_yaml(path)

        if not isinstance(data, dict):
            raise ValueError(f"Recipe file must be a YAML mapping: {path}")

        if "name" not in data:
            raise ValueError(f"Recipe must have a 'name' field: {path}")

        if "steps" not in data or not isinstance(data["steps"], list):
            raise ValueError(f"Recipe must have a 'steps' list: {path}")

        # Parse parameters
        params = {}
        for param_def in data.get("parameters", []):
            if isinstance(param_def, dict) and "name" in param_def:
                params[param_def["name"]] = param_def.get("default")

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            steps=data["steps"],
            default_params=params,
            checkpoints=data.get("checkpoints"),
            tags=data.get("tags", []),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recipe":
        """Create a Recipe from a dictionary.

        Args:
            data: Dict with keys: name, description, steps, parameters, etc.

        Returns:
            Recipe instance.
        """
        params = {}
        for param_def in data.get("parameters", []):
            if isinstance(param_def, dict) and "name" in param_def:
                params[param_def["name"]] = param_def.get("default")

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            steps=data["steps"],
            default_params=params,
            checkpoints=data.get("checkpoints"),
            tags=data.get("tags", []),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize recipe to dictionary."""
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
        }
        if self.default_params:
            d["parameters"] = [
                {"name": k, "default": v} for k, v in self.default_params.items()
            ]
        if self.checkpoints:
            d["checkpoints"] = self.checkpoints
        if self.tags:
            d["tags"] = self.tags
        return d


# Built-in recipes
_BUILTIN_RECIPES: dict[str, Recipe] = {
    "clean-csv": Recipe(
        name="Standard CSV Cleaning",
        description="Clean a raw CSV file: cast types, remove duplicates, standardize nulls, trim whitespace.",
        steps=[
            "detect_and_cast_types",
            "remove_full_duplicates",
            "standardize_nulls",
            "trim_whitespace",
            "drop_all_null_rows",
            "validate",
            "generate_report",
        ],
        tags=["cleaning", "csv"],
    ),
    "quality-check": Recipe(
        name="Data Quality Check",
        description="Run a comprehensive quality assessment: detect types, find outliers, validate.",
        steps=[
            "detect_and_cast_types",
            "detect_outliers",
            "validate",
            "generate_report",
        ],
        tags=["quality", "profiling"],
    ),
    "prepare-export": Recipe(
        name="Prepare for Export",
        description="Clean and validate data before exporting: trim, dedupe, validate, report.",
        steps=[
            "trim_whitespace",
            "remove_full_duplicates",
            "drop_all_null_columns",
            "validate",
            "generate_report",
        ],
        tags=["export", "cleaning"],
    ),
}


class RecipeRegistry:
    """Registry of available recipes (built-in and user-defined).

    Provides discovery and loading of recipes from the built-in set
    and from a user-specified directory.
    """

    def __init__(self, recipe_dir: str | Path | None = None) -> None:
        """Initialize the registry.

        Args:
            recipe_dir: Optional directory to scan for .yaml recipe files.
        """
        self._recipes: dict[str, Recipe] = dict(_BUILTIN_RECIPES)
        self._recipe_dir = Path(recipe_dir) if recipe_dir else None

        if self._recipe_dir and self._recipe_dir.is_dir():
            self._load_from_directory(self._recipe_dir)

    def _load_from_directory(self, directory: Path) -> None:
        """Load all .yaml files from a directory as recipes."""
        for yaml_file in sorted(directory.glob("*.yaml")):
            try:
                recipe = Recipe.from_yaml(yaml_file)
                key = yaml_file.stem
                self._recipes[key] = recipe
            except (ValueError, FileNotFoundError):
                continue

    def get(self, name: str) -> Recipe | None:
        """Get a recipe by name.

        Args:
            name: Recipe key (e.g., "clean-csv").

        Returns:
            Recipe if found, None otherwise.
        """
        return self._recipes.get(name)

    def list(self) -> list[dict[str, Any]]:
        """List all available recipes.

        Returns:
            List of dicts with name, description, steps count, tags.
        """
        return [
            {
                "key": key,
                "name": recipe.name,
                "description": recipe.description,
                "n_steps": len(recipe.steps),
                "steps": recipe.steps,
                "tags": recipe.tags,
            }
            for key, recipe in sorted(self._recipes.items())
        ]

    def register(self, key: str, recipe: Recipe) -> None:
        """Register a recipe.

        Args:
            key: Short key for the recipe.
            recipe: Recipe instance.
        """
        self._recipes[key] = recipe
