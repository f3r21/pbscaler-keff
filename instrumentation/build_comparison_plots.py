"""Build 3 comparison plots from aggregate_baselines.csv (Sprint 1.5)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

CONTROLLER_COLORS = {"vanilla": "#1f77b4", "khpa": "#ff7f0e", "keff": "#2ca02c"}
BENCHMARK_LABEL = {"online-boutique": "Online Boutique", "train-ticket": "Train Ticket"}
WORKLOAD_ORDER = ["step", "bursty", "diurnal", "steady_ramp", "trace_driven"]


def per_run_totals(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate service-level rows up to one row per (controller, benchmark, workload, rep)."""
    return (
        df.groupby(["controller", "benchmark", "workload", "rep"], as_index=False)
        .agg(
            phantom_integral=("phantom_integral", "sum"),
            double_scaling_count=("double_scaling_count", "sum"),
            ping_pong_count=("ping_pong_count", "sum"),
            avg_replicas=("avg_replica_count", "mean"),
            p90_latency=("p90_latency_mean", "mean"),
            total_replica_seconds=("total_replica_seconds", "sum"),
        )
    )


def plot_phantom_boxplot(df: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 5), sharey=False)
    for ax, bench in zip(axes, ["online-boutique", "train-ticket"]):
        sub = df[df.benchmark == bench]
        data = []
        labels = []
        colors = []
        for ctrl in ["vanilla", "khpa"]:
            for wl in WORKLOAD_ORDER:
                vals = sub[(sub.controller == ctrl) & (sub.workload == wl)]["phantom_integral"]
                if not vals.empty:
                    data.append(vals.values)
                    labels.append(f"{wl[:6]}\n{ctrl}")
                    colors.append(CONTROLLER_COLORS[ctrl])
        bp = ax.boxplot(data, tick_labels=labels, patch_artist=True, widths=0.6)
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)
        ax.set_title(BENCHMARK_LABEL[bench])
        ax.set_ylabel("phantom_integral (pod·s)")
        ax.tick_params(axis="x", rotation=45, labelsize=8)
        ax.grid(True, axis="y", alpha=0.3)
    fig.suptitle("Phantom capacity por controller × workload (3 reps)")
    fig.tight_layout()
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_replica_efficiency(df: pd.DataFrame, out: Path) -> None:
    """Scatter avg_replicas vs p90_latency. Lower-left = mejor (cheap + fast)."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    for ax, bench in zip(axes, ["online-boutique", "train-ticket"]):
        sub = df[df.benchmark == bench]
        for ctrl in ["vanilla", "khpa"]:
            d = sub[sub.controller == ctrl]
            ax.scatter(
                d["avg_replicas"], d["p90_latency"],
                label=ctrl, c=CONTROLLER_COLORS[ctrl],
                alpha=0.7, s=80, edgecolor="black", linewidth=0.5,
            )
        ax.set_title(BENCHMARK_LABEL[bench])
        ax.set_xlabel("avg replica count (per service, mean)")
        ax.set_ylabel("p90 latency (ms, mean across services)")
        ax.legend(title="controller")
        ax.grid(True, alpha=0.3)
    fig.suptitle("Eficiencia: replicas (costo) vs latencia (rendimiento). Esquina ↙ = mejor.")
    fig.tight_layout()
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_double_scaling_bars(df: pd.DataFrame, out: Path) -> None:
    """Bar chart of double_scaling_count by (workload, controller) per benchmark.

    SLO rate plot was skipped because slo_violation_rate=0 universal in this
    dataset. Double-scaling is the next-most-informative failure mode.
    """
    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    width = 0.35
    x = range(len(WORKLOAD_ORDER))
    for ax, bench in zip(axes, ["online-boutique", "train-ticket"]):
        sub = df[df.benchmark == bench]
        means = sub.groupby(["controller", "workload"])["double_scaling_count"].mean().unstack(fill_value=0)
        means = means.reindex(columns=WORKLOAD_ORDER, fill_value=0)
        for i, ctrl in enumerate(["vanilla", "khpa"]):
            if ctrl in means.index:
                vals = [means.loc[ctrl, w] for w in WORKLOAD_ORDER]
                offset = (i - 0.5) * width
                ax.bar([xi + offset for xi in x], vals,
                       width=width, label=ctrl, color=CONTROLLER_COLORS[ctrl], alpha=0.8)
        ax.set_xticks(list(x))
        ax.set_xticklabels(WORKLOAD_ORDER, rotation=20)
        ax.set_title(BENCHMARK_LABEL[bench])
        ax.set_ylabel("double_scaling_count (mean across reps)")
        ax.legend(title="controller")
        ax.grid(True, axis="y", alpha=0.3)
    fig.suptitle("Double-scaling events por workload × controller")
    fig.tight_layout()
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aggregate", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    if not args.aggregate.exists():
        print(f"aggregate not found: {args.aggregate}", file=sys.stderr)
        return 1

    df = pd.read_csv(args.aggregate)
    runs = per_run_totals(df)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    p1 = args.out_dir / "comparison_phantom_by_controller.png"
    p2 = args.out_dir / "comparison_replica_efficiency.png"
    p3 = args.out_dir / "comparison_double_scaling.png"
    plot_phantom_boxplot(runs, p1)
    plot_replica_efficiency(runs, p2)
    plot_double_scaling_bars(runs, p3)

    print("=== plots ===")
    print(f"  {p1}")
    print(f"  {p2}")
    print(f"  {p3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
