"""Consolidate per-run failure_modes_summary.csv files into one aggregate.

Finds every `failure_modes_summary.csv` under `<root>` recursively (any
depth), so it works with BOTH layouts in use:
    layout antiguo:  <root>/<benchmark>[-khpa]/<workload>/run<N>/...
    layout canónico: <root>/<benchmark>/<workload>/<controller>/seed<N>/...

The controller is read from the CSV's own `controller` column (populated
from each run's metadata.json by compute_failure_mode_summary.py), with a
normalization map so runtime names (PBScaler, PBScaler-keff, KHPA,
NaiveTemporalGate) and legacy labels (vanilla, khpa, keff) collapse to a
single vocabulary: vanilla / keff / naive / khpa. Files whose controller
column is missing or unrecognized fall back to path-based inference. Paths
under any `_archive`/`_*` directory are skipped (old schema, contaminated
runs).

Output columns:
    benchmark, controller, workload, rep, seed, service, runtime,
    <failure-mode + replica + latency + cost metrics>

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


# Single controller vocabulary for analysis. Maps both legacy labels and
# runtime controller names (what run_one_ob.sh / PBSCALER_CONTROLLER use)
# onto the four labels the comparison tables expect.
CONTROLLER_NORMALIZE: Final[dict[str, str]] = {
    "vanilla": "vanilla", "pbscaler": "vanilla",
    "keff": "keff", "pbscaler-keff": "keff",
    "naive": "naive", "naivetemporalgate": "naive", "naive-temporal-gate": "naive",
    "khpa": "khpa",
}
VALID_LABELS: Final[frozenset[str]] = frozenset({"vanilla", "keff", "naive", "khpa"})


def normalize_controller(value: str) -> str | None:
    """Map a raw controller string to the canonical label, or None if unknown."""
    if value is None:
        return None
    return CONTROLLER_NORMALIZE.get(str(value).strip().lower())


def _is_excluded(p: Path, root: Path) -> bool:
    """Skip archived/contaminated trees: any path component starting with '_'."""
    try:
        rel_parts = p.relative_to(root).parts
    except ValueError:
        rel_parts = p.parts
    return any(part.startswith("_") for part in rel_parts)


def find_summary_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("failure_modes_summary.csv") if not _is_excluded(p, root)
    )


def infer_controller_from_path(p: Path) -> str:
    """Fallback controller inference from the path when the CSV column is unusable.

    Convention:
        results/sprint-1/online-boutique/...        -> vanilla
        results/sprint-1/online-boutique-khpa/...   -> khpa
        results/sprint-1/online-boutique-keff/...   -> keff
        .../<workload>/PBScaler-keff/seed<N>/...     -> keff (layout canónico)
    """
    parts = p.parts
    # Layout canónico: controller is its own path segment (a recognized name).
    for part in parts:
        norm = normalize_controller(part)
        if norm is not None and norm != "vanilla":
            return norm
    # Layout antiguo: controller encoded as a suffix on the benchmark dir name.
    for part in parts:
        if part.endswith("-khpa"):
            return "khpa"
        if part.endswith("-keff"):
            return "keff"
    return "vanilla"


def resolve_controller(df: pd.DataFrame, p: Path) -> str:
    """Prefer the CSV's own controller column (from metadata); else infer from path."""
    if "controller" in df.columns and not df["controller"].empty:
        raw = df["controller"].iloc[0]
        norm = normalize_controller(raw)
        if norm in VALID_LABELS:
            return norm
    return infer_controller_from_path(p)


def normalize_benchmark(df: pd.DataFrame) -> pd.DataFrame:
    """Strip the "-khpa"/"-keff" suffix from the benchmark column.

    Returns a new frame (does not mutate the input) so the benchmark column
    stays as "online-boutique" / "train-ticket" regardless of the controller
    suffix used in the old-layout directory names.
    """
    if "benchmark" not in df.columns:
        return df
    out = df.copy()
    out["benchmark"] = out["benchmark"].astype(str).str.replace(
        r"-(khpa|keff)$", "", regex=True
    )
    return out


# Canonical analysis column order, shared by aggregate_results and downstream
# consolidators (e.g. merge_aggregate.py). Columns absent from the data are
# dropped by the caller via [c for c in COLUMN_ORDER if c in df.columns].
COLUMN_ORDER: Final[list[str]] = [
    "benchmark", "controller", "workload", "rep", "seed", "service", "runtime",
    # Failure-mode metrics (old layout)
    "phantom_integral", "phantom_integral_total", "phantom_integral_real",
    "phantom_max", "phantom_duration_s",
    "double_scaling_count", "ping_pong_count",
    # Later-added metrics (replica/cost/SLO)
    "avg_replica_count", "max_replica_count", "total_replica_seconds",
    "scale_operation_count", "replica_count_std",
    "p90_latency_mean", "slo_violation_rate",
    # 2026-05-21 added (literature alignment — Opción A)
    "p95_latency_mean", "p99_latency_mean", "cpu_usage_integral_seconds",
    # 2026-05-21 added (convergence time per Cap_3:246)
    "convergence_time_s", "convergence_events",
]


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
        # Controller: trust the CSV's own column (populated from metadata.json),
        # normalized to the canonical vocabulary; fall back to path inference.
        df["controller"] = resolve_controller(df, p)
        # Normalize benchmark name: strip the "-khpa" / "-keff" suffix so
        # benchmark column stays as "online-boutique" / "train-ticket"
        df = normalize_benchmark(df)
        frames.append(df)

    if not frames:
        print("all summary files were empty", file=sys.stderr)
        return 1

    aggregate = pd.concat(frames, ignore_index=True)
    aggregate["runtime"] = aggregate["service"].apply(classify_runtime)

    existing = [c for c in COLUMN_ORDER if c in aggregate.columns]
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
