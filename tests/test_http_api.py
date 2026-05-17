"""Tests for the HTTP REST API (sweet/http_api.py)."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from starlette.testclient import TestClient

from sweet.http_api import create_app, reset_workspace, _get_workspace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    """Reset workspace before each test."""
    reset_workspace()
    yield
    reset_workspace()


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    df = pl.DataFrame({
        "name": ["Alice", "Bob", "Charlie", "Diana"],
        "age": [30, 25, 35, 28],
        "city": ["NYC", "LA", "NYC", "Chicago"],
    })
    path = tmp_path / "test.csv"
    df.write_csv(path)
    return path


@pytest.fixture
def loaded_client(client, csv_file):
    """Client with data already loaded."""
    client.post("/api/load", json={"path": str(csv_file)})
    return client


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_health_with_data(self, loaded_client):
        resp = loaded_client.get("/api/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["sheets"]) > 0


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


class TestLoad:
    def test_load_csv(self, client, csv_file):
        resp = client.post("/api/load", json={"path": str(csv_file)})
        assert resp.status_code == 200
        data = resp.json()
        assert data["loaded"] == str(csv_file)
        assert data["shape"] == [4, 3]
        assert "name" in data["columns"]

    def test_load_with_name(self, client, csv_file):
        resp = client.post("/api/load", json={"path": str(csv_file), "name": "my_data"})
        assert resp.status_code == 200
        assert resp.json()["shape"] == [4, 3]

    def test_load_missing_path(self, client):
        resp = client.post("/api/load", json={})
        assert resp.status_code == 400
        assert "path" in resp.json()["error"]

    def test_load_nonexistent(self, client):
        resp = client.post("/api/load", json={"path": "/nonexistent/file.csv"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------


class TestInspect:
    def test_inspect(self, loaded_client):
        resp = loaded_client.get("/api/inspect")
        assert resp.status_code == 200
        data = resp.json()
        assert data["shape"] == [4, 3]
        assert "name" in data["schema"]
        assert len(data["sample"]) > 0

    def test_inspect_no_data(self, client):
        resp = client.get("/api/inspect")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------


class TestTransform:
    def test_transform(self, loaded_client):
        resp = loaded_client.post(
            "/api/transform",
            json={"expr": "df.filter(pl.col('age') > 28)"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["transformed"] is True
        assert data["shape"][0] == 2  # Alice (30) and Charlie (35)

    def test_transform_missing_expr(self, loaded_client):
        resp = loaded_client.post("/api/transform", json={})
        assert resp.status_code == 400

    def test_transform_bad_expr(self, loaded_client):
        resp = loaded_client.post(
            "/api/transform", json={"expr": "invalid_code!!!"}
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class TestFilter:
    def test_filter(self, loaded_client):
        resp = loaded_client.post(
            "/api/filter", json={"condition": "pl.col('city') == 'NYC'"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filtered"] is True
        assert data["shape"][0] == 2

    def test_filter_missing_condition(self, loaded_client):
        resp = loaded_client.post("/api/filter", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


class TestSort:
    def test_sort(self, loaded_client):
        resp = loaded_client.post("/api/sort", json={"by": "age"})
        assert resp.status_code == 200
        assert resp.json()["sorted"] is True

    def test_sort_descending(self, loaded_client):
        resp = loaded_client.post(
            "/api/sort", json={"by": "age", "descending": True}
        )
        assert resp.status_code == 200

    def test_sort_missing_by(self, loaded_client):
        resp = loaded_client.post("/api/sort", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Select
# ---------------------------------------------------------------------------


class TestSelect:
    def test_select(self, loaded_client):
        resp = loaded_client.post("/api/select", json={"columns": ["name", "age"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["selected"] is True
        assert data["shape"][1] == 2

    def test_select_missing(self, loaded_client):
        resp = loaded_client.post("/api/select", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------


class TestSheets:
    def test_sheets_empty(self, client):
        resp = client.get("/api/sheets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sheets"] == []

    def test_sheets_with_data(self, loaded_client):
        resp = loaded_client.get("/api/sheets")
        data = resp.json()
        assert len(data["sheets"]) == 1
        assert data["sheets"][0]["shape"] == [4, 3]


# ---------------------------------------------------------------------------
# Sample
# ---------------------------------------------------------------------------


class TestSample:
    def test_sample(self, loaded_client):
        resp = loaded_client.get("/api/sample?n=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rows"]) == 2

    def test_sample_default(self, loaded_client):
        resp = loaded_client.get("/api/sample")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rows"]) == 4  # only 4 rows, less than default 10


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_csv(self, loaded_client, tmp_path):
        out = tmp_path / "output.csv"
        resp = loaded_client.post("/api/export", json={"path": str(out)})
        assert resp.status_code == 200
        assert resp.json()["exported"] == str(out)
        assert out.exists()
        df = pl.read_csv(out)
        assert len(df) == 4

    def test_export_missing_path(self, loaded_client):
        resp = loaded_client.post("/api/export", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Undo / Redo
# ---------------------------------------------------------------------------


class TestUndoRedo:
    def test_undo(self, loaded_client):
        # Transform then undo
        loaded_client.post(
            "/api/transform", json={"expr": "df.filter(pl.col('age') > 30)"}
        )
        resp = loaded_client.post("/api/undo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["undone"] is True
        assert data["shape"][0] == 4  # back to original

    def test_redo(self, loaded_client):
        loaded_client.post(
            "/api/transform", json={"expr": "df.filter(pl.col('age') > 30)"}
        )
        loaded_client.post("/api/undo")
        resp = loaded_client.post("/api/redo")
        assert resp.status_code == 200
        assert resp.json()["redone"] is True


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


class TestHistory:
    def test_history(self, loaded_client):
        loaded_client.post(
            "/api/transform", json={"expr": "df.filter(pl.col('age') > 28)"}
        )
        resp = loaded_client.get("/api/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["operations"]) >= 1


# ---------------------------------------------------------------------------
# Branch / Switch
# ---------------------------------------------------------------------------


class TestBranchSwitch:
    def test_branch(self, loaded_client):
        resp = loaded_client.post("/api/branch", json={"name": "experiment"})
        assert resp.status_code == 200
        assert resp.json()["branched"] == "experiment"

    def test_switch(self, loaded_client):
        loaded_client.post("/api/branch", json={"name": "exp"})
        resp = loaded_client.post("/api/switch", json={"name": "exp"})
        assert resp.status_code == 200
        assert resp.json()["switched"] == "exp"

    def test_branch_missing_name(self, loaded_client):
        resp = loaded_client.post("/api/branch", json={})
        assert resp.status_code == 400

    def test_switch_missing_name(self, loaded_client):
        resp = loaded_client.post("/api/switch", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Describe
# ---------------------------------------------------------------------------


class TestDescribe:
    def test_describe(self, loaded_client):
        resp = loaded_client.get("/api/describe")
        assert resp.status_code == 200
        data = resp.json()
        assert "description" in data
        assert len(data["description"]) > 0


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_schema(self, loaded_client):
        resp = loaded_client.get("/api/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data  # column name in schema dict
        assert "age" in data


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


class TestRecipes:
    def test_recipe_list(self, loaded_client):
        resp = loaded_client.get("/api/recipe/list")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 3  # built-in recipes
        names = [r["name"] for r in data]
        assert "clean-csv" in names

    def test_recipe_run(self, loaded_client):
        resp = loaded_client.post(
            "/api/recipe/run", json={"recipe": "clean-csv"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["steps_completed"] == 3

    def test_recipe_run_with_params(self, loaded_client):
        resp = loaded_client.post(
            "/api/recipe/run",
            json={"recipe": "quick-profile", "params": {"sample_size": 2}},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_recipe_run_missing(self, loaded_client):
        resp = loaded_client.post("/api/recipe/run", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate(self, loaded_client):
        resp = loaded_client.post(
            "/api/validate",
            json={
                "rules": [
                    {"name": "age_positive", "check": "col('age') > 0", "severity": "error"}
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "rules_checked" in data

    def test_validate_missing_rules(self, loaded_client):
        resp = loaded_client.post("/api/validate", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Generate Code
# ---------------------------------------------------------------------------


class TestGenerateCode:
    def test_generate_code(self, loaded_client):
        loaded_client.post(
            "/api/transform", json={"expr": "df.filter(pl.col('age') > 28)"}
        )
        resp = loaded_client.get("/api/generate-code")
        assert resp.status_code == 200
        data = resp.json()
        assert "code" in data
        assert data["format"] == "polars"


# ---------------------------------------------------------------------------
# Query (SQL)
# ---------------------------------------------------------------------------


class TestQuery:
    def test_query(self, loaded_client):
        resp = loaded_client.post(
            "/api/query",
            json={"sql": "SELECT name, age FROM test WHERE age > 28"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["queried"] is True

    def test_query_missing_sql(self, loaded_client):
        resp = loaded_client.post("/api/query", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class TestCORS:
    def test_cors_headers(self, client):
        resp = client.options(
            "/api/health",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "*"
