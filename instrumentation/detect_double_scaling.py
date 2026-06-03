"""Detect double-scaling events from PBScaler instances.csv + phantom_capacity.csv.

A *double scaling* event = scale-up action on a service while replicas
from a previous scale-up are still not Ready (i.e. phantom capacity > 0
in the recent past for the same service). This is the failure mode that
Cap_2 sec:coldstart claims `k_eff` mitigates.

Operationalisation (matches PLAN_CLAUDE_CODE.md C.2):
    For each scale-up event in instances.csv:
        if phantom_capacity.csv shows delta > 0 for the SAME service in
        the WINDOW_S seconds preceding the scale-up timestamp,
        mark this scale-up as a double-scaling event.

instances.csv format (PBScaler output):
    timestamp,<svc1>&count,<svc2>&count,...

phantom_capacity.csv format (measure_phantom_capacity.py):
    timestamp,service,declared,ready,delta

Output CSV columns:
    timestamp,service,from_replicas,to_replicas,
    pre_window_s,max_delta_in_window,had_phantom

Usage:
    python detect_double_scaling.py \\
        path/to/instances.csv path/to/phantom_capacity.csv \\
        --window 60 --out path/to/double_scaling.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final

import pandas as pd

DEFAULT_WINDOW_S: Final[int] = 60


def load_instances(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def load_phantom(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def extract_scale_ups(instances: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame with one row per scale-up event."""
    instances = instances.set_index("timestamp")
    service_cols = [c for c in instances.columns if c.endswith("&count")]
    rows = []
    for col in service_cols:
        service = col.removesuffix("&count")
        diffs = instances[col].diff().fillna(0)
        for ts, delta in diffs.items():
            if delta > 0:
                rows.append(
                    {
                        "timestamp": ts,
                        "service": service,
                        "from_replicas": int(instances[col].loc[ts] - delta),
                        "to_replicas": int(instances[col].loc[ts]),
                    }
                )
    if not rows:
        return pd.DataFrame(
            columns=["timestamp", "service", "from_replicas", "to_replicas"]
        )
    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


def annotate_double_scaling(
    scale_ups: pd.DataFrame, phantom: pd.DataFrame, window_s: int
) -> pd.DataFrame:
    """For each scale-up, look back `window_s` and check phantom delta > 0."""
    if scale_ups.empty:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "service",
                "from_replicas",
                "to_replicas",
                "pre_window_s",
                "max_delta_in_window",
                "had_phantom",
            ]
        )

    out_rows = []
    window = pd.Timedelta(seconds=window_s)
    by_service = {svc: g for svc, g in phantom.groupby("service")}

    for _, row in scale_ups.iterrows():
        svc_phantom = by_service.get(row["service"])
        if svc_phantom is None:
            max_delta = 0
        else:
            mask = (svc_phantom["timestamp"] >= row["timestamp"] - window) & (
                svc_phantom["timestamp"] < row["timestamp"]
            )
            window_slice = svc_phantom.loc[mask, "delta"]
            max_delta = int(window_slice.max()) if not window_slice.empty else 0

        out_rows.append(
            {
                "timestamp": row["timestamp"],
                "service": row["service"],
                "from_replicas": row["from_replicas"],
                "to_replicas": row["to_replicas"],
                "pre_window_s": window_s,
                "max_delta_in_window": max_delta,
                "had_phantom": max_delta > 0,
            }
        )

    return pd.DataFrame(out_rows)


def summarize(events: pd.DataFrame) -> str:
    if events.empty:
        return "No scale-up events found."
    total = len(events)
    double = int(events["had_phantom"].sum())
    lines = [
        f"Scale-up events: {total}",
        f"Double-scaling events: {double} ({double / total:.1%})",
    ]
    if double > 0:
        per_svc = (
            events[events["had_phantom"]].groupby("service").size().sort_values(ascending=False)
        )
        lines.append("By service:")
        for svc, n in per_svc.items():
            lines.append(f"  {svc}: {n}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("instances_csv", type=Path)
    ap.add_argument("phantom_csv", type=Path)
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW_S,
                    help=f"look-back window in seconds (default {DEFAULT_WINDOW_S})")
    ap.add_argument("--out", type=Path, required=True, help="output CSV path")
    args = ap.parse_args()

    for path in (args.instances_csv, args.phantom_csv):
        if not path.exists():
            print(f"file not found: {path}", file=sys.stderr)
            return 1

    instances = load_instances(args.instances_csv)
    phantom = load_phantom(args.phantom_csv)

    scale_ups = extract_scale_ups(instances)
    annotated = annotate_double_scaling(scale_ups, phantom, args.window)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    annotated.to_csv(args.out, index=False)

    print(f"=== {args.instances_csv} (vs {args.phantom_csv}) ===")
    print(summarize(annotated))
    print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
