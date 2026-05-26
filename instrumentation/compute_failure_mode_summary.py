"""Compute per-(workload, run, service) failure-mode summary for one run.

Inputs (all in the same run directory):
    instances.csv          PBScaler scaling history (timestamp, <svc>&count)
    phantom_capacity.csv   measure_phantom_capacity output
    double_scaling.csv     detect_double_scaling output
    <instances>.ping_pong.csv  detect_ping_pong output (optional)
    metadata.json          run metadata with benchmark/workload/rep/seed

Output (one CSV in the same dir):
    failure_modes_summary.csv with columns:
        benchmark, workload, rep, seed, service,
        phantom_integral, phantom_max, phantom_duration_s,
        double_scaling_count, ping_pong_count

`phantom_integral` is the time-integrated phantom capacity in pod·seconds:
    sum(delta * interval_s) over all samples
where interval_s is inferred from the median gap between consecutive
phantom_capacity.csv timestamps for that service.

Usage:
    python compute_failure_mode_summary.py path/to/runX/ \\
        --out path/to/runX/failure_modes_summary.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _infer_interval_s(timestamps: pd.Series) -> float:
    """Median gap between samples, in seconds. 5.0 if undecidable."""
    if len(timestamps) < 2:
        return 5.0
    diffs = timestamps.diff().dropna().dt.total_seconds()
    if diffs.empty:
        return 5.0
    return float(diffs.median())


def compute_phantom_metrics(phantom_csv: Path, scaling_window_s: int = 60) -> pd.DataFrame:
    """Per-service phantom metrics, distinguishing real vs flap.

    Definitions:
        phantom_integral_total: sum(delta * interval_s) over all samples — includes
            BOTH scaling-induced phantom AND probe-flap noise (pods cycling
            in/out of Ready while declared replicas stays constant). Kept for
            backward compatibility but should not drive conclusions.

        phantom_integral_real: same sum, but only over samples that fall WITHIN
            scaling_window_s seconds of a declared-replicas-up event for the
            same service. This isolates phantom caused by autoscaler decisions
            from steady-state probe flapping.

        phantom_integral: alias for phantom_integral_real (the metric for thesis).
    """
    cols = [
        "service",
        "phantom_integral", "phantom_integral_total", "phantom_integral_real",
        "phantom_max", "phantom_duration_s",
    ]
    if not phantom_csv.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(phantom_csv, parse_dates=["timestamp"])
    if df.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    window = pd.Timedelta(seconds=scaling_window_s)
    for svc, g in df.groupby("service"):
        g = g.sort_values("timestamp").reset_index(drop=True)
        interval_s = _infer_interval_s(g["timestamp"])
        delta = g["delta"].astype(int)

        # Mark samples within scaling_window_s of a declared-up event
        decl_diff = g["declared"].diff().fillna(0)
        ups = g.index[decl_diff > 0].tolist()
        in_window = pd.Series(False, index=g.index)
        for idx in ups:
            ts0 = g.loc[idx, "timestamp"]
            mask = (g["timestamp"] >= ts0) & (g["timestamp"] <= ts0 + window)
            in_window |= mask

        integral_total = float(delta.sum() * interval_s)
        integral_real = float(delta[in_window].sum() * interval_s)

        rows.append(
            {
                "service": svc,
                "phantom_integral": integral_real,           # primary metric
                "phantom_integral_total": integral_total,    # noisy reference
                "phantom_integral_real": integral_real,
                "phantom_max": int(delta.max()) if not delta.empty else 0,
                "phantom_duration_s": float((delta > 0).sum() * interval_s),
            }
        )
    return pd.DataFrame(rows)


def compute_double_scaling_counts(double_csv: Path) -> pd.DataFrame:
    if not double_csv.exists():
        return pd.DataFrame(columns=["service", "double_scaling_count"])
    df = pd.read_csv(double_csv)
    if df.empty:
        return pd.DataFrame(columns=["service", "double_scaling_count"])
    counts = (
        df[df["had_phantom"]]
        .groupby("service")
        .size()
        .reset_index(name="double_scaling_count")
    )
    return counts


def compute_ping_pong_counts(run_dir: Path) -> pd.DataFrame:
    """detect_ping_pong.py writes <instances>.ping_pong.csv next to the input."""
    candidates = list(run_dir.glob("*.ping_pong.csv"))
    if not candidates:
        return pd.DataFrame(columns=["service", "ping_pong_count"])
    try:
        df = pd.read_csv(candidates[0])
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=["service", "ping_pong_count"])
    if df.empty:
        return pd.DataFrame(columns=["service", "ping_pong_count"])
    return (
        df.groupby("service").size().reset_index(name="ping_pong_count")
    )


def compute_replica_metrics(instances_csv: Path) -> pd.DataFrame:
    """Per-service replica statistics from instances.csv timeline.

    Returns: avg_replica_count, max_replica_count, total_replica_seconds,
    scale_operation_count, replica_count_std.
    """
    cols = [
        "service",
        "avg_replica_count",
        "max_replica_count",
        "total_replica_seconds",
        "scale_operation_count",
        "replica_count_std",
    ]
    if not instances_csv.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(instances_csv, parse_dates=["timestamp"])
    except (pd.errors.EmptyDataError, ValueError):
        return pd.DataFrame(columns=cols)
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame(columns=cols)

    # Service columns end in '&count'
    svc_cols = [c for c in df.columns if c.endswith("&count")]
    if not svc_cols:
        return pd.DataFrame(columns=cols)

    # Median sampling interval (PBScaler logs every ~5 s)
    interval_s = _infer_interval_s(df["timestamp"].sort_values())

    rows = []
    for col in svc_cols:
        svc = col.removesuffix("&count")
        values = df[col].fillna(0).astype(float)
        if values.empty:
            continue
        diffs = values.diff().fillna(0)
        rows.append(
            {
                "service": svc,
                "avg_replica_count": float(values.mean()),
                "max_replica_count": int(values.max()) if not values.empty else 0,
                "total_replica_seconds": float(values.sum() * interval_s),
                "scale_operation_count": int((diffs != 0).sum()),
                "replica_count_std": float(values.std(ddof=0)) if len(values) > 1 else 0.0,
            }
        )
    return pd.DataFrame(rows)


def compute_latency_p90_mean(latency_csv: Path) -> pd.DataFrame:
    """Per-service mean of the &p90 column in latency.csv."""
    cols = ["service", "p90_latency_mean"]
    if not latency_csv.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(latency_csv)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=cols)
    if df.empty:
        return pd.DataFrame(columns=cols)

    p90_cols = [c for c in df.columns if c.endswith("&p90")]
    rows = []
    for col in p90_cols:
        svc = col.removesuffix("&p90")
        series = df[col].dropna()
        rows.append(
            {
                "service": svc,
                "p90_latency_mean": float(series.mean()) if not series.empty else 0.0,
            }
        )
    return pd.DataFrame(rows)


def compute_slo_violation_rate(
    latency_csv: Path, slo_threshold_ms: float = 500.0
) -> pd.DataFrame:
    """Per-service slo_violation_rate computed client-side from latency.csv.

    Reads latency.csv (which has columns `<svc>&p90` per service per
    timestamp) and reports the fraction of timestamps where the service's
    p90 exceeded `slo_threshold_ms`. This is a more rigorous proxy than the
    Istio-bucket query that originally fed slo_violations.csv (which
    returned empty in all 60 runs of Sprint 1+1.5 due to a bucket-boundary
    mismatch in the Istio histogram).
    """
    cols = ["service", "slo_violation_rate"]
    if not latency_csv.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(latency_csv)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=cols)
    if df.empty:
        return pd.DataFrame(columns=cols)

    p90_cols = [c for c in df.columns if c.endswith("&p90")]
    rows = []
    for col in p90_cols:
        svc = col.removesuffix("&p90")
        series = df[col].dropna()
        if series.empty:
            rate = 0.0
        else:
            rate = float((series > slo_threshold_ms).mean())
        rows.append({"service": svc, "slo_violation_rate": rate})
    return pd.DataFrame(rows)


def compute_latency_p95_mean(latency_p95_csv: Path) -> pd.DataFrame:
    """Per-service mean of the &p95 column in latency_p95.csv.

    Note: P95 lives in a separate file from P90/P99 because the original
    Prometheus query split p95 into its own export.
    """
    cols = ["service", "p95_latency_mean"]
    if not latency_p95_csv.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(latency_p95_csv)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=cols)
    if df.empty:
        return pd.DataFrame(columns=cols)

    p95_cols = [c for c in df.columns if c.endswith("&p95")]
    rows = []
    for col in p95_cols:
        svc = col.removesuffix("&p95")
        series = df[col].dropna()
        rows.append(
            {
                "service": svc,
                "p95_latency_mean": float(series.mean()) if not series.empty else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=cols)


def compute_latency_p99_mean(latency_csv: Path) -> pd.DataFrame:
    """Per-service mean of the &p99 column in latency.csv (apéndice/DETAIL)."""
    cols = ["service", "p99_latency_mean"]
    if not latency_csv.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(latency_csv)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=cols)
    if df.empty:
        return pd.DataFrame(columns=cols)

    p99_cols = [c for c in df.columns if c.endswith("&p99")]
    rows = []
    for col in p99_cols:
        svc = col.removesuffix("&p99")
        series = df[col].dropna()
        rows.append(
            {
                "service": svc,
                "p99_latency_mean": float(series.mean()) if not series.empty else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=cols)


def compute_cpu_usage_integral(svc_metric_csv: Path) -> pd.DataFrame:
    """Per-service integral of CPU usage over the run (CPU-seconds).

    Equivalent to the integral of container_cpu_usage_seconds_total
    prescribed by Cap_3:238. Aligns with cost metrics reported by Cushion
    (CPU rate), GRAF (CPU saved), Morphis (QPS/CPU).
    """
    cols = ["service", "cpu_usage_integral_seconds"]
    if not svc_metric_csv.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(svc_metric_csv, parse_dates=["timestamp"])
    except (pd.errors.EmptyDataError, ValueError):
        return pd.DataFrame(columns=cols)
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame(columns=cols)

    interval_s = _infer_interval_s(df["timestamp"].sort_values())
    cpu_cols = [c for c in df.columns if c.endswith("&cpu_usage")]
    rows = []
    for col in cpu_cols:
        svc = col.removesuffix("&cpu_usage")
        series = df[col].dropna().astype(float)
        rows.append(
            {
                "service": svc,
                "cpu_usage_integral_seconds": float(series.sum() * interval_s),
            }
        )
    return pd.DataFrame(rows, columns=cols)


def compute_convergence_time(
    latency_csv: Path,
    slo_threshold_ms: float = 500.0,
    convergence_window_s: float = 60.0,
) -> pd.DataFrame:
    """Per-service convergence time after SLO violation (Cap_3:246).

    Convergence definition: time between the detection of an SLO violation
    (p90 > slo_threshold_ms) and the start of ``convergence_window_s`` of
    continuous compliance (Cap_3:246 uses k=4 windows of 15 s = 60 s).

    Since latency.csv samples may not be 15 s aligned (default Prometheus
    scrape is ~5 s), we adapt: compute how many consecutive samples sum to
    ``convergence_window_s`` using the inferred sampling interval, then
    require that many consecutive non-violation samples after each
    violation onset.

    Reports per service the **median** convergence time across all
    violation events. Services with no violations or violations that never
    converge before end-of-run produce ``convergence_time_s = 0`` and
    ``convergence_events = 0`` (interpret with care: 0 may mean "never
    violated" or "never converged"; check ``convergence_events`` to
    disambiguate).
    """
    cols = ["service", "convergence_time_s", "convergence_events"]
    if not latency_csv.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(latency_csv, parse_dates=["timestamp"])
    except (pd.errors.EmptyDataError, ValueError):
        return pd.DataFrame(columns=cols)
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame(columns=cols)

    df = df.sort_values("timestamp").reset_index(drop=True)
    interval_s = _infer_interval_s(df["timestamp"])
    # Number of consecutive non-violation samples required to declare convergence
    k = max(1, int(round(convergence_window_s / max(interval_s, 1e-6))))

    p90_cols = [c for c in df.columns if c.endswith("&p90")]
    rows = []
    for col in p90_cols:
        svc = col.removesuffix("&p90")
        series = df[col].fillna(0).astype(float)
        viol = (series > slo_threshold_ms).to_numpy()
        timestamps = df["timestamp"].to_numpy()
        n = len(viol)
        convergence_times: list[float] = []
        i = 0
        while i < n:
            if not viol[i]:
                i += 1
                continue
            # Violation onset at i. Walk forward looking for k consecutive False.
            violation_start = i
            j = i + 1
            found = False
            while j <= n - k:
                # Check positions [j, j+k) are all False
                if not viol[j : j + k].any():
                    dt = (timestamps[j] - timestamps[violation_start]) / np.timedelta64(1, "s")
                    convergence_times.append(float(dt))
                    i = j + k  # skip past the compliance window
                    found = True
                    break
                j += 1
            if not found:
                break  # No convergence found before end-of-run; stop.

        if convergence_times:
            rows.append(
                {
                    "service": svc,
                    "convergence_time_s": float(np.median(convergence_times)),
                    "convergence_events": len(convergence_times),
                }
            )
        else:
            rows.append(
                {
                    "service": svc,
                    "convergence_time_s": 0.0,
                    "convergence_events": 0,
                }
            )
    return pd.DataFrame(rows, columns=cols)


def load_metadata(run_dir: Path) -> dict:
    meta_path = run_dir / "metadata.json"
    if not meta_path.exists():
        return {"benchmark": "unknown", "workload": "unknown", "rep": -1, "seed": -1}
    with meta_path.open() as fh:
        return json.load(fh)


def merge_summary(
    phantom: pd.DataFrame,
    double: pd.DataFrame,
    ping_pong: pd.DataFrame,
    replica: pd.DataFrame,
    p90: pd.DataFrame,
    slo: pd.DataFrame,
    p95: pd.DataFrame,
    p99: pd.DataFrame,
    cpu_int: pd.DataFrame,
    convergence: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    services = sorted(
        set(phantom["service"])
        | set(double["service"])
        | set(ping_pong["service"])
        | set(replica["service"])
        | set(p90["service"])
        | set(slo["service"])
        | set(p95["service"])
        | set(p99["service"])
        | set(cpu_int["service"])
        | set(convergence["service"])
    )
    if not services:
        return pd.DataFrame()
    out = pd.DataFrame({"service": services})
    for df in (phantom, double, ping_pong, replica, p90, slo, p95, p99, cpu_int, convergence):
        out = out.merge(df, on="service", how="left")

    fill_zero = [
        "phantom_integral", "phantom_integral_total", "phantom_integral_real",
        "phantom_max", "phantom_duration_s",
        "double_scaling_count", "ping_pong_count",
        "avg_replica_count", "max_replica_count", "total_replica_seconds",
        "scale_operation_count", "replica_count_std",
        "p90_latency_mean", "slo_violation_rate",
        "p95_latency_mean", "p99_latency_mean", "cpu_usage_integral_seconds",
        "convergence_time_s", "convergence_events",
    ]
    for col in fill_zero:
        if col not in out.columns:
            out[col] = 0
    out[fill_zero] = out[fill_zero].fillna(0)
    int_cols = ["phantom_max", "double_scaling_count", "ping_pong_count",
                "max_replica_count", "scale_operation_count", "convergence_events"]
    for col in int_cols:
        out[col] = out[col].astype(int)
    out.insert(0, "benchmark", metadata.get("benchmark", "unknown"))
    out.insert(1, "controller", metadata.get("controller", "vanilla"))
    out.insert(2, "workload", metadata.get("workload", "unknown"))
    out.insert(3, "rep", metadata.get("rep", -1))
    out.insert(4, "seed", metadata.get("seed", -1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path, help="directory with one run's CSVs")
    ap.add_argument("--out", type=Path, required=True, help="output summary CSV")
    args = ap.parse_args()

    if not args.run_dir.is_dir():
        print(f"not a directory: {args.run_dir}", file=sys.stderr)
        return 1

    phantom = compute_phantom_metrics(args.run_dir / "phantom_capacity.csv")
    double = compute_double_scaling_counts(args.run_dir / "double_scaling.csv")
    ping_pong = compute_ping_pong_counts(args.run_dir)
    replica = compute_replica_metrics(args.run_dir / "instances.csv")
    p90 = compute_latency_p90_mean(args.run_dir / "latency.csv")
    slo = compute_slo_violation_rate(args.run_dir / "latency.csv")
    p95 = compute_latency_p95_mean(args.run_dir / "latency_p95.csv")
    p99 = compute_latency_p99_mean(args.run_dir / "latency.csv")
    cpu_int = compute_cpu_usage_integral(args.run_dir / "svc_metric.csv")
    convergence = compute_convergence_time(args.run_dir / "latency.csv")
    metadata = load_metadata(args.run_dir)

    summary = merge_summary(phantom, double, ping_pong, replica, p90, slo, p95, p99, cpu_int, convergence, metadata)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.out, index=False)

    print(f"=== {args.run_dir.name} ===")
    if summary.empty:
        print("(no service-level data found)")
    else:
        print(summary.to_string(index=False))
    print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
