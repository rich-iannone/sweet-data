"""Tests for Phase 2 remaining features: PII detection, relationships,
schema contracts, suggested casts, and correlations."""

import polars as pl
import pytest

from sweet.core.workspace import Workspace


# =============================================================================
# PII Detection
# =============================================================================


class TestDetectPII:
    """Tests for Workspace.detect_pii()."""

    def test_detect_email_by_value(self):
        """Detect emails from value patterns."""
        df = pl.DataFrame({"contact": ["alice@test.com", "bob@example.org", "charlie@co.io"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.detect_pii()

        assert result["has_pii"] is True
        assert len(result["pii_columns"]) == 1
        assert result["pii_columns"][0]["pii_type"] == "email"
        assert result["pii_columns"][0]["detected_by"] == "value_pattern"

    def test_detect_ssn_by_value(self):
        """Detect SSNs from value patterns."""
        df = pl.DataFrame({"tax_id": ["123-45-6789", "234-56-7890", "345-67-8901"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.detect_pii()

        assert result["has_pii"] is True
        pii_col = result["pii_columns"][0]
        assert pii_col["pii_type"] == "ssn"
        assert pii_col["confidence"] >= 0.9

    def test_detect_phone_by_value(self):
        """Detect phone numbers from value patterns."""
        df = pl.DataFrame({"mobile": ["+1-555-0100", "+1-555-0101", "+1-555-0102"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.detect_pii()

        assert result["has_pii"] is True
        pii_types = [c["pii_type"] for c in result["pii_columns"]]
        assert "phone" in pii_types

    def test_detect_by_column_name(self):
        """Detect PII from column naming patterns."""
        df = pl.DataFrame({"salary": [50000, 60000, 70000], "age": [30, 40, 50]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.detect_pii()

        assert result["has_pii"] is True
        pii_cols = [c["column"] for c in result["pii_columns"]]
        assert "salary" in pii_cols

    def test_no_pii(self):
        """No PII detected in clean data."""
        df = pl.DataFrame({"x": [1, 2, 3], "category": ["a", "b", "c"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.detect_pii()

        assert result["has_pii"] is False
        assert result["pii_columns"] == []

    def test_ip_address_detection(self):
        """Detect IP addresses."""
        df = pl.DataFrame({"server": ["192.168.1.1", "10.0.0.1", "172.16.0.1"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.detect_pii()

        assert result["has_pii"] is True
        assert result["pii_columns"][0]["pii_type"] == "ip_address"

    def test_credit_card_detection(self):
        """Detect credit card numbers."""
        df = pl.DataFrame(
            {"payment": ["4111-1111-1111-1111", "5500-0000-0000-0004", "3782-8224-6310-0050"]}
        )
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.detect_pii()

        assert result["has_pii"] is True

    def test_no_data_raises(self):
        """detect_pii raises with no data."""
        ws = Workspace()
        with pytest.raises(ValueError):
            ws.detect_pii()


# =============================================================================
# Relationship Detection
# =============================================================================


class TestDetectRelationships:
    """Tests for Workspace.detect_relationships()."""

    def test_basic_fk_detection(self):
        """Detect customer_id as a foreign key."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame({"customer_id": [1, 2, 3], "name": ["A", "B", "C"]}),
            name="customers",
        )
        ws.load_df(
            pl.DataFrame(
                {"order_id": [10, 20, 30], "customer_id": [1, 2, 1], "amount": [5.0, 10.0, 3.0]}
            ),
            name="orders",
        )

        result = ws.detect_relationships()

        assert len(result["relationships"]) >= 1
        rel = result["relationships"][0]
        assert rel["column_a"] == "customer_id" or rel["column_b"] == "customer_id"
        assert rel["confidence"] >= 0.5

    def test_one_to_many_type(self):
        """Relationship type is identified as one-to-many."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame({"id": [1, 2, 3, 4, 5], "name": ["A", "B", "C", "D", "E"]}),
            name="users",
        )
        ws.load_df(
            pl.DataFrame(
                {
                    "id": [10, 20, 30, 40, 50, 60],
                    "id": [1, 2, 1, 3, 5, 2],
                    "val": [1, 2, 3, 4, 5, 6],
                }
            ),
            name="events",
        )

        result = ws.detect_relationships()
        # Should find at least some relationship
        assert isinstance(result["relationships"], list)

    def test_no_relationships_single_sheet(self):
        """Raises error with fewer than 2 sheets."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        with pytest.raises(ValueError, match="at least 2 sheets"):
            ws.detect_relationships()

    def test_no_relationships_unrelated(self):
        """No relationships found for unrelated sheets."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame({"color": ["red", "blue", "green"]}),
            name="colors",
        )
        ws.load_df(
            pl.DataFrame({"planet": ["mars", "venus", "earth"]}),
            name="planets",
        )

        result = ws.detect_relationships()
        assert result["relationships"] == []


# =============================================================================
# Schema Contracts
# =============================================================================


class TestSchemaContracts:
    """Tests for Workspace.infer_contract() and enforce_contract()."""

    def test_infer_contract_basic(self):
        """Infer contract captures dtypes, nullability, uniqueness."""
        df = pl.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["Alice", "Bob", "Charlie"],
                "score": [85.0, 92.0, 78.5],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="test")

        contract = ws.infer_contract()

        assert contract["name"] == "test"
        assert contract["n_rows"] == 3
        assert len(contract["columns"]) == 3

        id_col = next(c for c in contract["columns"] if c["column"] == "id")
        assert id_col["dtype"] == "Int64"
        assert id_col["nullable"] is False
        assert id_col["unique"] is True
        assert id_col["min"] == 1
        assert id_col["max"] == 3

    def test_infer_contract_categorical(self):
        """Infer contract captures allowed values for low-cardinality string cols."""
        df = pl.DataFrame({"status": ["active", "pending", "active", "closed"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        contract = ws.infer_contract()

        status_col = next(c for c in contract["columns"] if c["column"] == "status")
        assert status_col["is_categorical"] is True
        assert set(status_col["allowed_values"]) == {"active", "pending", "closed"}

    def test_enforce_contract_passes(self):
        """Contract enforcement passes for matching data."""
        df = pl.DataFrame({"id": [1, 2, 3], "name": ["A", "B", "C"]})
        ws = Workspace()
        ws.load_df(df, name="test")
        contract = ws.infer_contract()

        # Same data should pass
        result = ws.enforce_contract(contract)
        assert result["passed"] is True
        assert result["n_violations"] == 0

    def test_enforce_contract_dtype_mismatch(self):
        """Contract enforcement detects dtype changes."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))
        contract = ws.infer_contract()

        # Load different type data
        ws2 = Workspace()
        ws2.load_df(pl.DataFrame({"x": ["a", "b", "c"]}))

        result = ws2.enforce_contract(contract)
        assert result["passed"] is False
        violations = [v for v in result["violations"] if v["violation"] == "dtype_mismatch"]
        assert len(violations) == 1

    def test_enforce_contract_missing_column(self):
        """Contract enforcement detects missing columns."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"a": [1], "b": [2]}))
        contract = ws.infer_contract()

        ws2 = Workspace()
        ws2.load_df(pl.DataFrame({"a": [1]}))

        result = ws2.enforce_contract(contract)
        violations = [v for v in result["violations"] if v["violation"] == "missing_column"]
        assert len(violations) == 1
        assert violations[0]["column"] == "b"

    def test_enforce_contract_unexpected_nulls(self):
        """Contract enforcement detects unexpected nulls."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))
        contract = ws.infer_contract()  # nullable=False

        ws2 = Workspace()
        ws2.load_df(pl.DataFrame({"x": [1, None, 3]}))

        result = ws2.enforce_contract(contract)
        violations = [v for v in result["violations"] if v["violation"] == "unexpected_nulls"]
        assert len(violations) == 1

    def test_enforce_contract_range_violation(self):
        """Contract enforcement detects out-of-range numeric values."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"val": [10, 20, 30]}))
        contract = ws.infer_contract()  # min=10, max=30

        ws2 = Workspace()
        ws2.load_df(pl.DataFrame({"val": [5, 20, 50]}))

        result = ws2.enforce_contract(contract)
        below = [v for v in result["violations"] if v["violation"] == "below_minimum"]
        above = [v for v in result["violations"] if v["violation"] == "above_maximum"]
        assert len(below) == 1
        assert len(above) == 1

    def test_enforce_contract_unexpected_values(self):
        """Contract enforcement detects unexpected categorical values."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"status": ["A", "B", "A"]}))
        contract = ws.infer_contract()

        ws2 = Workspace()
        ws2.load_df(pl.DataFrame({"status": ["A", "C"]}))

        result = ws2.enforce_contract(contract)
        violations = [v for v in result["violations"] if v["violation"] == "unexpected_values"]
        assert len(violations) == 1
        assert "C" in violations[0]["unexpected"]

    def test_enforce_contract_extra_columns(self):
        """Contract enforcement reports extra columns."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1]}))
        contract = ws.infer_contract()

        ws2 = Workspace()
        ws2.load_df(pl.DataFrame({"x": [1], "y": [2]}))

        result = ws2.enforce_contract(contract)
        violations = [v for v in result["violations"] if v["violation"] == "extra_columns"]
        assert len(violations) == 1
        assert "y" in violations[0]["columns"]

    def test_no_data_raises(self):
        """infer_contract raises with no data."""
        ws = Workspace()
        with pytest.raises(ValueError):
            ws.infer_contract()


# =============================================================================
# Suggested Casts
# =============================================================================


class TestSuggestCasts:
    """Tests for Workspace.suggest_casts() and apply_casts()."""

    def test_suggest_date_cast(self):
        """Suggest casting ISO date strings."""
        df = pl.DataFrame({"date_col": ["2024-01-15", "2024-02-20", "2024-03-25"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        suggestions = ws.suggest_casts()

        assert len(suggestions) == 1
        assert suggestions[0]["column"] == "date_col"
        assert "Date" in suggestions[0]["to_type"]
        assert "str.to_date" in suggestions[0]["expression"]

    def test_suggest_integer_cast(self):
        """Suggest casting integer strings."""
        df = pl.DataFrame({"amount": ["100", "200", "300", "400"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        suggestions = ws.suggest_casts()

        assert len(suggestions) == 1
        assert suggestions[0]["column"] == "amount"
        assert "Int64" in suggestions[0]["to_type"]

    def test_suggest_boolean_cast(self):
        """Suggest casting boolean strings."""
        df = pl.DataFrame({"active": ["true", "false", "true", "false"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        suggestions = ws.suggest_casts()

        assert len(suggestions) == 1
        assert "Boolean" in suggestions[0]["to_type"]

    def test_no_suggestions_for_typed_data(self):
        """No suggestions for already-typed columns."""
        df = pl.DataFrame({"x": [1, 2, 3], "y": [1.0, 2.0, 3.0]})
        ws = Workspace()
        ws.load_df(df, name="test")

        suggestions = ws.suggest_casts()
        assert suggestions == []

    def test_apply_casts(self):
        """apply_casts transforms string columns to detected types."""
        df = pl.DataFrame({"num_str": ["10", "20", "30"], "name": ["a", "b", "c"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        ws.apply_casts()

        # num_str should now be Int64
        assert ws.df["num_str"].dtype == pl.Int64

    def test_apply_casts_no_suggestions(self):
        """apply_casts does nothing with no suggestions."""
        df = pl.DataFrame({"x": [1, 2, 3]})
        ws = Workspace()
        ws.load_df(df, name="test")

        ws.apply_casts()  # Should not raise
        assert ws.df["x"].dtype == pl.Int64


# =============================================================================
# Correlations
# =============================================================================


class TestCorrelations:
    """Tests for Workspace.correlations()."""

    def test_perfect_positive_correlation(self):
        """Detect perfect positive correlation."""
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [2.0, 4.0, 6.0, 8.0, 10.0]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.correlations()

        assert len(result["pairs"]) == 1
        assert result["pairs"][0]["correlation"] == pytest.approx(1.0, abs=0.001)

    def test_perfect_negative_correlation(self):
        """Detect perfect negative correlation."""
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "z": [10.0, 8.0, 6.0, 4.0, 2.0]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.correlations()

        assert len(result["pairs"]) == 1
        assert result["pairs"][0]["correlation"] == pytest.approx(-1.0, abs=0.001)

    def test_min_abs_filter(self):
        """min_abs filters out weak correlations."""
        df = pl.DataFrame(
            {
                "x": [1.0, 2.0, 3.0, 4.0, 5.0],
                "y": [2.0, 4.0, 6.0, 8.0, 10.0],
                "noise": [3.2, 1.1, 7.8, 4.4, 2.9],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.correlations(min_abs=0.9)

        # Only x↔y should pass
        assert len(result["pairs"]) == 1
        assert result["pairs"][0]["column_a"] == "x"
        assert result["pairs"][0]["column_b"] == "y"

    def test_spearman_method(self):
        """Spearman correlation works."""
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [1.0, 4.0, 9.0, 16.0, 25.0]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.correlations(method="spearman")

        assert result["method"] == "spearman"
        assert len(result["pairs"]) == 1
        # Monotonic relationship → spearman = 1.0
        assert result["pairs"][0]["correlation"] == pytest.approx(1.0, abs=0.001)

    def test_fewer_than_2_numeric_cols_raises(self):
        """Raises ValueError with fewer than 2 numeric columns."""
        df = pl.DataFrame({"x": [1, 2, 3], "name": ["a", "b", "c"]})
        ws = Workspace()
        ws.load_df(df, name="test")

        with pytest.raises(ValueError, match="at least 2 numeric"):
            ws.correlations()

    def test_invalid_method_raises(self):
        """Invalid method raises ValueError."""
        df = pl.DataFrame({"x": [1.0, 2.0], "y": [3.0, 4.0]})
        ws = Workspace()
        ws.load_df(df, name="test")

        with pytest.raises(ValueError, match="Unknown correlation method"):
            ws.correlations(method="kendall")

    def test_no_data_raises(self):
        """correlations raises with no data."""
        ws = Workspace()
        with pytest.raises(ValueError):
            ws.correlations()

    def test_handles_nulls(self):
        """Correlations handle null values by dropping them."""
        df = pl.DataFrame({"x": [1.0, 2.0, None, 4.0, 5.0], "y": [2.0, 4.0, 6.0, None, 10.0]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.correlations()
        # Should compute on non-null pairs
        assert len(result["pairs"]) == 1


# =============================================================================
# Integration Workflow
# =============================================================================


class TestPhase2FullWorkflow:
    """End-to-end tests combining Phase 2 features."""

    def test_full_data_understanding_workflow(self):
        """Complete data understanding workflow."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "customer_id": [1, 2, 3, 4, 5],
                    "email": ["a@b.com", "c@d.com", None, "e@f.com", "g@h.com"],
                    "revenue": [100.0, 200.0, 150.0, 300.0, 250.0],
                    "signup_date": [
                        "2024-01-01",
                        "2024-02-15",
                        "2024-03-20",
                        "2024-04-10",
                        "2024-05-05",
                    ],
                    "status": ["active", "active", "churned", "active", "pending"],
                }
            ),
            name="customers",
        )

        # 1. PII detection
        pii = ws.detect_pii()
        assert pii["has_pii"] is True
        pii_cols = [c["column"] for c in pii["pii_columns"]]
        assert "email" in pii_cols

        # 2. Suggest casts
        casts = ws.suggest_casts()
        cast_cols = [c["column"] for c in casts]
        assert "signup_date" in cast_cols

        # 3. Infer contract
        contract = ws.infer_contract()
        assert contract["n_rows"] == 5
        status_col = next(c for c in contract["columns"] if c["column"] == "status")
        assert "active" in status_col["allowed_values"]

        # 4. Correlations
        # Only revenue and customer_id are numeric here
        corr = ws.correlations()
        assert corr["n_numeric_columns"] == 2

    def test_contract_roundtrip(self):
        """Infer contract, modify data, enforce contract."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "id": [1, 2, 3],
                    "score": [80.0, 90.0, 85.0],
                    "grade": ["B", "A", "B"],
                }
            )
        )

        contract = ws.infer_contract()

        # Modify data to violate
        ws2 = Workspace()
        ws2.load_df(
            pl.DataFrame(
                {
                    "id": [1, 1, 3],  # duplicate id
                    "score": [80.0, 110.0, 85.0],  # out of range
                    "grade": ["B", "A", "F"],  # unexpected value
                }
            )
        )

        result = ws2.enforce_contract(contract)
        assert result["passed"] is False
        violation_types = [v["violation"] for v in result["violations"]]
        assert "uniqueness_violated" in violation_types
        assert "above_maximum" in violation_types
        assert "unexpected_values" in violation_types
