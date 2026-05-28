"""Generate large eval datasets.

Run this script to regenerate the large dataset files used by eval scenarios.
These files are gitignored to keep the repo lightweight.

Usage:
    python evals/generate_datasets.py
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

SEED = 42
DATASETS_DIR = Path(__file__).parent / "datasets"


def generate_large_transactions(n: int = 50_000) -> None:
    """Generate large_transactions.csv (~50K rows, ~5MB).

    Enterprise transactions with intentional quality issues:
    - ~1% duplicate rows (500 rows)
    - ~3% null descriptions
    - ~2% negative amounts (refunds)
    - Some 'failed' status rows
    """
    random.seed(SEED)

    categories = [
        "Technology",
        "Marketing",
        "Operations",
        "Legal",
        "Travel",
        "Payroll",
        "Training",
        "Food & Bev",
    ]
    regions = ["US-East", "US-West", "EU-North", "EU-South", "APAC", "LATAM"]
    payment_methods = ["credit_card", "wire", "ach", "check", "crypto"]
    statuses = ["completed", "pending", "failed", "refunded"]
    status_weights = [0.85, 0.07, 0.05, 0.03]

    words = [
        "service",
        "subscription",
        "purchase",
        "renewal",
        "license",
        "equipment",
        "travel",
        "meal",
        "conference",
        "software",
    ]

    start_date = datetime(2023, 1, 1)
    rows = []
    for i in range(1, n + 1):
        date = start_date + timedelta(days=random.randint(0, 729))
        amount = round(random.lognormvariate(5.0, 1.2), 2)
        # ~2% negative amounts
        if random.random() < 0.02:
            amount = -abs(amount)

        desc = " ".join(random.choices(words, k=random.randint(2, 5)))
        # ~3% null descriptions
        if random.random() < 0.03:
            desc = None

        rows.append(
            {
                "txn_id": f"TXN-{i:06d}",
                "date": date.strftime("%Y-%m-%d"),
                "amount": amount,
                "category": random.choice(categories),
                "region": random.choice(regions),
                "payment_method": random.choice(payment_methods),
                "status": random.choices(statuses, weights=status_weights, k=1)[0],
                "description": desc,
                "vendor_id": f"V{random.randint(1, 500):04d}",
            }
        )

    df = pl.DataFrame(rows)
    # Add ~1% duplicates
    n_dupes = n // 100
    dupe_indices = random.sample(range(n), n_dupes)
    dupes = df[dupe_indices]
    df = pl.concat([df, dupes])

    df.write_csv(DATASETS_DIR / "large_transactions.csv")
    print(f"large_transactions.csv: {df.shape[0]} rows × {df.shape[1]} cols")


def generate_large_sensor_telemetry(n: int = 100_000) -> None:
    """Generate large_sensor_telemetry.parquet (100K rows).

    IoT sensor data with:
    - 50 sensors
    - ~0.5% temperature outliers (values < 5 or > 40)
    - ~2% null humidity readings
    - Time range: 2024-01-01 to 2024-12-31
    """
    random.seed(SEED)

    n_sensors = 50
    sensor_ids = [f"SENSOR-{i:03d}" for i in range(1, n_sensors + 1)]

    start = datetime(2024, 1, 1)
    end = datetime(2024, 12, 31, 23, 50, 0)
    total_minutes = int((end - start).total_seconds() / 60)

    timestamps = []
    sensors = []
    temperatures = []
    humidities = []
    pressures = []
    batteries = []
    signals = []

    for i in range(n):
        sensor = random.choice(sensor_ids)
        ts = start + timedelta(minutes=random.randint(0, total_minutes))

        # Normal temperature: 15-30°C with noise
        temp = round(random.gauss(22.0, 5.0), 1)
        # ~0.5% outliers
        if random.random() < 0.005:
            temp = round(random.choice([-99.0, -50.0, 60.0, 80.0, 99.9]), 1)

        # Humidity: 30-80% with ~2% nulls
        humidity = round(random.gauss(55.0, 12.0), 1)
        if random.random() < 0.02:
            humidity = None

        pressure = round(random.gauss(1013.25, 5.0), 1)
        battery = round(random.uniform(2.8, 4.2), 2)
        signal = random.randint(-90, -30)

        sensors.append(sensor)
        timestamps.append(ts)
        temperatures.append(temp)
        humidities.append(humidity)
        pressures.append(pressure)
        batteries.append(battery)
        signals.append(signal)

    df = pl.DataFrame(
        {
            "sensor_id": sensors,
            "timestamp": timestamps,
            "temperature_c": temperatures,
            "humidity_pct": humidities,
            "pressure_hpa": pressures,
            "battery_v": batteries,
            "signal_strength_dbm": signals,
        }
    )
    df = df.sort("timestamp")
    df.write_parquet(DATASETS_DIR / "large_sensor_telemetry.parquet")
    print(f"large_sensor_telemetry.parquet: {df.shape[0]} rows × {df.shape[1]} cols")


def generate_large_multi_table(n_orders: int = 80_000) -> None:
    """Generate large_orders.csv, large_customers.csv, large_products.csv.

    Multi-table join dataset:
    - 5000 customers
    - 200 products
    - 80K orders referencing them
    """
    random.seed(SEED)

    # Customers
    n_customers = 5000
    regions = ["US-East", "US-West", "EU-North", "EU-South", "APAC"]
    tiers = ["bronze", "silver", "gold", "platinum"]
    tier_weights = [0.5, 0.3, 0.15, 0.05]

    customers = []
    for i in range(1, n_customers + 1):
        name_len = random.randint(4, 10)
        first = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=name_len)).capitalize()
        last = "".join(
            random.choices("abcdefghijklmnopqrstuvwxyz", k=random.randint(5, 12))
        ).capitalize()
        signup = datetime(2020, 1, 1) + timedelta(days=random.randint(0, 1825))
        customers.append(
            {
                "customer_id": f"CUST-{i:05d}",
                "customer_name": f"{first} {last}",
                "region": random.choice(regions),
                "tier": random.choices(tiers, weights=tier_weights, k=1)[0],
                "signup_date": signup.strftime("%Y-%m-%d"),
            }
        )
    df_customers = pl.DataFrame(customers)
    df_customers.write_csv(DATASETS_DIR / "large_customers.csv")
    print(f"large_customers.csv: {df_customers.shape[0]} rows")

    # Products
    n_products = 200
    product_categories = ["Hardware", "Software", "Services", "Support", "Consulting"]
    products = []
    for i in range(1, n_products + 1):
        code = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=2))
        products.append(
            {
                "product_id": f"PROD-{i:04d}",
                "product_name": f"Product {code}-{random.randint(100, 999)}",
                "unit_price": round(random.lognormvariate(3.0, 1.0), 2),
                "category": random.choice(product_categories),
            }
        )
    df_products = pl.DataFrame(products)
    df_products.write_csv(DATASETS_DIR / "large_products.csv")
    print(f"large_products.csv: {df_products.shape[0]} rows")

    # Orders
    customer_ids = [c["customer_id"] for c in customers]
    product_ids = [p["product_id"] for p in products]
    orders = []
    for i in range(1, n_orders + 1):
        order_date = datetime(2024, 1, 1) + timedelta(days=random.randint(0, 364))
        orders.append(
            {
                "order_id": f"ORD-{i:06d}",
                "customer_id": random.choice(customer_ids),
                "product_id": random.choice(product_ids),
                "quantity": random.randint(1, 20),
                "order_date": order_date.strftime("%Y-%m-%d"),
                "discount_pct": random.choice([0, 0, 0, 5, 10, 15, 20, 25]),
            }
        )
    df_orders = pl.DataFrame(orders)
    df_orders.write_csv(DATASETS_DIR / "large_orders.csv")
    print(f"large_orders.csv: {df_orders.shape[0]} rows")


def generate_large_wide_metrics(n: int = 10_000) -> None:
    """Generate large_wide_metrics.parquet (10K rows × 61 cols).

    Wide dataset with 50 numeric metric columns and 10 categorical columns.
    Tests agent's ability to select relevant columns from many.
    """
    random.seed(SEED)

    data: dict = {"id": list(range(1, n + 1))}
    # 50 numeric columns
    for i in range(50):
        data[f"metric_{i:02d}"] = [round(random.gauss(0, 1), 4) for _ in range(n)]
    # 10 categorical columns
    for i in range(10):
        data[f"cat_{i:02d}"] = [random.choice(["A", "B", "C", "D", "E"]) for _ in range(n)]

    df = pl.DataFrame(data)
    df.write_parquet(DATASETS_DIR / "large_wide_metrics.parquet")
    print(f"large_wide_metrics.parquet: {df.shape[0]} rows × {df.shape[1]} cols")


if __name__ == "__main__":
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    print("Generating large eval datasets...\n")
    generate_large_transactions()
    generate_large_sensor_telemetry()
    generate_large_multi_table()
    generate_large_wide_metrics()
    print("\nDone! All large datasets generated.")
