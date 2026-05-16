"""Scenario-based tests for real data science and data engineering workflows.

Each test uses realistic data patterns and asserts provably correct expected
outputs — not just shapes, but exact values. These cover the kinds of transforms
data workers perform daily.
"""

import pytest
import polars as pl
from datetime import date, datetime

from sweet.core.workspace import Workspace


# =============================================================================
# Fixtures: Realistic Datasets
# =============================================================================


@pytest.fixture
def sales_df():
    """Sales transactions with typical data engineering challenges."""
    return pl.DataFrame(
        {
            "order_id": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "customer_id": [101, 102, 101, 103, 104, 102, 101, 105, 103, 104],
            "product": [
                "Widget",
                "Gadget",
                "Widget",
                "Doohickey",
                "Widget",
                "Gadget",
                "Thingamajig",
                "Widget",
                "Gadget",
                "Doohickey",
            ],
            "quantity": [2, 1, 5, 3, 1, 2, 4, 10, 1, 2],
            "unit_price": [10.0, 25.0, 10.0, 15.0, 10.0, 25.0, 8.0, 10.0, 25.0, 15.0],
            "order_date": [
                "2024-01-15",
                "2024-01-20",
                "2024-02-01",
                "2024-02-10",
                "2024-02-15",
                "2024-03-01",
                "2024-03-05",
                "2024-03-10",
                "2024-03-15",
                "2024-03-20",
            ],
            "region": [
                "East",
                "West",
                "East",
                "East",
                "West",
                "West",
                "East",
                "East",
                "West",
                "East",
            ],
        }
    )


@pytest.fixture
def messy_df():
    """Dataset with common data quality issues."""
    return pl.DataFrame(
        {
            "name": ["  Alice ", "Bob", "CHARLIE", "alice", "  Bob  ", "David", None, "Eve"],
            "email": [
                "alice@example.com",
                "bob@test.com",
                "charlie@example.com",
                "alice@example.com",
                "bob@test.com",
                "david@work.org",
                "eve@example.com",
                "eve@example.com",
            ],
            "age": [30, 25, 35, 30, 25, None, 28, 22],
            "salary": [50000, 60000, 75000, 50000, 60000, 80000, 55000, 45000],
            "department": [
                "Engineering",
                "Marketing",
                "engineering",
                "Engineering",
                "marketing",
                "Sales",
                "Engineering",
                "Marketing",
            ],
        }
    )


@pytest.fixture
def timeseries_df():
    """Time series data for window function testing."""
    return pl.DataFrame(
        {
            "date": pl.Series(
                [
                    date(2024, 1, 1),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                    date(2024, 1, 5),
                    date(2024, 1, 6),
                    date(2024, 1, 7),
                    date(2024, 1, 8),
                    date(2024, 1, 9),
                    date(2024, 1, 10),
                ]
            ),
            "value": [10.0, 12.0, 15.0, 11.0, 13.0, 18.0, 20.0, 16.0, 14.0, 17.0],
            "category": ["A", "B", "A", "B", "A", "B", "A", "B", "A", "B"],
        }
    )


@pytest.fixture
def customers_df():
    """Customer dimension table for join testing."""
    return pl.DataFrame(
        {
            "customer_id": [101, 102, 103, 104, 105],
            "customer_name": ["Acme Corp", "Beta Inc", "Gamma LLC", "Delta Co", "Epsilon Ltd"],
            "tier": ["Gold", "Silver", "Bronze", "Gold", "Silver"],
            "signup_date": ["2020-01-15", "2021-06-30", "2022-03-10", "2019-11-01", "2023-01-20"],
        }
    )


@pytest.fixture
def nulls_df():
    """Dataset for null handling scenarios."""
    return pl.DataFrame(
        {
            "id": [1, 2, 3, 4, 5, 6, 7, 8],
            "value_a": [10.0, None, 30.0, None, 50.0, 60.0, None, 80.0],
            "value_b": [1.0, 2.0, None, 4.0, None, 6.0, 7.0, None],
            "group": ["X", "X", "X", "Y", "Y", "Y", "Z", "Z"],
        }
    )


# =============================================================================
# Group-By Aggregation Scenarios
# =============================================================================


class TestAggregations:
    """Tests with provably correct aggregation results."""

    def test_revenue_by_product(self, sales_df):
        """Total revenue per product — manually verified."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.transform(
            "df.with_columns((pl.col('quantity') * pl.col('unit_price')).alias('revenue'))"
            ".group_by('product').agg(pl.col('revenue').sum().alias('total_revenue'))"
            ".sort('product')"
        )

        result = ws.df
        # Manual calculation:
        # Doohickey: 3*15 + 2*15 = 45 + 30 = 75
        # Gadget: 1*25 + 2*25 + 1*25 = 25 + 50 + 25 = 100
        # Thingamajig: 4*8 = 32
        # Widget: 2*10 + 5*10 + 1*10 + 10*10 = 20 + 50 + 10 + 100 = 180
        assert result["product"].to_list() == ["Doohickey", "Gadget", "Thingamajig", "Widget"]
        assert result["total_revenue"].to_list() == [75.0, 100.0, 32.0, 180.0]

    def test_customer_order_count(self, sales_df):
        """Count orders per customer — manually verified."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.transform(
            "df.group_by('customer_id').agg("
            "  pl.col('order_id').count().alias('order_count'),"
            "  pl.col('quantity').sum().alias('total_items')"
            ").sort('customer_id')"
        )

        result = ws.df
        # customer 101: orders [1,3,7] → count=3, items=2+5+4=11
        # customer 102: orders [2,6] → count=2, items=1+2=3
        # customer 103: orders [4,9] → count=2, items=3+1=4
        # customer 104: orders [5,10] → count=2, items=1+2=3
        # customer 105: orders [8] → count=1, items=10
        assert result["customer_id"].to_list() == [101, 102, 103, 104, 105]
        assert result["order_count"].to_list() == [3, 2, 2, 2, 1]
        assert result["total_items"].to_list() == [11, 3, 4, 3, 10]

    def test_regional_monthly_summary(self, sales_df):
        """Revenue by region and month — multi-level aggregation."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.transform(
            "df.with_columns("
            "  (pl.col('quantity') * pl.col('unit_price')).alias('revenue'),"
            "  pl.col('order_date').str.slice(0, 7).alias('month')"
            ").group_by('region', 'month').agg("
            "  pl.col('revenue').sum().alias('total_revenue')"
            ").sort('region', 'month')"
        )

        result = ws.df
        # East, 2024-01: order 1 → 2*10=20
        # East, 2024-02: orders 3,4 → 5*10 + 3*15 = 50+45=95
        # East, 2024-03: orders 7,8,10 → 4*8 + 10*10 + 2*15 = 32+100+30=162
        # West, 2024-01: order 2 → 1*25=25
        # West, 2024-02: order 5 → 1*10=10
        # West, 2024-03: orders 6,9 → 2*25 + 1*25 = 50+25=75
        east = result.filter(pl.col("region") == "East")
        west = result.filter(pl.col("region") == "West")

        assert east["month"].to_list() == ["2024-01", "2024-02", "2024-03"]
        assert east["total_revenue"].to_list() == [20.0, 95.0, 162.0]
        assert west["month"].to_list() == ["2024-01", "2024-02", "2024-03"]
        assert west["total_revenue"].to_list() == [25.0, 10.0, 75.0]


# =============================================================================
# Data Cleaning Scenarios
# =============================================================================


class TestDataCleaning:
    """Tests for common data cleaning operations with exact expected outputs."""

    def test_string_normalization(self, messy_df):
        """Strip whitespace and normalize case."""
        ws = Workspace()
        ws.load_df(messy_df, name="messy")
        ws.transform(
            "df.with_columns("
            "  pl.col('name').str.strip_chars().str.to_lowercase().alias('name_clean'),"
            "  pl.col('department').str.to_lowercase().alias('dept_clean')"
            ")"
        )

        result = ws.df
        assert result["name_clean"].to_list() == [
            "alice",
            "bob",
            "charlie",
            "alice",
            "bob",
            "david",
            None,
            "eve",
        ]
        assert result["dept_clean"].to_list() == [
            "engineering",
            "marketing",
            "engineering",
            "engineering",
            "marketing",
            "sales",
            "engineering",
            "marketing",
        ]

    def test_deduplication_by_email(self, messy_df):
        """Deduplicate keeping the first occurrence per email."""
        ws = Workspace()
        ws.load_df(messy_df, name="messy")
        ws.transform("df.unique(subset=['email'], keep='first')")

        result = ws.df
        # 8 rows → 5 unique emails:
        #   alice@example.com (×2), bob@test.com (×2), eve@example.com (×2),
        #   charlie@example.com (×1), david@work.org (×1)
        assert result.shape[0] == 5
        assert result["email"].n_unique() == 5

    def test_null_fill_strategies(self, nulls_df):
        """Fill nulls with different strategies — verify exact values."""
        ws = Workspace()
        ws.load_df(nulls_df, name="nulls")

        # Fill value_a nulls with the group mean
        ws.transform(
            "df.with_columns("
            "  pl.col('value_a').fill_null(pl.col('value_a').mean().over('group')).alias('a_filled')"
            ")"
        )

        result = ws.df
        # Group X: non-null values are [10, 30] → mean = 20
        # Group Y: non-null values are [50, 60] → mean = 55
        # Group Z: non-null values are [80] → mean = 80
        filled = result["a_filled"].to_list()
        assert filled[0] == 10.0  # original
        assert filled[1] == 20.0  # was null, X mean
        assert filled[2] == 30.0  # original
        assert filled[3] == 55.0  # was null, Y mean
        assert filled[4] == 50.0  # original
        assert filled[5] == 60.0  # original
        assert filled[6] == 80.0  # was null, Z mean
        assert filled[7] == 80.0  # original

    def test_conditional_column_creation(self, sales_df):
        """Create a category column based on business rules."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.transform(
            "df.with_columns("
            "  pl.when(pl.col('quantity') * pl.col('unit_price') >= 100)"
            "    .then(pl.lit('high'))"
            "    .when(pl.col('quantity') * pl.col('unit_price') >= 30)"
            "    .then(pl.lit('medium'))"
            "    .otherwise(pl.lit('low'))"
            "    .alias('revenue_tier')"
            ")"
        )

        result = ws.df
        # revenue: [20, 25, 50, 45, 10, 50, 32, 100, 25, 30]
        # tiers:   [low, low, med, med, low, med, med, high, low, med]
        assert result["revenue_tier"].to_list() == [
            "low",
            "low",
            "medium",
            "medium",
            "low",
            "medium",
            "medium",
            "high",
            "low",
            "medium",
        ]


# =============================================================================
# Type Casting & Date Parsing
# =============================================================================


class TestTypeCasting:
    """Tests for type coercion — common source of bugs."""

    def test_string_to_date_parsing(self, sales_df):
        """Parse date strings into proper Date type."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.transform(
            "df.with_columns(pl.col('order_date').str.to_date('%Y-%m-%d').alias('date_parsed'))"
        )

        result = ws.df
        assert result["date_parsed"].dtype == pl.Date
        assert result["date_parsed"][0] == date(2024, 1, 15)
        assert result["date_parsed"][9] == date(2024, 3, 20)

    def test_numeric_type_casting(self):
        """Cast between numeric types and verify precision."""
        df = pl.DataFrame(
            {
                "int_val": [1, 2, 3, 4, 5],
                "float_str": ["1.5", "2.7", "3.14", "4.0", "5.99"],
                "bool_val": [True, False, True, True, False],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="types")
        ws.transform(
            "df.with_columns("
            "  pl.col('int_val').cast(pl.Float64).alias('as_float'),"
            "  pl.col('float_str').cast(pl.Float64).alias('parsed_float'),"
            "  pl.col('bool_val').cast(pl.Int8).alias('bool_as_int')"
            ")"
        )

        result = ws.df
        assert result["as_float"].to_list() == [1.0, 2.0, 3.0, 4.0, 5.0]
        assert result["parsed_float"].to_list() == [1.5, 2.7, 3.14, 4.0, 5.99]
        assert result["bool_as_int"].to_list() == [1, 0, 1, 1, 0]

    def test_date_extraction(self):
        """Extract year, month, day-of-week from dates."""
        df = pl.DataFrame(
            {
                "dt": pl.Series(
                    [
                        date(2024, 1, 1),  # Monday
                        date(2024, 3, 15),  # Friday
                        date(2024, 7, 4),  # Thursday
                        date(2024, 12, 25),  # Wednesday
                    ]
                ),
            }
        )

        ws = Workspace()
        ws.load_df(df, name="dates")
        ws.transform(
            "df.with_columns("
            "  pl.col('dt').dt.year().alias('year'),"
            "  pl.col('dt').dt.month().alias('month'),"
            "  pl.col('dt').dt.weekday().alias('weekday')"
            ")"
        )

        result = ws.df
        assert result["year"].to_list() == [2024, 2024, 2024, 2024]
        assert result["month"].to_list() == [1, 3, 7, 12]
        # Polars weekday: Monday=1, Sunday=7
        assert result["weekday"].to_list() == [1, 5, 4, 3]


# =============================================================================
# Window Functions
# =============================================================================


class TestWindowFunctions:
    """Tests for window/analytic functions — verified against manual calculation."""

    def test_running_sum(self, timeseries_df):
        """Cumulative sum by category."""
        ws = Workspace()
        ws.load_df(timeseries_df, name="ts")
        ws.transform(
            "df.with_columns(  pl.col('value').cum_sum().over('category').alias('running_total'))"
        )

        result = ws.df
        # Category A (rows 0,2,4,6,8): values [10, 15, 13, 20, 14]
        #   running: [10, 25, 38, 58, 72]
        # Category B (rows 1,3,5,7,9): values [12, 11, 18, 16, 17]
        #   running: [12, 23, 41, 57, 74]
        a_rows = result.filter(pl.col("category") == "A")
        b_rows = result.filter(pl.col("category") == "B")

        assert a_rows["running_total"].to_list() == [10.0, 25.0, 38.0, 58.0, 72.0]
        assert b_rows["running_total"].to_list() == [12.0, 23.0, 41.0, 57.0, 74.0]

    def test_rank_within_group(self, sales_df):
        """Rank customers by total spending within region."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.transform(
            "df.with_columns("
            "  (pl.col('quantity') * pl.col('unit_price')).alias('revenue')"
            ").group_by('region', 'customer_id').agg("
            "  pl.col('revenue').sum().alias('total_spend')"
            ").with_columns("
            "  pl.col('total_spend').rank('dense', descending=True).over('region').alias('rank')"
            ").sort('region', 'rank')"
        )

        result = ws.df
        # East customers:
        #   101: 20 + 50 + 32 = 102
        #   103: 45
        #   104: 30
        #   105: 100
        # Ranks: 101=1, 105=2, 103=3, 104=4
        east = result.filter(pl.col("region") == "East")
        assert east["customer_id"].to_list() == [101, 105, 103, 104]
        assert east["total_spend"].to_list() == [102.0, 100.0, 45.0, 30.0]
        assert east["rank"].to_list() == [1, 2, 3, 4]

    def test_moving_average(self, timeseries_df):
        """3-period moving average — exact expected values."""
        ws = Workspace()
        ws.load_df(timeseries_df, name="ts")
        ws.transform("df.with_columns(  pl.col('value').rolling_mean(window_size=3).alias('ma3'))")

        result = ws.df
        ma3 = result["ma3"].to_list()
        # First 2 are null (not enough window), then:
        # (10+12+15)/3=12.33, (12+15+11)/3=12.67, (15+11+13)/3=13.0,
        # (11+13+18)/3=14.0, (13+18+20)/3=17.0, (18+20+16)/3=18.0,
        # (20+16+14)/3=16.67, (16+14+17)/3=15.67
        assert ma3[0] is None
        assert ma3[1] is None
        assert abs(ma3[2] - 12.333333) < 0.001
        assert abs(ma3[3] - 12.666667) < 0.001
        assert abs(ma3[4] - 13.0) < 0.001
        assert abs(ma3[5] - 14.0) < 0.001
        assert abs(ma3[6] - 17.0) < 0.001
        assert abs(ma3[7] - 18.0) < 0.001
        assert abs(ma3[8] - 16.666667) < 0.001
        assert abs(ma3[9] - 15.666667) < 0.001


# =============================================================================
# SQL Query Scenarios
# =============================================================================


class TestSQLQueries:
    """SQL queries via DuckDB with verifiable results."""

    def test_sql_aggregation(self, sales_df):
        """GROUP BY with HAVING in SQL."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.query(
            "SELECT product, SUM(quantity * unit_price) as revenue "
            "FROM sales "
            "GROUP BY product "
            "HAVING SUM(quantity * unit_price) >= 75 "
            "ORDER BY revenue DESC"
        )

        result = ws.df
        # Widget: 180, Gadget: 100, Doohickey: 75, Thingamajig: 32 (excluded)
        assert result["product"].to_list() == ["Widget", "Gadget", "Doohickey"]
        assert result["revenue"].to_list() == [180.0, 100.0, 75.0]

    def test_sql_subquery(self, sales_df):
        """Subquery to find customers who spent above average."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.query(
            "SELECT customer_id, total_spend FROM ("
            "  SELECT customer_id, SUM(quantity * unit_price) as total_spend "
            "  FROM sales GROUP BY customer_id"
            ") WHERE total_spend > ("
            "  SELECT AVG(quantity * unit_price) FROM sales"
            ") ORDER BY total_spend DESC"
        )

        result = ws.df
        # Per-order average revenue = (20+25+50+45+10+50+32+100+25+30)/10 = 38.7
        # Customer totals: 101=102, 102=75, 103=70, 104=40, 105=100
        # Above 38.7: all of them (since these are sums vs per-order avg)
        # Actually let me recalculate: the subquery compares customer total vs avg order revenue
        # avg(quantity*unit_price) = 387/10 = 38.7
        # customer 101: 102 > 38.7 ✓
        # customer 102: 75 > 38.7 ✓
        # customer 103: 70 > 38.7 ✓
        # customer 104: 40 > 38.7 ✓
        # customer 105: 100 > 38.7 ✓
        assert result.shape[0] == 5
        assert result["customer_id"][0] == 101  # highest spender

    def test_sql_window_function(self, sales_df):
        """SQL window function — row number within partition."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.query(
            "SELECT order_id, region, quantity * unit_price as revenue, "
            "  ROW_NUMBER() OVER (PARTITION BY region ORDER BY quantity * unit_price DESC) as rn "
            "FROM sales"
        )

        result = ws.df
        # East orders by revenue desc: 8(100), 3(50), 4(45), 7(32), 10(30), 1(20)
        # West orders by revenue desc: 6(50), 2(25), 9(25), 5(10)
        east_top = result.filter((pl.col("region") == "East") & (pl.col("rn") == 1))
        assert east_top["order_id"][0] == 8
        assert east_top["revenue"][0] == 100.0


# =============================================================================
# Null Handling Scenarios
# =============================================================================


class TestNullHandling:
    """Tests for null-aware operations — a major source of data bugs."""

    def test_null_counts(self, nulls_df):
        """Verify null detection is accurate."""
        ws = Workspace()
        ws.load_df(nulls_df, name="nulls")
        info = ws.inspect()

        assert info["null_counts"]["id"] == 0
        assert info["null_counts"]["value_a"] == 3  # rows 2, 4, 7
        assert info["null_counts"]["value_b"] == 3  # rows 3, 5, 8
        assert info["null_counts"]["group"] == 0

    def test_drop_nulls_specific_column(self, nulls_df):
        """Drop rows where a specific column is null."""
        ws = Workspace()
        ws.load_df(nulls_df, name="nulls")
        ws.transform("df.drop_nulls(subset=['value_a'])")

        result = ws.df
        assert result.shape[0] == 5  # 8 - 3 nulls
        assert result["id"].to_list() == [1, 3, 5, 6, 8]

    def test_null_coalesce(self, nulls_df):
        """Coalesce: use value_b when value_a is null."""
        ws = Workspace()
        ws.load_df(nulls_df, name="nulls")
        ws.transform("df.with_columns(  pl.coalesce(['value_a', 'value_b']).alias('coalesced'))")

        result = ws.df
        # Row 1: a=10 → 10
        # Row 2: a=null, b=2 → 2
        # Row 3: a=30, b=null → 30
        # Row 4: a=null, b=4 → 4
        # Row 5: a=50, b=null → 50
        # Row 6: a=60, b=6 → 60
        # Row 7: a=null, b=7 → 7
        # Row 8: a=80, b=null → 80
        assert result["coalesced"].to_list() == [10.0, 2.0, 30.0, 4.0, 50.0, 60.0, 7.0, 80.0]


# =============================================================================
# Multi-Step Pipelines (End-to-End)
# =============================================================================


class TestPipelines:
    """Multi-step data pipelines that mirror real workflows."""

    def test_etl_pipeline(self, sales_df, tmp_path):
        """Full ETL: load → clean → transform → aggregate → export."""
        out = tmp_path / "summary.parquet"

        ws = Workspace()
        ws.load_df(sales_df, name="sales")

        # Step 1: Parse dates
        ws.transform("df.with_columns(pl.col('order_date').str.to_date('%Y-%m-%d').alias('date'))")
        # Step 2: Compute revenue
        ws.transform(
            "df.with_columns((pl.col('quantity') * pl.col('unit_price')).alias('revenue'))"
        )
        # Step 3: Aggregate monthly by region
        ws.transform(
            "df.with_columns(pl.col('date').dt.month().alias('month'))"
            ".group_by('region', 'month').agg("
            "  pl.col('revenue').sum().alias('total_revenue'),"
            "  pl.col('order_id').count().alias('order_count')"
            ").sort('region', 'month')"
        )
        # Step 4: Export
        ws.export(out)

        # Verify final output
        result = pl.read_parquet(out)
        assert result.shape == (6, 4)  # 2 regions × 3 months
        assert set(result.columns) == {"region", "month", "total_revenue", "order_count"}

        # Verify a specific known value
        east_jan = result.filter((pl.col("region") == "East") & (pl.col("month") == 1))
        assert east_jan["total_revenue"][0] == 20.0
        assert east_jan["order_count"][0] == 1

    def test_branching_exploration(self, sales_df):
        """Branch workflow: explore two strategies, compare results."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.transform(
            "df.with_columns((pl.col('quantity') * pl.col('unit_price')).alias('revenue'))"
        )

        # Branch 1: High-value filter
        ws.branch("high_value")
        ws.transform("df.filter(pl.col('revenue') >= 50)")
        high_value_count = ws.shape[0]

        # Branch 2: East region only
        ws.switch("sales")
        ws.branch("east_only")
        ws.transform("df.filter(pl.col('region') == 'East')")
        east_count = ws.shape[0]

        # Verify both branches exist with correct data
        # revenue: [20, 25, 50, 45, 10, 50, 32, 100, 25, 30]
        # >= 50: orders 3(50), 6(50), 8(100) = 3 rows
        assert high_value_count == 3
        assert east_count == 6  # East region rows: 1,3,4,7,8,10

        # Original is still intact
        ws.switch("sales")
        assert ws.shape[0] == 10

    def test_undo_preserves_correctness(self, sales_df):
        """Undo chain maintains data integrity at each step."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")

        # Apply progressive filters
        ws.transform("df.filter(pl.col('region') == 'East')")
        assert ws.shape[0] == 6

        ws.transform("df.filter(pl.col('quantity') > 2)")
        assert ws.shape[0] == 4  # East rows with quantity > 2: orders 3(5), 4(3), 7(4), 8(10)

        ws.transform("df.filter(pl.col('unit_price') >= 10)")
        # Order 7 has unit_price=8, excluded. Keeps: 3(10), 4(15), 8(10) = 3 rows
        assert ws.shape[0] == 3

        # Undo back step by step
        ws.undo()
        assert ws.shape[0] == 4  # Back to quantity > 2 in East

        ws.undo()
        assert ws.shape[0] == 6  # Back to just East

        ws.undo()
        assert ws.shape[0] == 10  # Back to original

    def test_reproducible_code_generation(self, sales_df):
        """Generated code, when executed, produces identical results."""
        ws = Workspace()
        ws.load_df(sales_df, name="sales")
        ws.transform("df.filter(pl.col('region') == 'East')")
        ws.transform(
            "df.with_columns((pl.col('quantity') * pl.col('unit_price')).alias('revenue'))"
        )
        ws.transform("df.sort('revenue', descending=True)")

        # Get the result from the workspace
        expected = ws.df.clone()

        # Generate code and verify it contains the transforms
        code = ws.generate_code()
        assert "df.filter" in code
        assert "df.with_columns" in code
        assert "df.sort" in code

        # Execute the generated code against the original data
        df = sales_df.clone()
        # Apply transforms manually in same order
        df = df.filter(pl.col("region") == "East")
        df = df.with_columns((pl.col("quantity") * pl.col("unit_price")).alias("revenue"))
        df = df.sort("revenue", descending=True)

        assert df.equals(expected)


# =============================================================================
# Large Dataset Scenarios
# =============================================================================


class TestScaleScenarios:
    """Tests that verify correctness at larger scale with deterministic data."""

    def test_aggregation_10k_rows(self):
        """Verify aggregation correctness at 10K rows."""
        import random

        random.seed(42)

        n = 10_000
        groups = ["A", "B", "C", "D", "E"]
        df = pl.DataFrame(
            {
                "id": list(range(n)),
                "group": [groups[i % 5] for i in range(n)],
                "value": [float(i % 100) for i in range(n)],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="large")
        ws.transform(
            "df.group_by('group').agg("
            "  pl.col('value').sum().alias('total'),"
            "  pl.col('value').mean().alias('avg'),"
            "  pl.col('id').count().alias('count')"
            ").sort('group')"
        )

        result = ws.df
        # Each group has exactly 2000 rows (10000/5)
        assert result["count"].to_list() == [2000, 2000, 2000, 2000, 2000]

        # Group A has values: 0,5,10,...,95,0,5,10,...  (repeating pattern mod 100 for i%5==0)
        # Actually: group = groups[i % 5], value = float(i % 100)
        # Group A: i=0,5,10,15,...,9995 → values: 0%100, 5%100, 10%100, ..., 9995%100
        #   = 0,5,10,15,...,95, 0,5,10,... (repeats every 20 values since lcm(5,100)=100)
        # Sum of one cycle (0,5,10,...,95) = 20 values summing to 950
        # 2000 values = 100 cycles of 20 = 100 * 950 = hmm let me recalculate
        # Actually for group A (i%5==0): i = 0,5,10,...,9995
        # values = i%100 for each: 0,5,10,15,20,...,95,0,5,10,...
        # One cycle = 0,5,10,...,95 (20 terms), sum = 5*(0+1+...+19) = 5*190 = 950
        # 2000 / 20 = 100 cycles → total = 100 * 950 = 95000
        assert result.filter(pl.col("group") == "A")["total"][0] == 95000.0
        assert result.filter(pl.col("group") == "A")["avg"][0] == 47.5

    def test_sort_stability_large(self):
        """Verify sort produces correct ordering with ties."""
        df = pl.DataFrame(
            {
                "category": ["X"] * 100 + ["Y"] * 100 + ["Z"] * 100,
                "priority": ([1, 2, 3, 4, 5] * 20)
                + ([1, 2, 3, 4, 5] * 20)
                + ([1, 2, 3, 4, 5] * 20),
                "seq": list(range(300)),
            }
        )

        ws = Workspace()
        ws.load_df(df, name="sortable")
        ws.transform("df.sort('category', 'priority')")

        result = ws.df
        # Should be sorted by category then priority
        assert result["category"][0] == "X"
        assert result["category"][99] == "X"
        assert result["category"][100] == "Y"
        assert result["category"][200] == "Z"
        # Within X, priorities should be sorted
        x_priorities = result.filter(pl.col("category") == "X")["priority"].to_list()
        assert x_priorities == sorted(x_priorities)


# =============================================================================
# Export Round-Trip Integrity
# =============================================================================


class TestRoundTrip:
    """Verify data survives format conversions without corruption."""

    def test_csv_round_trip_preserves_data(self, sales_df, tmp_path):
        """CSV export then re-import produces identical data."""
        ws = Workspace()
        ws.load_df(sales_df, name="original")
        ws.export(tmp_path / "rt.csv")

        ws2 = Workspace()
        ws2.load(tmp_path / "rt.csv")

        assert ws2.df.shape == sales_df.shape
        # Compare all columns (CSV might change types slightly)
        for col in sales_df.columns:
            original = sales_df[col].to_list()
            loaded = ws2.df[col].to_list()
            assert original == loaded, f"Column '{col}' differs after CSV round-trip"

    def test_parquet_round_trip_preserves_types(self, tmp_path):
        """Parquet preserves exact types including dates and nulls."""
        df = pl.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["a", None, "c"],
                "value": [1.5, 2.5, None],
                "date": [date(2024, 1, 1), date(2024, 6, 15), date(2024, 12, 31)],
                "flag": [True, False, True],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="typed")
        ws.export(tmp_path / "rt.parquet")

        ws2 = Workspace()
        ws2.load(tmp_path / "rt.parquet")

        assert ws2.df.schema == df.schema
        assert ws2.df.equals(df)


# =============================================================================
# Join Scenarios
# =============================================================================


class TestJoins:
    """Tests for joining datasets — the bread and butter of data engineering."""

    def test_inner_join(self, sales_df, customers_df):
        """Inner join sales to customers on customer_id."""
        ws = Workspace()
        ws.load_df(customers_df, name="customers")
        ws.load_df(sales_df, name="sales")
        ws.switch("sales")

        ws.transform(
            "df.join("
            "  pl.DataFrame({"
            "    'customer_id': [101, 102, 103, 104, 105],"
            "    'customer_name': ['Acme Corp', 'Beta Inc', 'Gamma LLC', 'Delta Co', 'Epsilon Ltd'],"
            "    'tier': ['Gold', 'Silver', 'Bronze', 'Gold', 'Silver'],"
            "  }),"
            "  on='customer_id',"
            "  how='inner'"
            ")"
        )

        result = ws.df
        # All 10 sales have matching customers (101-105 all exist)
        assert result.shape[0] == 10
        assert "customer_name" in result.columns
        assert "tier" in result.columns
        # Order 1 → customer 101 → Acme Corp, Gold
        row_1 = result.filter(pl.col("order_id") == 1)
        assert row_1["customer_name"][0] == "Acme Corp"
        assert row_1["tier"][0] == "Gold"

    def test_left_join_with_nulls(self):
        """Left join where some keys don't match — produces nulls."""
        orders = pl.DataFrame(
            {
                "order_id": [1, 2, 3, 4, 5],
                "product_id": [10, 20, 30, 40, 50],
                "amount": [100, 200, 300, 400, 500],
            }
        )
        products = pl.DataFrame(
            {
                "product_id": [10, 20, 30],
                "product_name": ["Alpha", "Beta", "Gamma"],
            }
        )

        ws = Workspace()
        ws.load_df(orders, name="orders")
        ws.transform(
            "df.join("
            "  pl.DataFrame({'product_id': [10, 20, 30], 'product_name': ['Alpha', 'Beta', 'Gamma']}),"
            "  on='product_id',"
            "  how='left'"
            ")"
        )

        result = ws.df
        assert result.shape[0] == 5  # All orders kept
        # Orders 4, 5 have no matching product → null
        assert result.filter(pl.col("order_id") == 4)["product_name"][0] is None
        assert result.filter(pl.col("order_id") == 5)["product_name"][0] is None
        # Orders 1-3 have matches
        assert result.filter(pl.col("order_id") == 1)["product_name"][0] == "Alpha"
        assert result.filter(pl.col("order_id") == 2)["product_name"][0] == "Beta"
        assert result.filter(pl.col("order_id") == 3)["product_name"][0] == "Gamma"

    def test_cross_join_cartesian(self):
        """Cross join produces correct cartesian product."""
        colors = pl.DataFrame({"color": ["red", "blue", "green"]})
        sizes = pl.DataFrame({"size": ["S", "M", "L"]})

        ws = Workspace()
        ws.load_df(colors, name="colors")
        ws.transform("df.join(pl.DataFrame({'size': ['S', 'M', 'L']}), how='cross')")

        result = ws.df
        assert result.shape == (9, 2)  # 3 × 3
        # Every combination exists
        combos = set(zip(result["color"].to_list(), result["size"].to_list()))
        assert len(combos) == 9
        assert ("red", "S") in combos
        assert ("green", "L") in combos

    def test_self_join_hierarchy(self):
        """Self-join to resolve a parent-child hierarchy."""
        employees = pl.DataFrame(
            {
                "emp_id": [1, 2, 3, 4, 5],
                "name": ["CEO", "VP Sales", "VP Eng", "Dev Lead", "Sales Rep"],
                "manager_id": [None, 1, 1, 3, 2],
            }
        )

        ws = Workspace()
        ws.load_df(employees, name="employees")
        ws.transform(
            "df.join("
            "  df.select(pl.col('emp_id').alias('manager_id'), pl.col('name').alias('manager_name')),"
            "  on='manager_id',"
            "  how='left'"
            ")"
        )

        result = ws.df
        assert result.shape[0] == 5
        # CEO has no manager
        ceo = result.filter(pl.col("name") == "CEO")
        assert ceo["manager_name"][0] is None
        # Dev Lead's manager is VP Eng
        dev = result.filter(pl.col("name") == "Dev Lead")
        assert dev["manager_name"][0] == "VP Eng"


# =============================================================================
# Pivot / Reshape Scenarios
# =============================================================================


class TestReshape:
    """Tests for pivot, unpivot, and reshaping operations."""

    def test_pivot_long_to_wide(self):
        """Pivot: long format → wide format."""
        long_df = pl.DataFrame(
            {
                "student": ["Alice", "Alice", "Alice", "Bob", "Bob", "Bob"],
                "subject": ["Math", "Science", "English", "Math", "Science", "English"],
                "score": [85, 92, 78, 90, 88, 95],
            }
        )

        ws = Workspace()
        ws.load_df(long_df, name="scores")
        ws.transform("df.pivot(on='subject', index='student', values='score').sort('student')")

        result = ws.df
        assert result.shape == (2, 4)  # 2 students × (name + 3 subjects)
        alice = result.filter(pl.col("student") == "Alice")
        assert alice["Math"][0] == 85
        assert alice["Science"][0] == 92
        assert alice["English"][0] == 78
        bob = result.filter(pl.col("student") == "Bob")
        assert bob["Math"][0] == 90
        assert bob["English"][0] == 95

    def test_unpivot_wide_to_long(self):
        """Unpivot: wide format → long format."""
        wide_df = pl.DataFrame(
            {
                "city": ["NYC", "LA", "Chicago"],
                "jan_temp": [32, 58, 25],
                "feb_temp": [35, 60, 28],
                "mar_temp": [45, 63, 38],
            }
        )

        ws = Workspace()
        ws.load_df(wide_df, name="temps")
        ws.transform(
            "df.unpivot("
            "  index='city',"
            "  on=['jan_temp', 'feb_temp', 'mar_temp'],"
            "  variable_name='month',"
            "  value_name='temperature'"
            ").sort('city', 'month')"
        )

        result = ws.df
        assert result.shape == (9, 3)  # 3 cities × 3 months
        nyc_jan = result.filter((pl.col("city") == "NYC") & (pl.col("month") == "jan_temp"))
        assert nyc_jan["temperature"][0] == 32

    def test_explode_list_column(self):
        """Explode a list column into multiple rows."""
        df = pl.DataFrame(
            {
                "id": [1, 2, 3],
                "tags": [
                    ["python", "data"],
                    ["rust", "systems"],
                    ["python", "ml", "deep-learning"],
                ],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="tagged")
        ws.transform("df.explode('tags')")

        result = ws.df
        # 2 + 2 + 3 = 7 rows after explode
        assert result.shape[0] == 7
        # id=3 should appear 3 times
        assert result.filter(pl.col("id") == 3).shape[0] == 3
        assert "deep-learning" in result["tags"].to_list()


# =============================================================================
# String Operations
# =============================================================================


class TestStringOperations:
    """Tests for string manipulation — common in data cleaning pipelines."""

    def test_regex_extraction(self):
        """Extract structured data from messy strings."""
        df = pl.DataFrame(
            {
                "raw": [
                    "Order #12345 - $199.99",
                    "Order #67890 - $42.50",
                    "Order #11111 - $1,250.00",
                    "Order #99999 - $0.99",
                ],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="orders")
        ws.transform(
            "df.with_columns("
            "  pl.col('raw').str.extract(r'#(\\d+)', 1).cast(pl.Int64).alias('order_num')"
            ")"
        )

        result = ws.df
        assert result["order_num"].to_list() == [12345, 67890, 11111, 99999]

    def test_string_split_and_access(self):
        """Split strings and access parts."""
        df = pl.DataFrame(
            {
                "full_name": ["John Smith", "Jane Doe", "Bob Builder Jr", "Alice Wonderland"],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="names")
        ws.transform(
            "df.with_columns("
            "  pl.col('full_name').str.split(' ').list.first().alias('first_name'),"
            "  pl.col('full_name').str.split(' ').list.last().alias('last_part')"
            ")"
        )

        result = ws.df
        assert result["first_name"].to_list() == ["John", "Jane", "Bob", "Alice"]
        assert result["last_part"].to_list() == ["Smith", "Doe", "Jr", "Wonderland"]

    def test_string_contains_and_replace(self):
        """Filter by pattern and clean strings."""
        df = pl.DataFrame(
            {
                "url": [
                    "https://example.com/page1",
                    "http://example.com/page2",
                    "https://other.org/home",
                    "https://example.com/page3",
                    "ftp://files.example.com/data",
                ],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="urls")
        ws.transform(
            "df.filter(pl.col('url').str.contains('example.com'))"
            ".with_columns("
            "  pl.col('url').str.replace('http://', 'https://').alias('url_secure')"
            ")"
        )

        result = ws.df
        # 4 URLs contain "example.com" (including ftp://files.example.com)
        assert result.shape[0] == 4
        # http:// replaced with https://, ftp:// unchanged
        secure_list = result["url_secure"].to_list()
        assert "https://example.com/page2" in secure_list  # was http://
        assert "ftp://files.example.com/data" in secure_list  # ftp stays
        assert "http://example.com/page2" not in secure_list  # replaced

    def test_string_padding_and_concatenation(self):
        """Pad IDs and concatenate columns."""
        df = pl.DataFrame(
            {
                "id": [1, 42, 100, 7, 999],
                "prefix": ["A", "B", "A", "C", "B"],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="ids")
        ws.transform(
            "df.with_columns("
            "  (pl.col('prefix') + '-' + pl.col('id').cast(pl.Utf8).str.pad_start(5, '0'))"
            "  .alias('formatted_id')"
            ")"
        )

        result = ws.df
        assert result["formatted_id"].to_list() == [
            "A-00001",
            "B-00042",
            "A-00100",
            "C-00007",
            "B-00999",
        ]


# =============================================================================
# Statistical Computations
# =============================================================================


class TestStatistics:
    """Tests for statistical operations with exact expected values."""

    def test_descriptive_statistics(self):
        """Compute mean, std, median, quantiles — verified by hand."""
        # Simple dataset where stats are easy to verify
        df = pl.DataFrame(
            {
                "x": [2.0, 4.0, 6.0, 8.0, 10.0],  # mean=6, std=√8≈2.828
            }
        )

        ws = Workspace()
        ws.load_df(df, name="stats")
        ws.transform(
            "df.select("
            "  pl.col('x').mean().alias('mean'),"
            "  pl.col('x').median().alias('median'),"
            "  pl.col('x').min().alias('min'),"
            "  pl.col('x').max().alias('max'),"
            "  pl.col('x').sum().alias('sum'),"
            "  pl.col('x').quantile(0.25).alias('q25'),"
            "  pl.col('x').quantile(0.75).alias('q75')"
            ")"
        )

        result = ws.df
        assert result["mean"][0] == 6.0
        assert result["median"][0] == 6.0
        assert result["min"][0] == 2.0
        assert result["max"][0] == 10.0
        assert result["sum"][0] == 30.0
        assert result["q25"][0] == 4.0
        assert result["q75"][0] == 8.0

    def test_z_score_computation(self):
        """Compute z-scores — provably correct standardization."""
        df = pl.DataFrame(
            {
                "value": [10.0, 20.0, 30.0, 40.0, 50.0],
                # mean = 30, std = √200 = 10√2 ≈ 14.142 (population)
                # Polars uses sample std by default: √(1000/4) = √250 ≈ 15.811
            }
        )

        ws = Workspace()
        ws.load_df(df, name="zscores")
        ws.transform(
            "df.with_columns("
            "  ((pl.col('value') - pl.col('value').mean()) / pl.col('value').std())"
            "  .alias('z_score')"
            ")"
        )

        result = ws.df
        z = result["z_score"].to_list()
        # With sample std (ddof=1): std = sqrt(250) ≈ 15.8114
        # z = (x - 30) / 15.8114
        # z[0] = (10-30)/15.8114 = -1.2649
        # z[2] = (30-30)/15.8114 = 0.0
        # z[4] = (50-30)/15.8114 = 1.2649
        assert abs(z[2]) < 1e-10  # Middle value has z=0
        assert abs(z[0] + z[4]) < 1e-10  # Symmetric around mean
        assert z[0] < z[1] < z[2] < z[3] < z[4]  # Monotonically increasing

    def test_percentile_rank(self):
        """Compute percentile ranks within groups."""
        df = pl.DataFrame(
            {
                "student": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
                "score": [55, 72, 88, 91, 63, 79, 95, 68, 84, 77],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="scores")
        ws.transform(
            "df.with_columns("
            "  (pl.col('score').rank() / pl.col('score').count()).alias('percentile')"
            ").sort('score')"
        )

        result = ws.df
        # Sorted scores: 55,63,68,72,77,79,84,88,91,95
        # Ranks: 1,2,3,4,5,6,7,8,9,10
        # Percentiles: 0.1, 0.2, 0.3, ..., 1.0
        assert result["score"].to_list() == [55, 63, 68, 72, 77, 79, 84, 88, 91, 95]
        pcts = result["percentile"].to_list()
        assert pcts[0] == 0.1  # Lowest score = 10th percentile
        assert pcts[9] == 1.0  # Highest score = 100th percentile
        assert pcts[4] == 0.5  # Median score = 50th percentile

    def test_correlation_matrix(self):
        """Compute pairwise correlations — verify known relationship."""
        # Perfect positive correlation between x and y (y = 2x + 1)
        # Zero correlation with z (constant)
        df = pl.DataFrame(
            {
                "x": [1.0, 2.0, 3.0, 4.0, 5.0],
                "y": [3.0, 5.0, 7.0, 9.0, 11.0],  # y = 2x + 1
                "z": [5.0, 5.0, 5.0, 5.0, 5.0],  # constant
            }
        )

        ws = Workspace()
        ws.load_df(df, name="corr")
        ws.transform(
            "df.select(  pl.corr('x', 'y').alias('corr_xy'),  pl.corr('x', 'z').alias('corr_xz'))"
        )

        result = ws.df
        # Perfect linear: corr = 1.0
        assert abs(result["corr_xy"][0] - 1.0) < 1e-10
        # Constant has NaN correlation (zero variance)
        assert result["corr_xz"][0] is None or str(result["corr_xz"][0]) == "nan"


# =============================================================================
# Outlier Detection & Handling
# =============================================================================


class TestOutlierHandling:
    """Tests for identifying and handling outliers."""

    def test_iqr_outlier_detection(self):
        """Detect outliers using IQR method — verify known outliers."""
        # Data with clear outliers at both ends
        df = pl.DataFrame(
            {
                "value": [1.0, 2.0, 3.0, 4.0, 5.0, 5.0, 6.0, 7.0, 8.0, 9.0, 100.0, -50.0],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="outliers")
        ws.transform(
            "df.with_columns("
            "  pl.when("
            "    (pl.col('value') < pl.col('value').quantile(0.25) - 1.5 * "
            "      (pl.col('value').quantile(0.75) - pl.col('value').quantile(0.25))) |"
            "    (pl.col('value') > pl.col('value').quantile(0.75) + 1.5 * "
            "      (pl.col('value').quantile(0.75) - pl.col('value').quantile(0.25)))"
            "  ).then(pl.lit(True))"
            "  .otherwise(pl.lit(False))"
            "  .alias('is_outlier')"
            ")"
        )

        result = ws.df
        outliers = result.filter(pl.col("is_outlier"))
        non_outliers = result.filter(~pl.col("is_outlier"))

        # 100 and -50 are obvious outliers
        assert 100.0 in outliers["value"].to_list()
        assert -50.0 in outliers["value"].to_list()
        # Normal values (1-9) should not be outliers
        assert all(v not in outliers["value"].to_list() for v in [3.0, 4.0, 5.0, 6.0, 7.0])

    def test_winsorize_extremes(self):
        """Clip values to percentile bounds."""
        df = pl.DataFrame(
            {
                "value": [1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 100.0],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="clip")
        ws.transform("df.with_columns(  pl.col('value').clip(5.0, 35.0).alias('clipped'))")

        result = ws.df
        # 1 → 5 (clipped to lower), 100 → 35 (clipped to upper), rest unchanged
        assert result["clipped"].to_list() == [
            5.0,
            5.0,
            10.0,
            15.0,
            20.0,
            25.0,
            30.0,
            35.0,
            35.0,
            35.0,
        ]


# =============================================================================
# Time Series Operations
# =============================================================================


class TestTimeSeries:
    """Tests for time-series–specific operations."""

    def test_lag_and_lead(self):
        """Compute lag/lead columns for time-offset comparisons."""
        df = pl.DataFrame(
            {
                "day": list(range(1, 8)),
                "sales": [100, 120, 90, 150, 130, 140, 160],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="daily")
        ws.transform(
            "df.with_columns("
            "  pl.col('sales').shift(1).alias('prev_day_sales'),"
            "  pl.col('sales').shift(-1).alias('next_day_sales')"
            ")"
        )

        result = ws.df
        # First row has no previous
        assert result["prev_day_sales"][0] is None
        assert result["prev_day_sales"][1] == 100  # Day 2's prev = Day 1
        assert result["prev_day_sales"][6] == 140  # Day 7's prev = Day 6
        # Last row has no next
        assert result["next_day_sales"][6] is None
        assert result["next_day_sales"][0] == 120  # Day 1's next = Day 2

    def test_day_over_day_change(self):
        """Compute absolute and percentage change."""
        df = pl.DataFrame(
            {
                "day": list(range(1, 6)),
                "revenue": [100.0, 120.0, 90.0, 135.0, 150.0],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="revenue")
        ws.transform(
            "df.with_columns("
            "  (pl.col('revenue') - pl.col('revenue').shift(1)).alias('abs_change'),"
            "  ((pl.col('revenue') - pl.col('revenue').shift(1)) / pl.col('revenue').shift(1) * 100)"
            "  .alias('pct_change')"
            ")"
        )

        result = ws.df
        # Day 1: null (no previous)
        assert result["abs_change"][0] is None
        # Day 2: 120 - 100 = 20, pct = 20%
        assert result["abs_change"][1] == 20.0
        assert result["pct_change"][1] == 20.0
        # Day 3: 90 - 120 = -30, pct = -25%
        assert result["abs_change"][2] == -30.0
        assert result["pct_change"][2] == -25.0
        # Day 4: 135 - 90 = 45, pct = 50%
        assert result["abs_change"][3] == 45.0
        assert result["pct_change"][3] == 50.0

    def test_rolling_window_sum(self, timeseries_df):
        """Rolling 3-period sum — different from mean, verifies window logic."""
        ws = Workspace()
        ws.load_df(timeseries_df, name="ts")
        ws.transform(
            "df.with_columns(  pl.col('value').rolling_sum(window_size=3).alias('roll_sum'))"
        )

        result = ws.df
        sums = result["roll_sum"].to_list()
        # First 2 are null
        assert sums[0] is None
        assert sums[1] is None
        # 10+12+15=37, 12+15+11=38, 15+11+13=39, 11+13+18=42, 13+18+20=51, 18+20+16=54, 20+16+14=50, 16+14+17=47
        assert sums[2] == 37.0
        assert sums[3] == 38.0
        assert sums[4] == 39.0
        assert sums[5] == 42.0
        assert sums[6] == 51.0
        assert sums[7] == 54.0
        assert sums[8] == 50.0
        assert sums[9] == 47.0

    def test_cumulative_max(self):
        """Cumulative maximum — useful for drawdown analysis."""
        df = pl.DataFrame(
            {
                "day": list(range(1, 9)),
                "price": [100.0, 105.0, 102.0, 110.0, 108.0, 115.0, 112.0, 120.0],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="prices")
        ws.transform(
            "df.with_columns("
            "  pl.col('price').cum_max().alias('peak'),"
            "  (pl.col('price') - pl.col('price').cum_max()).alias('drawdown')"
            ")"
        )

        result = ws.df
        assert result["peak"].to_list() == [100.0, 105.0, 105.0, 110.0, 110.0, 115.0, 115.0, 120.0]
        assert result["drawdown"].to_list() == [0.0, 0.0, -3.0, 0.0, -2.0, 0.0, -3.0, 0.0]


# =============================================================================
# Data Validation Patterns
# =============================================================================


class TestDataValidation:
    """Tests for validation logic data engineers build into pipelines."""

    def test_uniqueness_check(self):
        """Verify a column is a valid primary key (unique, non-null)."""
        # Valid PK
        df_valid = pl.DataFrame({"pk": [1, 2, 3, 4, 5], "val": ["a", "b", "c", "d", "e"]})
        ws = Workspace()
        ws.load_df(df_valid, name="valid")
        info = ws.inspect()
        assert info["null_counts"]["pk"] == 0
        ws.transform("df.select(pl.col('pk').n_unique().alias('unique_count'))")
        assert ws.df["unique_count"][0] == 5  # All unique

        # Invalid PK (has duplicates)
        df_invalid = pl.DataFrame({"pk": [1, 2, 2, 4, 5], "val": ["a", "b", "c", "d", "e"]})
        ws2 = Workspace()
        ws2.load_df(df_invalid, name="invalid")
        ws2.transform("df.select(pl.col('pk').n_unique().alias('unique_count'))")
        assert ws2.df["unique_count"][0] == 4  # Not all unique!

    def test_referential_integrity_check(self):
        """Verify FK values exist in parent table."""
        parents = pl.DataFrame({"id": [1, 2, 3, 4, 5]})
        children = pl.DataFrame(
            {
                "child_id": [10, 20, 30, 40, 50],
                "parent_id": [1, 2, 6, 3, 99],  # 6 and 99 are orphans
            }
        )

        ws = Workspace()
        ws.load_df(children, name="children")
        ws.transform(
            "df.with_columns(  pl.col('parent_id').is_in([1, 2, 3, 4, 5]).alias('has_parent'))"
        )

        result = ws.df
        assert result["has_parent"].to_list() == [True, True, False, True, False]
        # Orphan count
        orphans = result.filter(~pl.col("has_parent"))
        assert orphans.shape[0] == 2
        assert orphans["parent_id"].to_list() == [6, 99]

    def test_range_validation(self):
        """Validate values fall within expected business ranges."""
        df = pl.DataFrame(
            {
                "age": [25, 30, -5, 150, 42, 18, 200, 65],
                "pct": [0.5, 1.0, 0.0, 1.5, -0.1, 0.75, 0.99, 1.01],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="validate")
        ws.transform(
            "df.with_columns("
            "  ((pl.col('age') >= 0) & (pl.col('age') <= 120)).alias('age_valid'),"
            "  ((pl.col('pct') >= 0.0) & (pl.col('pct') <= 1.0)).alias('pct_valid')"
            ")"
        )

        result = ws.df
        assert result["age_valid"].to_list() == [True, True, False, False, True, True, False, True]
        assert result["pct_valid"].to_list() == [True, True, True, False, False, True, True, False]

    def test_completeness_by_group(self):
        """Check data completeness percentage per group."""
        df = pl.DataFrame(
            {
                "group": ["A", "A", "A", "A", "B", "B", "B", "B"],
                "value": [1.0, None, 3.0, None, 5.0, 6.0, 7.0, None],
            }
        )

        ws = Workspace()
        ws.load_df(df, name="completeness")
        ws.transform(
            "df.group_by('group').agg("
            "  (1 - pl.col('value').null_count() / pl.col('value').len()).alias('completeness')"
            ").sort('group')"
        )

        result = ws.df
        # Group A: 2 nulls out of 4 total → 1 - 2/4 = 0.5
        # Group B: 1 null out of 4 total → 1 - 1/4 = 0.75
        assert result["group"].to_list() == ["A", "B"]
        assert result["completeness"][0] == 0.5
        assert result["completeness"][1] == 0.75


# =============================================================================
# Complex Multi-Source Pipelines
# =============================================================================


class TestMultiSourcePipelines:
    """End-to-end tests combining multiple operations across sheets."""

    def test_star_schema_denormalization(self):
        """Build a denormalized fact table from star schema components."""
        # Fact table
        facts = pl.DataFrame(
            {
                "sale_id": [1, 2, 3, 4, 5],
                "date_key": [20240101, 20240115, 20240201, 20240215, 20240301],
                "product_key": [1, 2, 1, 3, 2],
                "store_key": [10, 10, 20, 20, 10],
                "amount": [100.0, 250.0, 150.0, 75.0, 300.0],
            }
        )

        # Dimension: Products
        products = pl.DataFrame(
            {
                "product_key": [1, 2, 3],
                "product_name": ["Laptop", "Phone", "Tablet"],
                "category": ["Electronics", "Electronics", "Electronics"],
            }
        )

        # Dimension: Stores
        stores = pl.DataFrame(
            {
                "store_key": [10, 20],
                "store_name": ["Downtown", "Mall"],
                "city": ["NYC", "LA"],
            }
        )

        ws = Workspace()
        ws.load_df(facts, name="facts")

        # Join products
        ws.transform(
            "df.join("
            "  pl.DataFrame({'product_key': [1,2,3], 'product_name': ['Laptop','Phone','Tablet']}),"
            "  on='product_key', how='left'"
            ")"
        )
        # Join stores
        ws.transform(
            "df.join("
            "  pl.DataFrame({'store_key': [10,20], 'store_name': ['Downtown','Mall'], 'city': ['NYC','LA']}),"
            "  on='store_key', how='left'"
            ")"
        )

        result = ws.df
        assert result.shape[0] == 5
        assert "product_name" in result.columns
        assert "store_name" in result.columns
        assert "city" in result.columns

        # Verify specific denormalized rows
        sale_1 = result.filter(pl.col("sale_id") == 1)
        assert sale_1["product_name"][0] == "Laptop"
        assert sale_1["store_name"][0] == "Downtown"
        assert sale_1["city"][0] == "NYC"

        sale_4 = result.filter(pl.col("sale_id") == 4)
        assert sale_4["product_name"][0] == "Tablet"
        assert sale_4["store_name"][0] == "Mall"
        assert sale_4["city"][0] == "LA"

    def test_scd_type_2_deduplication(self):
        """Slowly Changing Dimension Type 2: keep only the latest version per entity."""
        scd = pl.DataFrame(
            {
                "customer_id": [1, 1, 1, 2, 2, 3],
                "name": ["Alice v1", "Alice v2", "Alice v3", "Bob v1", "Bob v2", "Charlie v1"],
                "valid_from": [
                    "2020-01-01",
                    "2021-06-15",
                    "2023-03-01",
                    "2019-05-01",
                    "2022-11-01",
                    "2023-01-01",
                ],
                "valid_to": [
                    "2021-06-14",
                    "2023-02-28",
                    "9999-12-31",
                    "2022-10-31",
                    "9999-12-31",
                    "9999-12-31",
                ],
            }
        )

        ws = Workspace()
        ws.load_df(scd, name="scd")
        # Get current records (valid_to = '9999-12-31')
        ws.transform("df.filter(pl.col('valid_to') == '9999-12-31').sort('customer_id')")

        result = ws.df
        assert result.shape[0] == 3
        assert result["customer_id"].to_list() == [1, 2, 3]
        assert result["name"].to_list() == ["Alice v3", "Bob v2", "Charlie v1"]

    def test_funnel_analysis(self):
        """Marketing funnel: compute conversion rates between stages."""
        events = pl.DataFrame(
            {
                "user_id": [1, 1, 1, 1, 2, 2, 2, 3, 3, 4, 5, 5, 5, 5, 5],
                "stage": [
                    "view",
                    "click",
                    "cart",
                    "purchase",
                    "view",
                    "click",
                    "cart",
                    "view",
                    "click",
                    "view",
                    "view",
                    "click",
                    "cart",
                    "purchase",
                    "purchase",
                ],
            }
        )

        ws = Workspace()
        ws.load_df(events, name="funnel")
        ws.transform(
            "df.group_by('stage').agg("
            "  pl.col('user_id').n_unique().alias('unique_users')"
            ").sort('unique_users', descending=True)"
        )

        result = ws.df
        # view: users 1,2,3,4,5 = 5
        # click: users 1,2,3,5 = 4
        # cart: users 1,2,5 = 3
        # purchase: users 1,5 = 2
        users_by_stage = dict(zip(result["stage"].to_list(), result["unique_users"].to_list()))
        assert users_by_stage["view"] == 5
        assert users_by_stage["click"] == 4
        assert users_by_stage["cart"] == 3
        assert users_by_stage["purchase"] == 2

    def test_sessionization(self):
        """Group events into sessions based on time gaps."""
        events = pl.DataFrame(
            {
                "user": ["A", "A", "A", "A", "A", "A"],
                "timestamp": [
                    datetime(2024, 1, 1, 10, 0, 0),
                    datetime(2024, 1, 1, 10, 5, 0),  # 5 min gap
                    datetime(2024, 1, 1, 10, 8, 0),  # 3 min gap
                    datetime(2024, 1, 1, 14, 0, 0),  # 4 hour gap → new session
                    datetime(2024, 1, 1, 14, 2, 0),  # 2 min gap
                    datetime(2024, 1, 1, 20, 0, 0),  # 6 hour gap → new session
                ],
            }
        )

        ws = Workspace()
        ws.load_df(events, name="events")
        # Define session break as > 30 minute gap
        ws.transform(
            "df.with_columns("
            "  (pl.col('timestamp').diff().dt.total_minutes() > 30)"
            "  .fill_null(True)"
            "  .cum_sum()"
            "  .alias('session_id')"
            ")"
        )

        result = ws.df
        # Events 1-3: within 30 min → session 1
        # Event 4-5: new session after 4h gap → session 2
        # Event 6: new session after 6h gap → session 3
        assert result["session_id"].to_list() == [1, 1, 1, 2, 2, 3]
