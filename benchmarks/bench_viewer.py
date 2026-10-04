"""Benchmark the viewer's responsiveness on large data.

Generates synthetic datasets (cached in --data-dir), then measures:

- open + first paint: time until the headless viewer has rendered its first rows
- row count:          time to count all rows
- stats:              time to compute header statistics for every column
- deep scroll:        time to fetch a window near the end of the data
- sorted window:      time to fetch the first window of a sorted view

Usage:
    python benchmarks/bench_viewer.py --rows 10_000_000 --data-dir /tmp/sweet-bench
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import time
from pathlib import Path

import polars as pl


def make_data(rows: int, data_dir: Path) -> dict[str, Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    parquet = data_dir / f"trips_{rows}.parquet"
    csv = data_dir / f"trips_{rows}.csv"
    if not parquet.exists():
        lf = pl.LazyFrame({"trip_id": pl.int_range(0, rows, eager=True)}).with_columns(
            vendor=pl.when(pl.col("trip_id") % 4 == 0)
            .then(pl.lit("CMT"))
            .when(pl.col("trip_id") % 4 == 1)
            .then(pl.lit("VTS"))
            .when(pl.col("trip_id") % 4 == 2)
            .then(pl.lit("DDS"))
            .otherwise(None),
            fare=((pl.col("trip_id") * 7919 % 10_000) / 100.0 - 3).round(2),
            passengers=(pl.col("trip_id") * 31 % 6 + 1).cast(pl.Int8),
            pickup=pl.lit(dt.datetime(2025, 1, 1))
            + pl.duration(seconds=pl.col("trip_id") * 37 % 31_536_000),
            distance=((pl.col("trip_id") * 104_729 % 5_000) / 250.0).round(3),
            note=pl.when(pl.col("trip_id") % 5 == 0)
            .then(pl.lit("late pickup, customer waited"))
            .when(pl.col("trip_id") % 7 == 0)
            .then(None)
            .otherwise(pl.lit("ok")),
        )
        lf.sink_parquet(parquet)
    if not csv.exists():
        pl.scan_parquet(parquet).sink_csv(csv)
    return {"parquet": parquet, "csv": csv}


def timed(fn) -> tuple[float, object]:
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


async def first_paint(path: Path) -> float:
    """Seconds from app start until the first data rows are rendered."""
    from sweet.ui.viewer import ViewerApp
    from sweet.ui.viewer.data_view import HEADER_LINES

    start = time.perf_counter()
    app = ViewerApp([str(path)])
    async with app.run_test(size=(140, 40)) as pilot:
        while True:
            grid = app.grid
            if app.view is not None and grid.render_line(HEADER_LINES).text.strip().split()[1:]:
                row = grid.current_row()
                if row is not None:
                    elapsed = time.perf_counter() - start
                    break
            await pilot.pause(0.005)
            if time.perf_counter() - start > 120:
                raise TimeoutError("first paint took over 120s")
    return elapsed


def bench(path: Path) -> dict[str, float]:
    from sweet import Workspace
    from sweet.core.view import TableView

    results: dict[str, float] = {}
    results["first paint (app)"] = asyncio.run(first_paint(path))

    ws = Workspace()
    results["open"], _ = timed(lambda: ws.read(path))
    view = TableView(ws, ws.current_sheet_name)
    results["first window"], _ = timed(lambda: view.fetch(0, 40))
    results["row count"], rows = timed(view.row_count)
    results["deep scroll window"], _ = timed(lambda: view.fetch(max(rows - 1000, 0), 40))
    results["stats (all columns)"], _ = timed(view.stats)
    view.set_sort([("fare", True)])
    results["sorted first window"], _ = timed(lambda: view.fetch(0, 40))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=lambda s: int(s.replace("_", "")), default=10_000_000)
    parser.add_argument("--data-dir", type=Path, default=Path("/tmp/sweet-bench"))
    parser.add_argument("--formats", default="parquet,csv")
    args = parser.parse_args()

    files = make_data(args.rows, args.data_dir)
    print(f"{args.rows:,} rows · polars {pl.__version__}\n")
    for fmt in args.formats.split(","):
        path = files[fmt]
        size = path.stat().st_size / 2**20
        print(f"{fmt} ({size:,.0f} MB)")
        for name, seconds in bench(path).items():
            print(f"  {name:<22} {seconds * 1000:>9,.0f} ms")
        print()


if __name__ == "__main__":
    main()
