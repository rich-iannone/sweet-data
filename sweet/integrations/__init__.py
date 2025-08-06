from pathlib import Path
from typing import Any

try:
    import duckdb
except ImportError:
    duckdb = None

try:
    import polars as pl
except ImportError:
    pl = None


class DBConnector:
    """Database connector for Sweet workbooks using DuckDB."""

    def __init__(self, db_path: str | Path = ":memory:", name: str = "default"):
        """Initialize database connector.

        Args:
            db_path: Path to DuckDB database file (":memory:" for in-memory)
            name: Name for this connection
        """
        if duckdb is None:
            raise ImportError("DuckDB is required but not installed")

        self.db_path = str(db_path)
        self.name = name
        self._connection = None

    @property
    def connection(self):
        """Get or create DuckDB connection."""
        if self._connection is None:
            self._connection = duckdb.connect(self.db_path)
        return self._connection

    def fetch_table(self, table_name: str) -> "pl.DataFrame":
        """Fetch a table as a Polars DataFrame.

        Args:
            table_name: Name of the table to fetch

        Returns:
            Polars DataFrame with table data

        Raises:
            ImportError: If polars is not available
        """
        if pl is None:
            raise ImportError("Polars is required but not installed")

        query = f"SELECT * FROM {table_name}"
        return self.fetch_query(query)

    def fetch_query(self, query: str) -> "pl.DataFrame":
        """Execute a query and return results as a Polars DataFrame.

        Args:
            query: SQL query to execute

        Returns:
            Polars DataFrame with query results

        Raises:
            ImportError: If polars is not available
        """
        if pl is None:
            raise ImportError("Polars is required but not installed")

        # Execute query with DuckDB and convert to Polars
        result = self.connection.execute(query).fetchdf()
        return pl.from_pandas(result)

    def write_table(
        self,
        df: "pl.DataFrame",
        table_name: str,
        mode: str = "replace",
    ) -> None:
        """Write a DataFrame to a database table.

        Args:
            df: DataFrame to write
            table_name: Name of the target table
            mode: Write mode ("replace", "append", "fail")

        Raises:
            ImportError: If polars is not available
        """
        if pl is None:
            raise ImportError("Polars is required but not installed")

        # Convert to pandas for DuckDB insertion
        pandas_df = df.to_pandas()

        if mode == "replace":
            # Drop table if exists, then create new
            self.connection.execute(f"DROP TABLE IF EXISTS {table_name}")
            self.connection.register("temp_df", pandas_df)
            self.connection.execute(f"CREATE TABLE {table_name} AS SELECT * FROM temp_df")
        elif mode == "append":
            # Insert into existing table
            self.connection.register("temp_df", pandas_df)
            self.connection.execute(f"INSERT INTO {table_name} SELECT * FROM temp_df")
        elif mode == "fail":
            # Fail if table exists
            tables = self.list_tables()
            if table_name in tables:
                raise ValueError(f"Table {table_name} already exists")
            self.connection.register("temp_df", pandas_df)
            self.connection.execute(f"CREATE TABLE {table_name} AS SELECT * FROM temp_df")
        else:
            raise ValueError(f"Invalid mode: {mode}. Use 'replace', 'append', or 'fail'")

    def list_tables(self) -> list[str]:
        """List all tables in the database.

        Returns:
            List of table names
        """
        result = self.connection.execute("SHOW TABLES").fetchall()
        return [row[0] for row in result]

    def get_table_schema(self, table_name: str) -> dict[str, str]:
        """Get the schema of a table.

        Args:
            table_name: Name of the table

        Returns:
            Dictionary mapping column names to data types
        """
        result = self.connection.execute(f"DESCRIBE {table_name}").fetchall()
        return {row[0]: row[1] for row in result}

    def execute_sql(self, query: str) -> Any:
        """Execute a SQL query and return raw results.

        Args:
            query: SQL query to execute

        Returns:
            Query results
        """
        return self.connection.execute(query).fetchall()

    def test_connection(self) -> bool:
        """Test if the database connection is working.

        Returns:
            True if connection is successful
        """
        try:
            self.connection.execute("SELECT 1")
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def load_csv(self, file_path: str | Path, table_name: str) -> None:
        """Load a CSV file directly into a table using DuckDB.

        Args:
            file_path: Path to the CSV file
            table_name: Name for the table
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        self.connection.execute(
            f"CREATE TABLE {table_name} AS SELECT * FROM read_csv_auto('{file_path}')"
        )

    def load_parquet(self, file_path: str | Path, table_name: str) -> None:
        """Load a Parquet file directly into a table using DuckDB.

        Args:
            file_path: Path to the Parquet file
            table_name: Name for the table
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        self.connection.execute(
            f"CREATE TABLE {table_name} AS SELECT * FROM read_parquet('{file_path}')"
        )


def create_memory_db(name: str = "memory") -> DBConnector:
    """Create an in-memory DuckDB connector.

    Args:
        name: Name for this connection

    Returns:
        DBConnector instance for in-memory DuckDB
    """
    return DBConnector(":memory:", name)


def create_file_db(db_path: str | Path, name: str = "file") -> DBConnector:
    """Create a file-based DuckDB connector.

    Args:
        db_path: Path to DuckDB database file
        name: Name for this connection

    Returns:
        DBConnector instance for file-based DuckDB
    """
    return DBConnector(db_path, name)
