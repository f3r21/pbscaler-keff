"""Build the descriptive comparison table for Cap_3 / Cap_4.

Reads `aggregate_baselines.csv` and produces Markdown tables with descriptive
statistics over N=3 reps por (benchmark, workload, metric, controller). El
controller de baseline (por defecto `vanilla`) aparece como columna de
referencia y cada controller no-baseline se compara contra él en una fila
separada: mediana, rango (mín, máx), ratio_medianas y signo_consistente_3de3.

`signo_consistente_3de3` reporta robustez muestral sin maquinaria
inferencial (per la decisión metodológica del usuario 2026-05-08, con N=3
el análisis se mantiene descriptivo):

    TRUE   si min(baseline_per_rep) > max(other_per_rep)   (baseline > other en los 3 reps)
    TRUE   si max(baseline_per_rep) < min(other_per_rep)   (baseline < other en los 3 reps)
    FALSE  otherwise (los rangos per-rep se solapan)
    "—"    si toda observación en ambos es exactamente 0 (caso TT trivial)

Las reglas de agregación per métrica entre servicios siguen
`SERVICE_AGGREGATION` en `build_comparison_table.py`. La misma lista
MASTER_METRICS aplica.

Usage:
    python build_comparison_descriptive.py \\
        --aggregate code/results/sprint-1/aggregate_baselines.csv \\
        --out-master code/results/sprint-1/comparison_table_descriptive.md \\
        --out-auxiliary code/results/sprint-1/comparison_table_descriptive_auxiliary.md
        [--baseline vanilla]
        [--controller-order vanilla keff NaiveTemporalGate khpa]
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


def consistent_sign_pairwise(baseline_reps: list[float], other_reps: list[float]) -> str:
    """Aplica la regla 3-de-3 entre baseline y otro controller."""
    if all(v == 0 for v in baseline_reps + other_reps):
        return "—"
    if min(baseline_reps) > max(other_reps):
        return "TRUE"
    if max(baseline_reps) < min(other_reps):
        return "TRUE"
    return "FALSE"


def order_controllers(
    present: list[str], baseline: str, controller_order: list[str] | None
) -> list[str]:
    """Devuelve los controllers no-baseline en el orden de presentación.

    Si `controller_order` se pasa, respeta ese orden (filtrando los que están
    presentes y no son baseline). Si no, ordena alfabéticamente.
    """
    if controller_order:
        return [c for c in controller_order if c != baseline and c in present]
    return sorted(c for c in present if c != baseline)


def build_rows(
    per_run: pd.DataFrame,
    metrics: list[str],
    baseline: str = "vanilla",
    controller_order: list[str] | None = None,
) -> list[dict]:
    """Walk (benchmark, workload, metric, controller_no_baseline) → filas."""
    rows: list[dict] = []
    controllers_present = sorted(per_run["controller"].unique().tolist())
    if baseline not in controllers_present:
        return rows  # sin baseline no se puede comparar
    non_baseline = order_controllers(controllers_present, baseline, controller_order)

    for benchmark in BENCHMARK_ORDER:
        sub_b = per_run[per_run["benchmark"] == benchmark]
        if sub_b.empty:
            continue
        for workload in WORKLOAD_ORDER:
            sub_w = sub_b[sub_b["workload"] == workload]
            if sub_w.empty:
                continue
            base_runs = sub_w[sub_w["controller"] == baseline]
            if base_runs.empty:
                continue
            for metric in metrics:
                if metric not in sub_w.columns:
                    continue
                base_reps = sorted(base_runs[metric].fillna(0).tolist())
                if not base_reps:
                    continue
                base_med = float(pd.Series(base_reps).median())
                base_lo, base_hi = min(base_reps), max(base_reps)
                for other in non_baseline:
                    other_runs = sub_w[sub_w["controller"] == other]
                    if other_runs.empty:
                        continue
                    other_reps = sorted(other_runs[metric].fillna(0).tolist())
                    if not other_reps:
                        continue
                    other_med = float(pd.Series(other_reps).median())
                    other_lo, other_hi = min(other_reps), max(other_reps)
                    rows.append({
                        "benchmark": benchmark,
                        "workload": workload,
                        "metric": metric,
                        "baseline": baseline,
                        "base_med": base_med,
                        "base_lo": base_lo,
                        "base_hi": base_hi,
                        "controller": other,
                        "other_med": other_med,
                        "other_lo": other_lo,
                        "other_hi": other_hi,
                        "ratio": fmt_ratio(other_med, base_med),
                        "signo": consistent_sign_pairwise(base_reps, other_reps),
                    })
    return rows


def to_markdown(rows: list[dict], title: str, baseline: str) -> str:
    out: list[str] = [f"# {title}", ""]
    if not rows:
        out.append("(no rows)")
        return "\n".join(out) + "\n"
    out.extend([
        f"Filas: {len(rows)} = (benchmark × workload × metric × controller_no_baseline).",
        f"Baseline = `{baseline}`. Cada fila compara un controller no-baseline contra el baseline sobre N=3 reps.",
        "Agregación previa entre servicios usa sum/mean/max según métrica "
        "(ver `build_comparison_table.SERVICE_AGGREGATION`).",
        "",
        "**Convenciones:**",
        f"- `mediana_{baseline}` y `rango_{baseline}` son los del baseline; se replican por cada fila del mismo (benchmark, workload, metric).",
        f"- `ratio_medianas` = `mediana_<controller> / mediana_{baseline}`. >1 → controller mayor que baseline; <1 → controller menor; `—` si denominador es 0.",
        "- `signo_consistente_3de3` = TRUE si los 3 reps de un controller están todos por encima (o todos por debajo) de los 3 reps del baseline; FALSE si los rangos se solapan; `—` si todas las observaciones son 0 (caso trivial).",
        "",
        f"| Benchmark | Workload | Metric | mediana_{baseline} | rango_{baseline} | Controller | mediana | rango | ratio_medianas | signo_consistente_3de3 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ])
    for r in rows:
        m = r["metric"]
        out.append(
            "| {bench} | {wl} | {metric} | {bmed} | {brng} | {ctrl} | {omed} | {orng} | {ratio} | {signo} |".format(
                bench=r["benchmark"],
                wl=r["workload"],
                metric=m,
                bmed=fmt_value(m, r["base_med"]),
                brng=fmt_range(m, r["base_lo"], r["base_hi"]),
                ctrl=r["controller"],
                omed=fmt_value(m, r["other_med"]),
                orng=fmt_range(m, r["other_lo"], r["other_hi"]),
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
                    help="aggregate CSV con columna controller")
    ap.add_argument("--out-master", type=Path, required=True,
                    help="output Markdown — tabla principal descriptiva (3 métricas alineadas con literatura)")
    ap.add_argument("--out-auxiliary", type=Path, required=True,
                    help="output Markdown — tabla auxiliar (mecanismo)")
    ap.add_argument("--baseline", default="vanilla",
                    help="controller de referencia (default: vanilla)")
    ap.add_argument("--controller-order", nargs="+", default=None,
                    help="orden de controllers no-baseline en la tabla. Default: alfabético")
    args = ap.parse_args()

    if not args.aggregate.exists():
        print(f"aggregate not found: {args.aggregate}", file=sys.stderr)
        return 1

    df = pd.read_csv(args.aggregate)
    if "controller" not in df.columns:
        print("aggregate.csv missing 'controller' column", file=sys.stderr)
        return 1

    per_run = aggregate_per_run(df)

    master_rows = build_rows(per_run, MASTER_METRICS, args.baseline, args.controller_order)
    args.out_master.parent.mkdir(parents=True, exist_ok=True)
    args.out_master.write_text(to_markdown(
        master_rows,
        title="Tabla descriptiva principal Cap_4 — 3 métricas alineadas con literatura (Opción A, 2026-05-21)",
        baseline=args.baseline,
    ))

    auxiliary_rows = build_rows(per_run, AUXILIARY_METRICS, args.baseline, args.controller_order)
    args.out_auxiliary.parent.mkdir(parents=True, exist_ok=True)
    args.out_auxiliary.write_text(to_markdown(
        auxiliary_rows,
        title="Tabla descriptiva auxiliar — métricas de mecanismo (ping-pong, phantom, double scaling)",
        baseline=args.baseline,
    ))

    print("=== descriptive tables ===")
    _report(master_rows, "master", args.out_master)
    _report(auxiliary_rows, "auxiliary", args.out_auxiliary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
