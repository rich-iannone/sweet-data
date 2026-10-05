"""Write a live sensor feed to an NDJSON file, for trying Sweet's live features.

    python examples/sensor_feed.py feed.ndjson        # one reading per sensor per tick
    sweet --follow feed.ndjson                        # in another pane

After `--drift-after` seconds, sensor s3 starts dropping readings (temp becomes null)
and drifting hot, so a null-rate or mean drift alert fires:

    sweet --follow feed.ndjson    then press A and enter:  temp_c < 45
    (or let an agent add {"column": "temp_c", "stat": "null_rate", "threshold": 0.1})
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime, timezone


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path")
    parser.add_argument("--sensors", type=int, default=5)
    parser.add_argument("--interval", type=float, default=0.25, help="Seconds between ticks")
    parser.add_argument(
        "--drift-after", type=float, default=20.0, help="Seconds before s3 misbehaves"
    )
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    start = time.monotonic()
    tick = 0
    with open(args.path, "a", buffering=1) as feed:
        while True:
            elapsed = time.monotonic() - start
            for n in range(1, args.sensors + 1):
                sensor = f"s{n}"
                temp = 21 + 3 * math.sin(tick / 20 + n) + rng.gauss(0, 0.4)
                humidity = 45 + 10 * math.cos(tick / 30 + n) + rng.gauss(0, 1)
                if sensor == "s3" and elapsed > args.drift_after:
                    temp = None if rng.random() < 0.6 else temp + (elapsed - args.drift_after)
                reading = {
                    "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "sensor": sensor,
                    "temp_c": None if temp is None else round(temp, 2),
                    "humidity": round(humidity, 1),
                }
                feed.write(json.dumps(reading) + "\n")
            tick += 1
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
