# Benchmarks

`bench_viewer.py` measures how responsive the viewer is on large data. It generates
synthetic trip data (7 columns: integers, strings with nulls, floats, a datetime) and
caches it in `--data-dir`.

```bash
python benchmarks/bench_viewer.py --rows 10_000_000 --data-dir /tmp/sweet-bench
```

## Results

10,000,000 rows, Polars 1.32.0, Apple Silicon laptop (Oct 2026):

| Measurement | Parquet (43 MB, lazy) | CSV (578 MB, lazy) |
|---|---:|---:|
| First paint (headless app start → first rows rendered) | 177 ms | 232 ms |
| Open (resolve schema) | 1 ms | 5 ms |
| First window (40 rows) | 13 ms | 75 ms |
| Row count | 2 ms | 23 ms |
| Deep scroll window (near the end) | 35 ms | 287 ms |
| Stats for every column (background) | 1.5 s | 3.4 s |
| Sorted first window | 0.74 s | 1.1 s |

The M1 target is first paint under 300 ms for any file size.

## Known gaps

- Each new window of a *sorted lazy* view repeats the sort (a top-k query), so paging
  through a sorted 10M-row view costs about 0.7 s per 256 rows. In-memory sheets sort once
  and reuse the result.
- Comparisons with VisiData, tabiew, and csvlens still need those tools installed and
  a matching "time to first paint" measurement for each.
