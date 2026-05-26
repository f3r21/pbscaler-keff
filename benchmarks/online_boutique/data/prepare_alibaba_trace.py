"""Preprocess Alibaba cluster-trace-v2018 batch_task.csv into a tiny
60-row "user curve" suitable for the trace-driven Locust workload.

Why preprocess
--------------
batch_task.csv is ~800 MB / 14M rows. Loading + bucketing it on every
Locust startup costs ~60-90 s. This script does the work once and writes
a small file that the locustfile reads instantly.

Output schema (alibaba_60min_curve.csv):
    minute,raw_arrivals
    0,12345
    1,11020
    ...
    59,15203

Where:
- minute  : 0..59, position in the chosen 60-min window
- raw_arrivals : number of task starts in that 60-second bucket

The locustfile does its own scaling (peak → 3N).

Selection rule:
- Bucket all start_time values into 60-second buckets.
- The first bucket of the trace contains all tasks with start_time=0
  (pre-existing tasks recorded as starting at trace epoch). This is an
  artifact, not real arrival traffic. We skip it.
- The first ~60 min and last ~60 min are warm-up/cooldown periods with
  near-zero arrivals. We trim them.
- Among the trimmed buckets, slide a 60-bucket window and pick the one
  with the highest MEAN arrivals (not peak — single-spike windows are
  not representative of sustained high load).

Usage:
    python prepare_alibaba_trace.py [--in batch_task.csv] [--out alibaba_60min_curve.csv]
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Final

# batch_task.csv has NO header. Field order from cluster-trace-v2018/schema.txt
SCHEMA_FIELDS: Final[tuple[str, ...]] = (
    "task_name",
    "instance_num",
    "job_name",
    "task_type",
    "status",
    "start_time",
    "end_time",
    "plan_cpu",
    "plan_mem",
)

BUCKET_SIZE_S: Final[int] = 60
TOTAL_BUCKETS: Final[int] = 60   # 60 buckets × 60 s = 60 min window
TRIM_START_BUCKETS: Final[int] = 60  # skip warm-up artifact + first hour
TRIM_END_BUCKETS: Final[int] = 60    # skip cooldown


def bucket_arrivals(csv_path: Path) -> dict[int, int]:
    """Read the raw CSV and return {bucket_index: arrival_count}."""
    counts: dict[int, int] = defaultdict(int)
    with csv_path.open("r", newline="") as fh:
        reader = csv.DictReader(fh, fieldnames=list(SCHEMA_FIELDS))
        for row in reader:
            raw = row.get("start_time")
            if raw is None:
                continue
            try:
                t = int(float(raw))
            except (ValueError, TypeError):
                continue
            counts[t // BUCKET_SIZE_S] += 1
    return counts


def pick_best_window(counts: dict[int, int]) -> tuple[int, list[int]]:
    """Pick the 60-bucket window with the highest mean arrival rate, after
    trimming warm-up and cooldown.

    Returns (start_bucket, [60 raw_arrival counts]).
    """
    if not counts:
        raise ValueError("no buckets in dataset")
    all_buckets = sorted(counts)
    trimmed = all_buckets[TRIM_START_BUCKETS : len(all_buckets) - TRIM_END_BUCKETS]
    if len(trimmed) < TOTAL_BUCKETS:
        raise ValueError(
            f"after trim, only {len(trimmed)} buckets remain (need {TOTAL_BUCKETS})"
        )

    best_start = trimmed[0]
    best_mean = -1.0
    for i in range(len(trimmed) - TOTAL_BUCKETS + 1):
        window = trimmed[i : i + TOTAL_BUCKETS]
        mean_arr = sum(counts[b] for b in window) / TOTAL_BUCKETS
        if mean_arr > best_mean:
            best_mean = mean_arr
            best_start = trimmed[i]

    chosen = [counts[best_start + offset] for offset in range(TOTAL_BUCKETS)]
    return best_start, chosen


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="in_path", type=Path, default=here / "batch_task.csv",
                    help="raw Alibaba batch_task.csv (no header)")
    ap.add_argument("--out", dest="out_path", type=Path, default=here / "alibaba_60min_curve.csv",
                    help="preprocessed 60-row curve")
    args = ap.parse_args()

    if not args.in_path.exists():
        print(f"ERROR: input not found: {args.in_path}")
        return 1

    print(f"reading {args.in_path} (this can take 60-90 s for ~14M rows)...")
    counts = bucket_arrivals(args.in_path)
    print(f"  {len(counts)} unique 60-second buckets")
    print(f"  total arrivals: {sum(counts.values()):,}")

    start, curve = pick_best_window(counts)
    print(f"chose window starting at bucket {start} ({start * BUCKET_SIZE_S} s into the trace)")
    print(f"  trimmed warm-up={TRIM_START_BUCKETS} buckets, cooldown={TRIM_END_BUCKETS} buckets")
    print(f"  mean arrivals in window: {sum(curve) / len(curve):,.0f}")
    print(f"  peak arrivals in window: {max(curve):,}")
    print(f"  min  arrivals in window: {min(curve):,}")

    with args.out_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["minute", "raw_arrivals"])
        for i, n in enumerate(curve):
            writer.writerow([i, n])

    print(f"\nwrote {args.out_path} ({args.out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
