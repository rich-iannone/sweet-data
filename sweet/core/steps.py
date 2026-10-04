"""Steps: the unit of change in Sweet.

Every change to a sheet (a filter, a cast, a cell edit, a free-form Polars or
SQL snippet) is a `Step`: a small, serializable record of *what* to do. A
registered `StepType` knows *how* to do it, and how to render it as Polars code
and (where the operation is order-independent) as DuckDB SQL.

Structured steps call the Polars API directly instead of evaluating strings.
Expression parameters may be written in either language:

- ``expr``: a Polars expression, e.g. ``pl.col("revenue") > 0``
- ``sql``:  a SQL expression, e.g. ``revenue > 0`` (applied via ``pl.sql_expr``)

Steps written with ``sql`` expressions can be exported to both Polars and SQL.
"""

from __future__ import annotations

import ast
import builtins
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

import polars as pl

from .registry import Registry


class StepError(ValueError):
    """Raised when a step has invalid parameters or fails to apply."""


class NotExportable(StepError):
    """Raised when a step can't be rendered in the requested language."""


# -----------------------------------------------------------------------------
# The Step record
# -----------------------------------------------------------------------------


@dataclass
class Step:
    """A serializable description of one change to a sheet.

    Attributes:
        kind: Registered step type name (e.g. "filter", "cast", "sql").
        params: Parameters for the step type.
        id: Stable identifier, unique within a pipeline.
        author: Who created the step ("human", "agent:<name>", ...).
        enabled: Disabled steps are kept in the pipeline but skipped.
        description: Optional human-readable label. Defaults to the step
            type's own description of `params`.
    """

    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    author: str = "human"
    enabled: bool = True
    description: str = ""

    @property
    def type(self) -> StepType:
        return STEP_TYPES.get(self.kind)

    @property
    def stateful(self) -> bool:
        """Whether the step needs to see all rows at once (vs. row-by-row)."""
        return self.type.stateful

    @property
    def label(self) -> str:
        return self.description or self.type.describe(self.params)

    def validate(self) -> None:
        self.type.validate(self.params)

    def apply(self, df: Frame) -> Frame:
        """Apply to a DataFrame or LazyFrame (returning the same kind).

        Step types that can't run lazily collect the LazyFrame first.
        """
        self.validate()
        try:
            if isinstance(df, pl.LazyFrame) and not self.type.lazy:
                return self.type.apply(df.collect(), self.params).lazy()
            result = self.type.apply(df, self.params)
            if isinstance(result, pl.LazyFrame):
                result.collect_schema()  # Surface plan errors now, not at first fetch
            return result
        except StepError:
            raise
        except Exception as e:
            raise StepError(f"Step '{self.label}' failed: {e}") from e

    def to_polars(self) -> str:
        """Render as a Polars expression producing the new `df`."""
        return self.type.to_polars(self.params)

    def to_polars_statements(self) -> str:
        """Render as Python statement(s) that update `df`."""
        return self.type.to_polars_statements(self.params)

    def to_sql(self, input_ref: str, columns: list[str] | None = None) -> str:
        """Render as a DuckDB query reading from `input_ref`.

        Raises:
            NotExportable: If this step has no SQL equivalent.
        """
        sql = self.type.to_sql(self.params, input_ref, columns)
        if sql is None:
            raise NotExportable(
                f"Step '{self.label}' ({self.kind}) can't be exported to SQL. "
                f"Rewrite it with a SQL expression or export the pipeline as Polars."
            )
        return sql

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "kind": self.kind, "params": self.params}
        if self.description:
            d["description"] = self.description
        if self.author != "human":
            d["author"] = self.author
        if not self.enabled:
            d["enabled"] = False
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Step:
        if "kind" not in d:
            raise StepError(f"Step is missing 'kind': {d!r}")
        kwargs: dict[str, Any] = {
            "kind": d["kind"],
            "params": dict(d.get("params") or {}),
            "author": d.get("author", "human"),
            "enabled": d.get("enabled", True),
            "description": d.get("description", ""),
        }
        if d.get("id"):
            kwargs["id"] = str(d["id"])
        return cls(**kwargs)


# -----------------------------------------------------------------------------
# Step types
# -----------------------------------------------------------------------------


class StepType:
    """Base class for step implementations. Subclasses register via `@step_type`."""

    kind: ClassVar[str] = ""
    stateful: ClassVar[bool] = False
    #: Whether `apply` works on a LazyFrame (otherwise the frame is collected first).
    lazy: ClassVar[bool] = True
    required: ClassVar[tuple[str, ...]] = ()

    def validate(self, params: dict[str, Any]) -> None:
        missing = [k for k in self.required if k not in params]
        if missing:
            raise StepError(f"'{self.kind}' step is missing parameter(s): {', '.join(missing)}")

    def apply(self, df: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        raise NotImplementedError

    def to_polars(self, params: dict[str, Any]) -> str:
        raise NotImplementedError

    def to_polars_statements(self, params: dict[str, Any]) -> str:
        return f"df = {self.to_polars(params)}"

    def to_sql(
        self, params: dict[str, Any], input_ref: str, columns: list[str] | None
    ) -> str | None:
        return None

    def describe(self, params: dict[str, Any]) -> str:
        return self.kind


STEP_TYPES: Registry[StepType] = Registry("step type")


def step_type(kind: str):
    """Class decorator registering a `StepType` subclass under `kind`."""

    def _register(cls: type[StepType]) -> type[StepType]:
        cls.kind = kind
        STEP_TYPES.register(kind, cls())
        return cls

    return _register


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

_SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len", "list",
        "max", "min", "range", "round", "set", "sorted", "str", "sum", "tuple", "zip",
    )
}  # fmt: skip


def _check_expression_source(code: str) -> ast.Expression:
    """Parse `code` as a single expression and reject dunder/private access."""
    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError as e:
        raise StepError(f"Invalid expression {code!r}: {e.msg}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise StepError(f"Access to private attribute '{node.attr}' is not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise StepError(f"Access to private name '{node.id}' is not allowed")
    return tree


def eval_polars_expr(code: str) -> pl.Expr:
    """Evaluate a Polars expression string in a restricted namespace."""
    tree = _check_expression_source(code)
    try:
        result = eval(compile(tree, "<expr>", "eval"), {"__builtins__": _SAFE_BUILTINS}, {"pl": pl})
    except Exception as e:
        raise StepError(f"Could not evaluate expression {code!r}: {e}") from e
    if not isinstance(result, pl.Expr):
        raise StepError(f"Expected a Polars expression, got {type(result).__name__}: {code!r}")
    return result


def _expr_param(params: dict[str, Any], kind: str) -> pl.Expr:
    if "sql" in params:
        return pl.sql_expr(params["sql"])
    if "expr" in params:
        return eval_polars_expr(params["expr"])
    raise StepError(f"'{kind}' step needs an 'expr' (Polars) or 'sql' parameter")


def _expr_code(params: dict[str, Any]) -> str:
    if "sql" in params:
        return f"pl.sql_expr({json.dumps(params['sql'])})"
    return params["expr"]


def _expr_text(params: dict[str, Any]) -> str:
    return params.get("sql") or params.get("expr", "")


def _py(value: Any) -> str:
    """Render a scalar as Python source (strings with double quotes)."""
    if isinstance(value, str):
        return json.dumps(value)
    return repr(value)


def _cols_py(columns: list[str]) -> str:
    return "[" + ", ".join(json.dumps(c) for c in columns) + "]"


def q(name: str) -> str:
    """Quote a SQL identifier."""
    return '"' + str(name).replace('"', '""') + '"'


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _columns(params: dict[str, Any], key: str = "columns") -> list[str]:
    cols = params.get(key)
    if isinstance(cols, str):
        return [cols]
    if not cols:
        raise StepError(f"Parameter '{key}' must name at least one column")
    return list(cols)


Frame = pl.DataFrame | pl.LazyFrame


def frame_schema(frame: Frame) -> pl.Schema:
    """A frame's schema (resolving a LazyFrame's plan if needed)."""
    return frame.collect_schema() if isinstance(frame, pl.LazyFrame) else frame.schema


def _require_columns(df: Frame, columns: list[str]) -> None:
    names = frame_schema(df).names()
    missing = [c for c in columns if c not in names]
    if missing:
        raise StepError(f"Column(s) not found: {', '.join(missing)}")


# Polars dtype names <-> DuckDB types, for `cast`.
_DTYPE_ALIASES: dict[str, str] = {
    "str": "String", "string": "String", "utf8": "String", "text": "String",
    "int": "Int64", "integer": "Int64", "float": "Float64", "double": "Float64",
    "bool": "Boolean", "boolean": "Boolean", "date": "Date", "datetime": "Datetime",
    "time": "Time", "categorical": "Categorical",
}  # fmt: skip

_SQL_TYPES: dict[str, str] = {
    "Int8": "TINYINT", "Int16": "SMALLINT", "Int32": "INTEGER", "Int64": "BIGINT",
    "UInt8": "UTINYINT", "UInt16": "USMALLINT", "UInt32": "UINTEGER", "UInt64": "UBIGINT",
    "Float32": "FLOAT", "Float64": "DOUBLE", "String": "VARCHAR", "Utf8": "VARCHAR",
    "Boolean": "BOOLEAN", "Date": "DATE", "Datetime": "TIMESTAMP", "Time": "TIME",
}  # fmt: skip


def normalize_dtype_name(name: str) -> str:
    """Map user-friendly names ("int", "str", "date") to Polars dtype names."""
    name = str(name).strip()
    if name.startswith("pl."):
        name = name[3:]
    return _DTYPE_ALIASES.get(name.lower(), name)


def parse_dtype(name: str) -> pl.DataType:
    """Resolve a dtype name like "Int64" or "date" to a Polars dtype."""
    normalized = normalize_dtype_name(name)
    dtype = getattr(pl, normalized, None)
    if dtype is None or not (
        isinstance(dtype, pl.DataType)
        or (isinstance(dtype, type) and issubclass(dtype, pl.DataType))
    ):
        raise StepError(f"Unknown data type '{name}'")
    return dtype


def dtype_name(dtype: pl.DataType) -> str:
    """The base name of a Polars dtype (e.g. Datetime(time_unit='us') -> 'Datetime')."""
    return dtype.base_type().__name__ if hasattr(dtype, "base_type") else str(dtype)


# -----------------------------------------------------------------------------
# Built-in step types
# -----------------------------------------------------------------------------


@step_type("filter")
class FilterStep(StepType):
    """Keep rows matching a condition. Params: `expr` or `sql`."""

    def apply(self, df, params):
        return df.filter(_expr_param(params, self.kind))

    def to_polars(self, params):
        return f"df.filter({_expr_code(params)})"

    def to_sql(self, params, input_ref, columns):
        if "sql" not in params:
            return None
        return f"SELECT * FROM {input_ref} WHERE {params['sql']}"

    def describe(self, params):
        return f"Filter: {_expr_text(params)}"


@step_type("sort")
class SortStep(StepType):
    """Sort rows (stable). Params: `columns`, optional `descending` (bool or list of
    bools) and `nulls_last` (default False)."""

    stateful = True
    required = ("columns",)

    def apply(self, df, params):
        cols = _columns(params)
        _require_columns(df, cols)
        return df.sort(
            cols,
            descending=params.get("descending", False),
            nulls_last=params.get("nulls_last", False),
            maintain_order=True,
        )

    def to_polars(self, params):
        extra = ", nulls_last=True" if params.get("nulls_last") else ""
        return (
            f"df.sort({_cols_py(_columns(params))}, "
            f"descending={params.get('descending', False)}{extra})"
        )

    def to_sql(self, params, input_ref, columns):
        cols = _columns(params)
        desc = params.get("descending", False)
        flags = desc if isinstance(desc, list) else [desc] * len(cols)
        nulls = " NULLS LAST" if params.get("nulls_last") else " NULLS FIRST"
        order = ", ".join(f"{q(c)}{' DESC' if d else ''}{nulls}" for c, d in zip(cols, flags))
        return f"SELECT * FROM {input_ref} ORDER BY {order}"

    def describe(self, params):
        direction = "desc" if params.get("descending") else "asc"
        return f"Sort by: {', '.join(_columns(params))} ({direction})"


@step_type("select")
class SelectStep(StepType):
    """Keep only the given columns, in order. Params: `columns`."""

    required = ("columns",)

    def apply(self, df, params):
        cols = _columns(params)
        _require_columns(df, cols)
        return df.select(cols)

    def to_polars(self, params):
        return f"df.select({_cols_py(_columns(params))})"

    def to_sql(self, params, input_ref, columns):
        return f"SELECT {', '.join(q(c) for c in _columns(params))} FROM {input_ref}"

    def describe(self, params):
        return f"Select columns: {', '.join(_columns(params))}"


@step_type("drop")
class DropStep(StepType):
    """Remove columns. Params: `columns`."""

    required = ("columns",)

    def apply(self, df, params):
        cols = _columns(params)
        _require_columns(df, cols)
        return df.drop(cols)

    def to_polars(self, params):
        return f"df.drop({_cols_py(_columns(params))})"

    def to_sql(self, params, input_ref, columns):
        return f"SELECT * EXCLUDE ({', '.join(q(c) for c in _columns(params))}) FROM {input_ref}"

    def describe(self, params):
        return f"Drop columns: {', '.join(_columns(params))}"


@step_type("rename")
class RenameStep(StepType):
    """Rename columns. Params: `mapping` ({old: new})."""

    required = ("mapping",)

    def apply(self, df, params):
        mapping = dict(params["mapping"])
        _require_columns(df, list(mapping))
        return df.rename(mapping)

    def to_polars(self, params):
        return f"df.rename({json.dumps(dict(params['mapping']))})"

    def to_sql(self, params, input_ref, columns):
        renames = ", ".join(f"{q(a)} AS {q(b)}" for a, b in params["mapping"].items())
        return f"SELECT * RENAME ({renames}) FROM {input_ref}"

    def describe(self, params):
        return "Rename: " + ", ".join(f"{a} → {b}" for a, b in params["mapping"].items())


@step_type("cast")
class CastStep(StepType):
    """Change column types. Params: `columns` ({name: dtype}), optional `strict` (default True)."""

    required = ("columns",)

    def validate(self, params):
        super().validate(params)
        if not isinstance(params["columns"], dict) or not params["columns"]:
            raise StepError("'cast' step needs 'columns' as a mapping of column -> type")
        for dtype in params["columns"].values():
            parse_dtype(dtype)

    def apply(self, df, params):
        mapping = params["columns"]
        _require_columns(df, list(mapping))
        strict = params.get("strict", True)
        return df.with_columns(
            [pl.col(c).cast(parse_dtype(t), strict=strict) for c, t in mapping.items()]
        )

    def to_polars(self, params):
        strict = params.get("strict", True)
        suffix = "" if strict else ", strict=False"
        casts = ", ".join(
            f"pl.col({json.dumps(c)}).cast(pl.{normalize_dtype_name(t)}{suffix})"
            for c, t in params["columns"].items()
        )
        return f"df.with_columns([{casts}])"

    def to_sql(self, params, input_ref, columns):
        fn = "CAST" if params.get("strict", True) else "TRY_CAST"
        parts = []
        for c, t in params["columns"].items():
            sql_type = _SQL_TYPES.get(normalize_dtype_name(t))
            if sql_type is None:
                return None
            parts.append(f"{fn}({q(c)} AS {sql_type}) AS {q(c)}")
        return f"SELECT * REPLACE ({', '.join(parts)}) FROM {input_ref}"

    def describe(self, params):
        return "Cast: " + ", ".join(
            f"{c} → {normalize_dtype_name(t)}" for c, t in params["columns"].items()
        )


@step_type("mutate")
class MutateStep(StepType):
    """Add or replace a column. Params: `column`, plus `expr` or `sql`.

    Optional `position` (0-based) places a *new* column at that index instead of
    at the end; it's an error if the column already exists.
    """

    required = ("column",)

    def apply(self, df, params):
        expr = _expr_param(params, self.kind).alias(params["column"])
        position = params.get("position")
        if position is None:
            return df.with_columns(expr)
        names = frame_schema(df).names()
        if params["column"] in names:
            raise StepError(f"Column '{params['column']}' already exists")
        position = min(int(position), len(names))
        order = [*names[:position], params["column"], *names[position:]]
        return df.with_columns(expr).select(order)

    def to_polars(self, params):
        expr = f"({_expr_code(params)}).alias({json.dumps(params['column'])})"
        if params.get("position") is None:
            return f"df.with_columns({expr})"
        return f"df.clone().insert_column({int(params['position'])}, {expr})"

    def to_sql(self, params, input_ref, columns):
        if "sql" not in params:
            return None
        col = params["column"]
        position = params.get("position")
        if position is not None:
            if columns is None:
                return None
            names = [q(c) for c in columns]
            names.insert(min(int(position), len(names)), f"{params['sql']} AS {q(col)}")
            return f"SELECT {', '.join(names)} FROM {input_ref}"
        if columns is not None and col in columns:
            return f"SELECT * REPLACE ({params['sql']} AS {q(col)}) FROM {input_ref}"
        return f"SELECT *, {params['sql']} AS {q(col)} FROM {input_ref}"

    def describe(self, params):
        return f"Mutate: {params['column']} = {_expr_text(params)}"


@step_type("edit_cell")
class EditCellStep(StepType):
    """Set one cell. Params: `row` (0-based position), `column`, `value`, optional `dtype`.

    Positional, so it has no SQL rendering (SQL relations are unordered).
    """

    required = ("row", "column", "value")

    def apply(self, df, params):
        row, col, value = int(params["row"]), params["column"], params["value"]
        _require_columns(df, [col])
        if isinstance(df, pl.DataFrame) and not 0 <= row < df.height:
            raise StepError(f"Row {row} is out of range (0..{df.height - 1})")
        dtype = frame_schema(df)[col]
        new_value = pl.lit(value).cast(dtype) if value is not None else pl.lit(None, dtype=dtype)
        return df.with_columns(
            pl.when(pl.int_range(pl.len()) == row).then(new_value).otherwise(pl.col(col)).alias(col)
        )

    def to_polars(self, params):
        col = json.dumps(params["column"])
        value = f"pl.lit({_py(params['value'])})"
        if params.get("dtype"):
            value += f".cast(pl.{normalize_dtype_name(params['dtype'])})"
        return (
            f"df.with_columns(pl.when(pl.int_range(pl.len()) == {int(params['row'])})"
            f".then({value}).otherwise(pl.col({col})).alias({col}))"
        )

    def describe(self, params):
        return f"Edit cell: {params['column']}[{params['row']}] = {params['value']!r}"


@step_type("insert_row")
class InsertRowStep(StepType):
    """Insert a row. Params: `values` ({column: value}), optional `index` (default: append)."""

    stateful = True

    def apply(self, df, params):
        values = params.get("values") or {}
        schema = frame_schema(df)
        unknown = [c for c in values if c not in schema]
        if unknown:
            raise StepError(f"Column(s) not found: {', '.join(unknown)}")
        new_row = pl.DataFrame(
            {c: [values.get(c)] for c in schema.names()}, schema=schema, strict=False
        )
        if isinstance(df, pl.LazyFrame):
            new_row = new_row.lazy()
        index = params.get("index")
        if index is None or (isinstance(df, pl.DataFrame) and index >= df.height):
            return pl.concat([df, new_row])
        index = max(int(index), 0)
        return pl.concat([df.slice(0, index), new_row, df.slice(index)])

    def to_polars(self, params):
        values = json.dumps(params.get("values") or {})
        new_row = f"pl.DataFrame({{c: [{values}.get(c)] for c in df.columns}}, schema=df.schema, strict=False)"
        index = params.get("index")
        if index is None:
            return f"pl.concat([df, {new_row}])"
        return f"pl.concat([df.slice(0, {int(index)}), {new_row}, df.slice({int(index)})])"

    def describe(self, params):
        where = "end" if params.get("index") is None else f"row {params['index']}"
        return f"Insert row at {where}"


@step_type("delete_rows")
class DeleteRowsStep(StepType):
    """Delete rows by 0-based position. Params: `rows` (list of ints)."""

    stateful = True
    required = ("rows",)

    def apply(self, df, params):
        rows = [int(r) for r in params["rows"]]
        return df.filter(~pl.int_range(pl.len()).is_in(rows))

    def to_polars(self, params):
        rows = [int(r) for r in params["rows"]]
        return f"df.filter(~pl.int_range(pl.len()).is_in({rows}))"

    def describe(self, params):
        rows = list(params["rows"])
        shown = ", ".join(str(r) for r in rows[:5]) + ("…" if len(rows) > 5 else "")
        return f"Delete {len(rows)} row(s): {shown}"


def _is_expression(code: str) -> bool:
    try:
        ast.parse(code.strip(), mode="eval")
        return True
    except SyntaxError:
        return False


@step_type("polars")
class PolarsStep(StepType):
    """Free-form Polars code. Params: `code`.

    `code` is either an expression returning the new DataFrame (``df.head(5)``)
    or statements that reassign `df` (``df = df.filter(...)``). Both see `df`
    and `pl`. Statements run with a restricted set of builtins and no imports.
    """

    stateful = True
    lazy = False
    required = ("code",)

    def apply(self, df, params):
        code = params["code"].strip()
        if _is_expression(code):
            from .transforms import apply_expr

            return apply_expr(df, code, params.get("extra_cells") or None)
        return self._run_statements(df, code)

    @staticmethod
    def _run_statements(df: pl.DataFrame, code: str) -> pl.DataFrame:
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as e:
            raise StepError(f"Invalid Polars code: {e.msg} (line {e.lineno})") from e
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise StepError("Imports aren't allowed in Polars steps (only `pl` and `df`)")
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise StepError(f"Access to private attribute '{node.attr}' is not allowed")
            if isinstance(node, ast.Name) and node.id.startswith("__"):
                raise StepError(f"Access to private name '{node.id}' is not allowed")
        namespace: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS, "pl": pl, "df": df}
        exec(compile(tree, "<polars step>", "exec"), namespace)
        result = namespace.get("df")
        if not isinstance(result, pl.DataFrame):
            raise StepError(
                f"Polars code must leave a DataFrame in `df`, got {type(result).__name__}"
            )
        return result

    def to_polars(self, params):
        code = params["code"].strip()
        if code.startswith("df = ") and _is_expression(code[len("df = ") :]):
            return code[len("df = ") :]
        if _is_expression(code):
            return code
        raise NotExportable(
            "Multi-statement Polars code can only be exported as statements "
            "(use to_polars_statements())"
        )

    def to_polars_statements(self, params):
        code = params["code"].strip()
        if _is_expression(code):
            return f"df = {code}"
        return code

    def describe(self, params):
        first_line = params["code"].strip().splitlines()[0] if params["code"].strip() else ""
        return f"Polars: {first_line}"


@step_type("sql")
class SqlStep(StepType):
    """Free-form DuckDB SQL. Params: `query`, optional `table` (default "df").

    The incoming data is visible to the query under the name `table`.
    """

    stateful = True
    lazy = False
    required = ("query",)

    def apply(self, df, params):
        import duckdb

        conn = duckdb.connect()
        try:
            conn.register(params.get("table") or "df", df.to_arrow())
            return pl.from_arrow(conn.execute(params["query"]).fetch_arrow_table())
        finally:
            conn.close()

    def to_polars(self, params):
        table = json.dumps(params.get("table") or "df")
        return f"duckdb.connect().register({table}, df).execute({json.dumps(params['query'])}).pl()"

    def to_sql(self, params, input_ref, columns):
        table = params.get("table") or "df"
        return f"WITH {q(table)} AS (SELECT * FROM {input_ref}) {params['query']}"

    def describe(self, params):
        return f"SQL: {params['query']}"


@step_type("manual")
class ManualStep(StepType):
    """A change made outside the step system (e.g. by a legacy UI code path).

    It's recorded so the change is undoable and audited, but it can't be
    replayed or exported. Sweet reports these instead of silently producing a
    pipeline that doesn't match the session.
    """

    stateful = True
    lazy = False

    def apply(self, df, params):
        raise StepError(
            f"'{self.describe(params)}' was a manual edit and can't be replayed. "
            "Redo it as a step (or remove it) to make the pipeline reproducible."
        )

    def to_polars(self, params):
        raise NotExportable(
            f"'{self.describe(params)}' was a manual edit and can't be exported. "
            "Redo it as a step (or remove it) to make the pipeline reproducible."
        )

    def describe(self, params):
        return params.get("description") or "Manual edit"


def value_filter_step(
    column: str, value: Any, dtype: pl.DataType, *, exclude: bool = False
) -> Step:
    """A `filter` step keeping (or excluding) rows where `column` equals `value`.

    Uses a SQL expression where the value has an exact SQL literal, so the step
    exports to both Polars and SQL. Excluding a value keeps null rows.

    Raises:
        StepError: For values that can't be compared (lists, structs, ...).
    """
    col = q(column)
    if value is None:
        sql = f"{col} IS NOT NULL" if exclude else f"{col} IS NULL"
        return Step("filter", {"sql": sql})
    if isinstance(value, float) and value != value:  # NaN
        target = f"pl.col({json.dumps(column)})"
        expr = f"~{target}.is_nan() | {target}.is_null()" if exclude else f"{target}.is_nan()"
        return Step("filter", {"expr": expr}, description=_value_label(column, value, exclude))

    literal: str | None = None
    if isinstance(value, (bool, int, float, str)):
        literal = sql_literal(value)
    elif dtype == pl.Date:
        literal = f"CAST('{value.isoformat()}' AS DATE)"
    elif isinstance(dtype, pl.Datetime) and dtype.time_zone is None:
        literal = f"CAST('{value.isoformat()}' AS TIMESTAMP)"

    if literal is not None:
        sql = f"{col} <> {literal} OR {col} IS NULL" if exclude else f"{col} = {literal}"
        return Step("filter", {"sql": sql}, description=_value_label(column, value, exclude))

    if dtype.is_temporal():
        physical = pl.Series([value], dtype=dtype).to_physical()[0]
        target = f"pl.col({json.dumps(column)}).to_physical()"
        expr = (
            f"({target} != {physical}) | {target}.is_null()"
            if exclude
            else f"{target} == {physical}"
        )
        return Step("filter", {"expr": expr}, description=_value_label(column, value, exclude))
    raise StepError(f"Can't filter on {type(value).__name__} values")


def _value_label(column: str, value: Any, exclude: bool) -> str:
    shown = value if not isinstance(value, str) else repr(value)
    return f"Filter: {column} {'≠' if exclude else '='} {shown}"


def keep_lineage(step: Step) -> Step:
    """A version of `step` that keeps Sweet's row-id column (`__sweet_row`) if present.

    Used for diffs: most steps carry extra columns through unchanged, but a
    column selection would drop it.
    """
    if step.kind == "select":
        columns = [*_columns(step.params), "__sweet_row"]
        return Step("select", {**step.params, "columns": columns}, id=step.id, enabled=step.enabled)
    return step
