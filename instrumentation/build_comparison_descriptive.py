"""Build the descriptive comparison table for Cap_3 / Cap_4.

Reads `aggregate_baselines.csv` and produces a single Markdown table with
descriptive statistics over N=3 reps per (benchmark, workload, metric):

    mediana_vanilla, rango_vanilla, mediana_khpa, rango_khpa,
    ratio_medianas (= mediana_khpa / mediana_vanilla),
    signo_consistente_3_de_3

`signo_consistente_3_de_3` reports sample-level robustness without inferential
machinery (per the user's methodology decision on 2026-05-08, with N=3 the
analysis stays descriptive only):

    TRUE   if min(vanilla_per_rep) > max(khpa_per_rep)   (vanilla > khpa in all 3 reps)
    TRUE   if max(vanilla_per_rep) < min(khpa_per_rep)   (vanilla < khpa in all 3 reps)
    FALSE  otherwise (per-rep ranges overlap)
    "—"    if every rep on both controllers is exactly 0 (TT trivial case)

Aggregation rules per metric across services follow `SERVICE_AGGREGATION` in
`build_comparison_table.py`. The same MASTER_METRICS list applies.

Usage:
    python build_comparison_descriptive.py \\
        --aggregate code/results/sprint-1/aggregate_baselines.csv \\
        --out       code/results/sprint-1/comparison_table_descriptive.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from build_comparison_table import (
    MASTER_METRICS,
    AUXILIARY_METRICS,
    SERVICE_AGGREGATION,
    aggregate_per_run,
    fmt_value,
)

WORKLOAD_ORDER = ["step", "bursty", "diurnal", "steady_ramp", "trace_driven"]
BENCHMARK_ORDER = ["online-boutique", "train-ticket"]


def fmt_range(metric: str, lo: float, hi: float) -> str:
    return f"[{fmt_value(metric, lo)}, {fmt_value(metric, hi)}]"


def fmt_ratio(num: float, den: float) -> str:
    if den == 0:
        return "—"
    return f"{num / den:.2f}"


def consistent_sign(vanilla_reps: list[float], khpa_reps: list[float]) -> str:
    """Return TRUE/FALSE/'—' per the consistency rule documented in the docstring."""
    if all(v == 0 for v in vanilla_reps + khpa_reps):
        return "—"
    if min(vanilla_reps) > max(khpa_reps):
        return "TRUE"
    if max(vanilla_reps) < min(khpa_reps):
        return "TRUE"
    return "FALSE"


def build_rows(per_run: pd.DataFrame, metrics: list[str]) -> list[dict]:
    """Walk (benchmark, workload, metric) and produce descriptive stats rows."""
    rows: list[dict] = []
    for benchmark in BENCHMARK_ORDER:
        sub_b = per_run[per_run["benchmark"] == benchmark]
        if sub_b.empty:
            continue
        for workload in WORKLOAD_ORDER:
            sub_w = sub_b[sub_b["workload"] == workload]
            if sub_w.empty:
                continue
            vanilla = sub_w[sub_w["controller"] == "vanilla"]
            khpa = sub_w[sub_w["controller"] == "khpa"]
            for metric in metrics:
                if metric not in sub_w.columns:
                    continue
                v_reps = sorted(vanilla[metric].fillna(0).tolist())
                k_reps = sorted(khpa[metric].fillna(0).tolist())
                if not v_reps or not k_reps:
                    continue
                v_med = float(pd.Series(v_reps).median())
                k_med = float(pd.Series(k_reps).median())
                rows.append({
                    "benchmark": benchmark,
                    "workload": workload,
                    "metric": metric,
                    "v_med": v_med,
                    "v_lo": min(v_reps),
                    "v_hi": max(v_reps),
                    "k_med": k_med,
                    "k_lo": min(k_reps),
                    "k_hi": max(k_reps),
                    "ratio": fmt_ratio(k_med, v_med),
                    "signo": consistent_sign(v_reps, k_reps),
                })
    return rows


def to_markdown(rows: list[dict], title: str) -> str:
    out: list[str] = [
        f"# {title}",
        "",
        f"Aggregation: {len(rows)} filas = (benchmark × workload × metric).",
        "Por (benchmark, workload, metric): mediana y rango (mín, máx) sobre 3 reps; "
        "agregación previa entre servicios usando sum/mean/max según métrica "
        "(ver `build_comparison_table.SERVICE_AGGREGATION`).",
        "",
        "**Convenciones:**",
        "- `ratio_medianas` = `mediana_khpa / mediana_vanilla`. >1 → khpa mayor; <1 → vanilla mayor; `—` si denominador es 0.",
        "- `signo_consistente_3de3` = TRUE si los 3 reps de un controller están todos por encima (o todos por debajo) de los 3 reps del otro; FALSE si los rangos se solapan; `—` si todas las observaciones son 0 (caso TT trivial).",
        "",
        "| Benchmark | Workload | Metric | mediana_vanilla | rango_vanilla | mediana_khpa | rango_khpa | ratio_medianas | signo_consistente_3de3 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        m = r["metric"]
        out.append(
            "| {bench} | {wl} | {metric} | {vmed} | {vrng} | {kmed} | {krng} | {ratio} | {signo} |".format(
                bench=r["benchmark"],
                wl=r["workload"],
                metric=m,
                vmed=fmt_value(m, r["v_med"]),
                vrng=fmt_range(m, r["v_lo"], r["v_hi"]),
                kmed=fmt_value(m, r["k_med"]),
                krng=fmt_range(m, r["k_lo"], r["k_hi"]),
                ratio=r["ratio"],
                signo=r["signo"],
            )
        )
    return "\n".join(out) + "\n"


def _report(rows: list[dict], label: str, out_path: Path) -> None:
    n_true = sum(1 for r in rows if r["signo"] == "TRUE")
    n_false = sum(1 for r in rows if r["signo"] == "FALSE")
    n_dash = sum(1 for r in rows if r["signo"] == "—")
    print(f"  [{label}] rows={len(rows)} signo_consistente: TRUE={n_true}, FALSE={n_false}, '—'={n_dash} -> {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aggregate", type=Path, required=True,
                    help="aggregate CSV with controller column")
    ap.add_argument("--out-master", type=Path, required=True,
                    help="output Markdown — main descriptive table (3 métricas lit-aligned)")
    ap.add_argument("--out-auxiliary", type=Path, required=True,
                    help="output Markdown — auxiliary descriptive table (mechanism)")
    args = ap.parse_args()

    if not args.aggregate.exists():
        print(f"aggregate not found: {args.aggregate}", file=sys.stderr)
        return 1

    df = pd.read_csv(args.aggregate)
    if "controller" not in df.columns:
        print("aggregate.csv missing 'controller' column", file=sys.stderr)
        return 1

    per_run = aggregate_per_run(df)

    master_rows = build_rows(per_run, MASTER_METRICS)
    args.out_master.parent.mkdir(parents=True, exist_ok=True)
    args.out_master.write_text(to_markdown(
        master_rows,
        title="Tabla descriptiva principal Cap_4 — 3 métricas alineadas con literatura (Opción A, 2026-05-21)",
    ))

    auxiliary_rows = build_rows(per_run, AUXILIARY_METRICS)
    args.out_auxiliary.parent.mkdir(parents=True, exist_ok=True)
    args.out_auxiliary.write_text(to_markdown(
        auxiliary_rows,
        title="Tabla descriptiva auxiliar — métricas de mecanismo (ping-pong, phantom, double scaling)",
    ))

    print("=== descriptive tables ===")
    _report(master_rows, "master", args.out_master)
    _report(auxiliary_rows, "auxiliary", args.out_auxiliary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
