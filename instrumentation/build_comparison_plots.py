"""Figuras de comparación para Cap_4 — dot-plots con puntos crudos + mediana.

Metodología (N=3, sin inferencia): se grafican los **3 puntos crudos** por celda
+ un marcador de **mediana**, NO boxplots ni error bars (con N pequeño la caja no
es informativa; regla de choosing-research-figures / Wohlin §8.1.2).

Las métricas de latencia/SLO son **checkout-scoped** (vía `aggregate_per_run`,
Cap_3:238), consistentes con las tablas. Solo Online Boutique (único benchmark
de este trabajo). Colores workload Okabe-Ito (colorblind-safe).

Uso:
    python build_comparison_plots.py \\
        --aggregate results/sprint-A/aggregate.csv \\
        --out-dir results/sprint-A/figs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from build_comparison_table import aggregate_per_run

CONTROLLER_ORDER = ["vanilla", "keff", "naive", "khpa"]
CONTROLLER_LABEL = {"vanilla": "PBScaler", "keff": "PBScaler-$k_{eff}$",
                    "naive": "NaiveGate", "khpa": "HPA"}
WORKLOAD_ORDER = ["step", "bursty"]
WORKLOAD_COLOR = {"step": "#0072B2", "bursty": "#E69F00"}  # Okabe-Ito
WORKLOAD_OFFSET = {"step": -0.16, "bursty": 0.16}

# (columna, etiqueta y, línea de referencia SLO o None)
MAIN_METRICS = [
    ("slo_violation_rate", "Tasa de violación de SLO\n(checkout, P90 > 500 ms)", None),
    ("p95_latency_mean", "p95 de checkout (ms)", 500.0),
    ("cpu_usage_integral_seconds", "Costo de CPU (CPU·s)", None),
]
MECH_METRICS = [
    ("double_scaling_count", "Double-scaling (conteo)", None),
    ("phantom_integral", "Phantom integral (pod·s)", None),
]


def _present_controllers(df: pd.DataFrame) -> list[str]:
    present = set(df["controller"].unique())
    return [c for c in CONTROLLER_ORDER if c in present]


def _plot_panel(ax, per_run: pd.DataFrame, metric: str, ylabel: str, slo: float | None) -> None:
    controllers = _present_controllers(per_run)
    for xi, ctrl in enumerate(controllers):
        for wl in WORKLOAD_ORDER:
            vals = per_run[(per_run.controller == ctrl) & (per_run.workload == wl)][metric]
            vals = [v for v in vals.tolist() if pd.notna(v)]
            if not vals:
                continue
            xbase = xi + WORKLOAD_OFFSET[wl]
            # 3 puntos crudos con pequeño jitter horizontal determinista
            jit = [-0.05, 0.0, 0.05][: len(vals)]
            ax.scatter([xbase + j for j in jit], vals, s=42,
                       color=WORKLOAD_COLOR[wl], alpha=0.85, edgecolor="black",
                       linewidth=0.4, zorder=3,
                       label=wl if xi == 0 else None)
            # marcador de mediana (barra horizontal)
            med = float(pd.Series(vals).median())
            ax.hlines(med, xbase - 0.11, xbase + 0.11, color=WORKLOAD_COLOR[wl],
                      linewidth=2.2, zorder=4)
    if slo is not None:
        ax.axhline(slo, color="#999999", linestyle="--", linewidth=1, zorder=1)
        ax.text(len(controllers) - 0.5, slo, " SLO", va="bottom", ha="right",
                fontsize=8, color="#666666")
    ax.set_xticks(range(len(controllers)))
    ax.set_xticklabels([CONTROLLER_LABEL.get(c, c) for c in controllers],
                       rotation=15, fontsize=8)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_ylim(bottom=0)


def _figure(per_run: pd.DataFrame, metrics: list, out: Path, suptitle: str) -> None:
    n = len(metrics)
    fig, axes = plt.subplots(1, n, figsize=(4.2 * n, 4.2))
    if n == 1:
        axes = [axes]
    for ax, (metric, ylabel, slo) in zip(axes, metrics):
        _plot_panel(ax, per_run, metric, ylabel, slo)
    # leyenda única (workloads) arriba
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, title="Workload", loc="upper right",
                   ncol=len(labels), fontsize=8)
    fig.suptitle(suptitle, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aggregate", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    if not args.aggregate.exists():
        print(f"aggregate not found: {args.aggregate}", file=sys.stderr)
        return 1

    df = pd.read_csv(args.aggregate)
    per_run = aggregate_per_run(df)
    per_run = per_run[per_run.benchmark == "online-boutique"]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    p_main = args.out_dir / "comparison_main_metrics.png"
    p_mech = args.out_dir / "comparison_mechanism.png"
    _figure(per_run, MAIN_METRICS, p_main,
            "Métricas principales por controlador × workload (N=3: puntos crudos + mediana)")
    _figure(per_run, MECH_METRICS, p_mech,
            "Métricas de mecanismo por controlador × workload (N=3)")

    print("=== plots ===")
    print(f"  {p_main}")
    print(f"  {p_mech}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
