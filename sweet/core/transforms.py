"""Transform step models and utilities for Sweet."""

import hashlib
from dataclasses import dataclass
from typing import Any

try:
    import polars as pl
except ImportError:
    pl = None


@dataclass
class TransformStep:
    """Represents a single transformation step in a data pipeline.

    Attributes:
        expr: Python expression to apply (e.g., "df.filter(pl.col('age') > 30)")
        input_hash: Hash of input data for dependency tracking
        output_schema: Schema snapshot after transformation
        metadata: Additional metadata about the transformation
    """

    expr: str
    input_hash: str
    output_schema: dict[str, str]
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Initialize metadata if not provided."""
        if self.metadata is None:
            self.metadata = {}


def apply_expr(
    df: "pl.DataFrame", expr: str, extra_cells: dict[str, str] | None = None
) -> "pl.DataFrame":
    """Apply a Python expression to a Polars DataFrame.

    Args:
        df: Input DataFrame
        expr: Python expression string
        extra_cells: Additional variables to include in context

    Returns:
        Transformed DataFrame

    Raises:
        ValueError: If expression is invalid or unsafe
        ImportError: If polars is not available
    """
    if pl is None:
        raise ImportError("Polars is required but not installed")

    if extra_cells is None:
        extra_cells = {}

    # Create controlled context for evaluation
    context = {
        "df": df,
        "pl": pl,
        **extra_cells,
    }

    # Validate expression safety (basic check)
    dangerous_keywords = ["import", "exec", "eval", "__", "open", "file"]
    if any(keyword in expr for keyword in dangerous_keywords):
        raise ValueError(
            f"Expression contains potentially dangerous operations: {expr}"
        )

    try:
        # Execute the expression
        result = eval(expr, {"__builtins__": {}}, context)

        # Ensure result is a DataFrame
        if not isinstance(result, pl.DataFrame):
            raise ValueError(f"Expression must return a DataFrame, got {type(result)}")

        return result
    except Exception as e:
        raise ValueError(f"Failed to apply expression '{expr}': {str(e)}") from e


def generate_polars_code(steps: list[TransformStep]) -> str:
    """Generate Polars code from a list of transformation steps.

    Args:
        steps: List of transformation steps

    Returns:
        Generated Python code string
    """
    if not steps:
        return "import polars as pl\n# No transformations applied"

    code_lines = ["import polars as pl", ""]

    for i, step in enumerate(steps):
        # Add comment with step info
        code_lines.append(
            f"# Step {i + 1}: {step.metadata.get('description', 'Transform')}"
        )
        code_lines.append(f"# Schema: {step.output_schema}")

        # Add the transformation
        if step.expr.startswith("df = "):
            code_lines.append(step.expr)
        else:
            code_lines.append(f"df = {step.expr}")

        code_lines.append("")

    return "\n".join(code_lines)


def compute_dataframe_hash(df: "pl.DataFrame") -> str:
    """Compute a hash of a DataFrame for dependency tracking.

    Args:
        df: DataFrame to hash

    Returns:
        Hash string

    Raises:
        ImportError: If polars is not available
    """
    if pl is None:
        raise ImportError("Polars is required but not installed")

    # Create a hash based on schema and first few rows
    schema_str = str(df.schema)

    # Sample a few rows for content hash (to avoid hashing large datasets)
    sample_size = min(100, df.height)
    if sample_size > 0:
        sample_data = df.head(sample_size).to_pandas().to_string()
    else:
        sample_data = ""

    content = f"{schema_str}:{sample_data}:{df.shape}"
    return hashlib.md5(content.encode()).hexdigest()


def validate_expression(expr: str) -> bool:
    """Validate if an expression is safe to execute.

    Args:
        expr: Expression to validate

    Returns:
        True if expression appears safe
    """
    # Basic safety checks
    dangerous_patterns = [
        "__import__",
        "exec(",
        "eval(",
        "open(",
        "file(",
        "input(",
        "raw_input(",
        "compile(",
        "globals(",
        "locals(",
        "vars(",
        "dir(",
        "setattr(",
        "getattr(",
        "delattr(",
        "hasattr(",
    ]

    expr_lower = expr.lower()
    return not any(pattern in expr_lower for pattern in dangerous_patterns)
