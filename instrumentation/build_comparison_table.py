"""Build the master comparison table for Cap_3 / Cap_4.

Reads `aggregate_baselines.csv` (output of aggregate_results.py with the
controller column) and produces two markdown tables:

1. **comparison_table.md** — master, 5 metrics:
   phantom_integral, double_scaling_count, ping_pong_count,
   avg_replicas, slo_violation_rate
   = 1 row per (controller, benchmark, workload), averaged across 3 reps and
   summed/averaged across services as appropriate.

2. **comparison_table_detailed.md** — appendix, 7 additional metrics:
   phantom_max, phantom_duration_s, max_replicas, total_replica_seconds,
   scale_operation_count, replica_count_std, p90_latency_mean.

Aggregation rules per metric:
- phantom_integral, *_count, total_replica_seconds, scale_operation_count:
  SUM across services (cluster-wide totals), then MEAN across reps.
- avg_replica_count, max_replica_count, replica_count_std,
  p90_latency_mean, slo_violation_rate: MEAN across services (per-service
  average), then MEAN across reps.
- phantom_max, phantom_duration_s: MAX across services, then MEAN across reps.

Usage:
    python build_comparison_table.py \\
        --aggregate code/results/sprint-1/aggregate_baselines.csv \\
        --out-master code/results/sprint-1/comparison_table.md \\
        --out-detailed code/results/sprint-1/comparison_table_detailed.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


# How to aggregate each metric across services (sum vs mean vs max)
SERVICE_AGGREGATION = {
    "phantom_integral": "sum",
    "phantom_integral_total": "sum",
    "phantom_integral_real": "sum",
    "phantom_max": "max",
    "phantom_duration_s": "max",
    "double_scaling_count": "sum",
    "ping_pong_count": "sum",
    "avg_replica_count": "mean",
    "max_replica_count": "max",
    "total_replica_seconds": "sum",
    "scale_operation_count": "sum",
    "replica_count_std": "mean",
    "p90_latency_mean": "mean",
    "slo_violation_rate": "mean",
    # 2026-05-21 added (literature alignment — Opción A)
    "p95_latency_mean": "mean",
    "p99_latency_mean": "mean",
    "cpu_usage_integral_seconds": "sum",  # cluster-wide CPU cost
    # 2026-05-21 added (Cap_3:246 — convergence time)
    "convergence_time_s": "mean",   # per-service mean of medians across services
    "convergence_events": "sum",    # total convergence events across services
}

# 2026-05-21 — Refactor per Opción A (literature-aligned metrics).
# Tabla principal de Cap_4 reporta exactamente 3 métricas, alineadas con
# Cushion §7.1.6 (P95 + viol rate + CPU), GRAF (CPU saved, P90/P99),
# Morphis (P95 + compliance + QPS/CPU), PBScaler Tabla 2 (viol rate + Cost $).
# Ver ~/.claude/.../memory/metrics_decision.md
MASTER_METRICS = [
    "slo_violation_rate",
    "p95_latency_mean",
    "cpu_usage_integral_seconds",
]

# Métricas de mecanismo (sección dedicada de Cap_4, no tabla principal).
# Evidencia de los 3 failure modes que k_eff ataca + métrica novel.
AUXILIARY_METRICS = [
    "ping_pong_count",
    "phantom_integral",
    "double_scaling_count",
    "convergence_time_s",  # Cap_3:246 — implementado 2026-05-21
]

# Apéndice de diagnóstico — completitud + reproducibilidad (Zobel Ch 14).
DETAIL_METRICS = [
    "p90_latency_mean",
    "p99_latency_mean",
    "avg_replica_count",
    "total_replica_seconds",
    "phantom_integral_total",
    "phantom_max",
    "phantom_duration_s",
    "max_replica_count",
    "scale_operation_count",
    "replica_count_std",
]


def aggregate_per_run(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse service rows into one row per (controller, benchmark, workload, rep)."""
    grouped = df.groupby(
        ["controller", "benchmark", "workload", "rep"], dropna=False, as_index=False
    ).agg({m: SERVICE_AGGREGATION[m] for m in SERVICE_AGGREGATION if m in df.columns})
    return grouped


def aggregate_across_reps(df: pd.DataFrame) -> pd.DataFrame:
    """Average across the 3 reps, leaving (controller, benchmark, workload)."""
    metric_cols = [c for c in df.columns if c not in ("controller", "benchmark", "workload", "rep")]
    grouped = df.groupby(
        ["controller", "benchmark", "workload"], dropna=False, as_index=False
    ).agg({c: "mean" for c in metric_cols})
    return grouped


def order_for_master_table(df: pd.DataFrame) -> pd.DataFrame:
    """Sort rows: by (benchmark, workload), within those alternating controllers."""
    workload_order = ["step", "bursty", "diurnal", "steady_ramp", "trace_driven"]
    benchmark_order = ["online-boutique", "train-ticket"]
    controller_order = ["vanilla", "khpa", "keff"]

    df = df.copy()
    df["_b"] = df["benchmark"].apply(lambda b: benchmark_order.index(b) if b in benchmark_order else 99)
    df["_w"] = df["workload"].apply(lambda w: workload_order.index(w) if w in workload_order else 99)
    df["_c"] = df["controller"].apply(lambda c: controller_order.index(c) if c in controller_order else 99)
    df = df.sort_values(["_b", "_w", "_c"]).drop(columns=["_b", "_w", "_c"])
    return df.reset_index(drop=True)


def fmt_value(metric: str, value: float) -> str:
    if pd.isna(value):
        return "—"
    if metric in ("double_scaling_count", "ping_pong_count", "max_replica_count",
                  "scale_operation_count", "phantom_max"):
        return f"{value:.0f}"
    if metric in ("slo_violation_rate",):
        return f"{value:.3f}"
    if metric in ("phantom_integral", "phantom_integral_total", "phantom_duration_s",
                  "total_replica_seconds", "cpu_usage_integral_seconds"):
        return f"{value:.0f}"
    if metric in ("convergence_time_s",):
        return f"{value:.0f}"
    if metric in ("convergence_events",):
        return f"{value:.0f}"
    return f"{value:.2f}"


def to_markdown(df: pd.DataFrame, metrics: list[str], title: str) -> str:
    """Render a markdown table from the dataframe with the given metric columns."""
    lines = [
        f"# {title}",
        "",
        f"Aggregation: {len(df)} rows = (controller × benchmark × workload).",
        "Per-row values are mean across 3 reps; per-rep value is sum/mean/max across services depending on metric (see compute_failure_mode_summary.py).",
        "",
    ]

    headers = ["Controller", "Benchmark", "Workload"] + metrics
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")

    for _, row in df.iterrows():
        cells = [str(row["controller"]), str(row["benchmark"]), str(row["workload"])]
        for m in metrics:
            cells.append(fmt_value(m, row.get(m, float("nan"))))
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aggregate", type=Path, required=True,
                    help="aggregate CSV with controller column")
    ap.add_argument("--out-master", type=Path, required=True,
                    help="output markdown — master table (3 metrics, lit-aligned)")
    ap.add_argument("--out-auxiliary", type=Path, required=True,
                    help="output markdown — auxiliary table (mechanism metrics)")
    ap.add_argument("--out-detailed", type=Path, required=True,
                    help="output markdown — detailed table (apéndice)")
    args = ap.parse_args()

    if not args.aggregate.exists():
        print(f"aggregate not found: {args.aggregate}", file=sys.stderr)
        return 1

    df = pd.read_csv(args.aggregate)
    if "controller" not in df.columns:
        print("aggregate.csv missing 'controller' column", file=sys.stderr)
        return 1

    per_run = aggregate_per_run(df)
    per_workload = aggregate_across_reps(per_run)
    per_workload = order_for_master_table(per_workload)

    args.out_master.parent.mkdir(parents=True, exist_ok=True)
    args.out_master.write_text(to_markdown(
        per_workload,
        MASTER_METRICS,
        title="Tabla principal Cap_4 — 3 métricas alineadas con literatura (Opción A, 2026-05-21)",
    ))
    args.out_auxiliary.parent.mkdir(parents=True, exist_ok=True)
    args.out_auxiliary.write_text(to_markdown(
        per_workload,
        AUXILIARY_METRICS,
        title="Tabla auxiliar — métricas de mecanismo (ping-pong, phantom, double scaling)",
    ))
    args.out_detailed.parent.mkdir(parents=True, exist_ok=True)
    args.out_detailed.write_text(to_markdown(
        per_workload,
        DETAIL_METRICS,
        title="Tabla detallada — apéndice de diagnóstico (10 métricas)",
    ))

    print(f"=== comparison tables ===")
    print(f"  rows: {len(per_workload)}")
    print(f"  controllers: {sorted(per_workload['controller'].unique())}")
    print(f"  benchmarks: {sorted(per_workload['benchmark'].unique())}")
    print(f"  workloads: {sorted(per_workload['workload'].unique())}")
    print(f"  master    -> {args.out_master}")
    print(f"  auxiliary -> {args.out_auxiliary}")
    print(f"  detailed  -> {args.out_detailed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
