"""Consolidate per-run failure_modes_summary.csv files into one aggregate.

Walks `<root>/<benchmark>/<workload>/run<N>/failure_modes_summary.csv`
(matches the layout used by Fase G of PLAN_CLAUDE_CODE.md), concatenates
them, and tags each service with a `runtime` column (java vs no-java)
critical for the Cap_2 Java-vs-non-Java comparison in REPORT.md.

Output columns:
    benchmark, workload, rep, seed, service, runtime,
    phantom_integral, phantom_max, phantom_duration_s,
    double_scaling_count, ping_pong_count

Usage:
    python aggregate_results.py path/to/results/sprint-1/ \\
        --out path/to/results/sprint-1/aggregate.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final

import pandas as pd


# Heuristic runtime classifier. Train Ticket services all start with 'ts-'
# and are Java/Spring Boot, *except* a handful written in Python or Node.
# Online Boutique services are mostly Go/Node/Python with one Java service
# (adservice). Update this map when introducing new benchmarks.
JAVA_SERVICES: Final[frozenset[str]] = frozenset({"adservice"})

# Train Ticket services that are NOT Java (rare exceptions in the demo)
TRAIN_TICKET_NON_JAVA: Final[frozenset[str]] = frozenset({
    "ts-avatar-service",   # Python
    "ts-voucher-service",  # Python
})


def classify_runtime(service: str) -> str:
    if service in JAVA_SERVICES:
        return "java"
    if service.startswith("ts-"):
        return "no-java" if service in TRAIN_TICKET_NON_JAVA else "java"
    return "no-java"


def find_summary_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/*/run*/failure_modes_summary.csv"))


def infer_controller_from_path(p: Path) -> str:
    """Infer controller (vanilla/khpa/keff) from the path.

    Convention:
        results/sprint-1/online-boutique/...        -> vanilla
        results/sprint-1/online-boutique-khpa/...   -> khpa
        results/sprint-1/online-boutique-keff/...   -> keff (Sprint 2+)
        idem train-ticket
    """
    parts = p.parts
    for part in parts:
        if part.endswith("-khpa"):
            return "khpa"
        if part.endswith("-keff"):
            return "keff"
    return "vanilla"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="root of results/sprint-N tree")
    ap.add_argument("--out", type=Path, required=True, help="output aggregate CSV")
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"not a directory: {args.root}", file=sys.stderr)
        return 1

    paths = find_summary_files(args.root)
    if not paths:
        print(f"no failure_modes_summary.csv found under {args.root}", file=sys.stderr)
        return 1

    frames = []
    for p in paths:
        try:
            df = pd.read_csv(p)
        except pd.errors.EmptyDataError:
            print(f"  skipping empty file: {p}", file=sys.stderr)
            continue
        # Always set controller from path (overrides metadata's controller column
        # if present, so e.g. archived dirs are correctly classified)
        df["controller"] = infer_controller_from_path(p)
        # Normalize benchmark name: strip the "-khpa" / "-keff" suffix so
        # benchmark column stays as "online-boutique" / "train-ticket"
        if "benchmark" in df.columns:
            df["benchmark"] = df["benchmark"].astype(str).str.replace(
                r"-(khpa|keff)$", "", regex=True
            )
        frames.append(df)

    if not frames:
        print("all summary files were empty", file=sys.stderr)
        return 1

    aggregate = pd.concat(frames, ignore_index=True)
    aggregate["runtime"] = aggregate["service"].apply(classify_runtime)

    column_order = [
        "benchmark", "controller", "workload", "rep", "seed", "service", "runtime",
        # Failure-mode metrics (Sprint 1)
        "phantom_integral", "phantom_integral_total", "phantom_integral_real",
        "phantom_max", "phantom_duration_s",
        "double_scaling_count", "ping_pong_count",
        # Sprint 1.5 added metrics (replica/cost/SLO)
        "avg_replica_count", "max_replica_count", "total_replica_seconds",
        "scale_operation_count", "replica_count_std",
        "p90_latency_mean", "slo_violation_rate",
        # 2026-05-21 added (literature alignment — Opción A)
        "p95_latency_mean", "p99_latency_mean", "cpu_usage_integral_seconds",
        # 2026-05-21 added (convergence time per Cap_3:246)
        "convergence_time_s", "convergence_events",
    ]
    existing = [c for c in column_order if c in aggregate.columns]
    aggregate = aggregate[existing]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    aggregate.to_csv(args.out, index=False)

    print(f"=== aggregate over {len(paths)} runs ===")
    print(f"  rows: {len(aggregate)}")
    if "controller" in aggregate.columns:
        print(f"  controllers: {dict(aggregate['controller'].value_counts())}")
    print(f"  unique (benchmark, controller, workload, rep): "
          f"{aggregate.groupby(['benchmark', 'controller', 'workload', 'rep'], dropna=False).ngroups}")
    print(f"  runtime distribution:")
    print(aggregate["runtime"].value_counts().to_string())
    print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
