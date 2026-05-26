"""
detect_ping_pong.py

Detects bottleneck ping-pong patterns from a PBScaler instances.csv.

A ping-pong event = scale-up followed by scale-down (or vice versa) on the
same service within `WINDOW_SECONDS`. This operationalizes the
"bottleneck ping-pong" failure mode described in Cap_2 (sec:coldstart) of
the thesis.

Usage:
    python detect_ping_pong.py PATH/TO/instances.csv [--window 120]

Outputs:
    - stdout summary: count and timing of detected ping-pong events
    - <input>.ping_pong.csv: rows for each detected event
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

WINDOW_SECONDS_DEFAULT = 120


def load_instances(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def detect_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Return one row per scaling action: (timestamp, service, delta)."""
    df = df.set_index("timestamp")
    services = [c for c in df.columns if c.endswith("&count")]
    events = []
    for svc in services:
        diffs = df[svc].diff().fillna(0)
        for ts, d in diffs.items():
            if d != 0:
                events.append({
                    "timestamp": ts,
                    "service": svc.replace("&count", ""),
                    "delta": int(d),
                    "from": int(df[svc].loc[ts] - d),
                    "to": int(df[svc].loc[ts]),
                })
    if not events:
        return pd.DataFrame(columns=["timestamp", "service", "delta", "from", "to"])
    return pd.DataFrame(events).sort_values("timestamp").reset_index(drop=True)


def find_ping_pong(events: pd.DataFrame, window_s: int) -> pd.DataFrame:
    """Pair adjacent up/down (or down/up) events on the same service within window."""
    if events.empty:
        return events
    rows = []
    for svc, grp in events.groupby("service"):
        grp = grp.reset_index(drop=True)
        for i in range(len(grp) - 1):
            a, b = grp.iloc[i], grp.iloc[i + 1]
            if a.delta * b.delta >= 0:
                continue
            dt = (b.timestamp - a.timestamp).total_seconds()
            if dt > window_s:
                continue
            rows.append({
                "service": svc,
                "first_event": a.timestamp,
                "first_delta": a.delta,
                "first_replicas": f"{a['from']} -> {a.to}",
                "second_event": b.timestamp,
                "second_delta": b.delta,
                "second_replicas": f"{b['from']} -> {b.to}",
                "interval_s": round(dt, 1),
            })
    return pd.DataFrame(rows)


def summarize(events: pd.DataFrame, ping_pong: pd.DataFrame, total_seconds: float) -> str:
    lines = []
    lines.append(f"Total scaling actions: {len(events)}")
    if not events.empty:
        ups = (events["delta"] > 0).sum()
        downs = (events["delta"] < 0).sum()
        lines.append(f"  scale-up:   {ups}")
        lines.append(f"  scale-down: {downs}")
        per_svc = events.groupby("service").size().sort_values(ascending=False)
        lines.append("  by service:")
        for svc, n in per_svc.items():
            lines.append(f"    {svc}: {n}")
    lines.append("")
    lines.append(f"Ping-pong events: {len(ping_pong)}")
    if not ping_pong.empty:
        per_svc = ping_pong.groupby("service").size().sort_values(ascending=False)
        for svc, n in per_svc.items():
            lines.append(f"  {svc}: {n}")
        lines.append("")
        lines.append("Sample ping-pong events:")
        for _, row in ping_pong.head(5).iterrows():
            lines.append(
                f"  {row.service} | "
                f"{row.first_event.time()} ({row.first_replicas}) -> "
                f"{row.second_event.time()} ({row.second_replicas}) | "
                f"interval {row.interval_s}s"
            )
    lines.append("")
    lines.append(f"Experiment duration: {total_seconds:.0f}s")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("instances_csv", type=Path)
    ap.add_argument("--window", type=int, default=WINDOW_SECONDS_DEFAULT,
                    help=f"ping-pong window in seconds (default {WINDOW_SECONDS_DEFAULT})")
    args = ap.parse_args()

    if not args.instances_csv.exists():
        print(f"file not found: {args.instances_csv}", file=sys.stderr)
        return 1

    df = load_instances(args.instances_csv)
    duration = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds()
    events = detect_changes(df)
    ping_pong = find_ping_pong(events, args.window)

    out_path = args.instances_csv.with_suffix(".ping_pong.csv")
    ping_pong.to_csv(out_path, index=False)

    print(f"=== {args.instances_csv} ===")
    print(summarize(events, ping_pong, duration))
    print(f"\nDetailed ping-pong events written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
