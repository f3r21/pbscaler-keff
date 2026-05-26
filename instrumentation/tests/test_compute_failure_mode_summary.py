"""Synthetic tests for compute_failure_mode_summary."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from compute_failure_mode_summary import (
    compute_double_scaling_counts,
    compute_phantom_metrics,
    compute_ping_pong_counts,
    merge_summary,
)


def _ts(offset_s: int) -> str:
    base = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_s)).isoformat()


def _write_phantom(path: Path, rows: list[tuple[int, str, int, int]]) -> None:
    df = pd.DataFrame(
        [
            {
                "timestamp": _ts(t),
                "service": svc,
                "declared": decl,
                "ready": rdy,
                "delta": decl - rdy,
            }
            for t, svc, decl, rdy in rows
        ]
    )
    df.to_csv(path, index=False)


def test_phantom_integral_matches_manual_calculation(tmp_path: Path):
    csv = tmp_path / "phantom_capacity.csv"
    _write_phantom(
        csv,
        [
            (0,  "adservice", 1, 1),  # delta=0
            (5,  "adservice", 2, 1),  # delta=1
            (10, "adservice", 3, 1),  # delta=2
            (15, "adservice", 3, 3),  # delta=0
        ],
    )
    metrics = compute_phantom_metrics(csv)
    row = metrics.iloc[0]
    # interval inferred = 5 s; sum(delta) = 0+1+2+0 = 3 → integral = 15 pod·s
    assert row["service"] == "adservice"
    assert row["phantom_integral"] == 15.0
    assert row["phantom_max"] == 2
    assert row["phantom_duration_s"] == 10.0  # two samples with delta>0 × 5 s


def test_phantom_metrics_returns_empty_for_missing_file(tmp_path: Path):
    metrics = compute_phantom_metrics(tmp_path / "nope.csv")
    assert metrics.empty


def test_double_scaling_counts_filters_to_had_phantom_only(tmp_path: Path):
    csv = tmp_path / "double_scaling.csv"
    pd.DataFrame(
        [
            {"timestamp": _ts(0),  "service": "ad",  "from_replicas": 1, "to_replicas": 2,
             "pre_window_s": 60, "max_delta_in_window": 1, "had_phantom": True},
            {"timestamp": _ts(30), "service": "ad",  "from_replicas": 2, "to_replicas": 3,
             "pre_window_s": 60, "max_delta_in_window": 0, "had_phantom": False},
            {"timestamp": _ts(60), "service": "fe",  "from_replicas": 1, "to_replicas": 2,
             "pre_window_s": 60, "max_delta_in_window": 2, "had_phantom": True},
        ]
    ).to_csv(csv, index=False)

    counts = compute_double_scaling_counts(csv)
    counts = counts.set_index("service")["double_scaling_count"].to_dict()
    assert counts == {"ad": 1, "fe": 1}


def test_ping_pong_counts_aggregates(tmp_path: Path):
    csv = tmp_path / "instances.ping_pong.csv"
    pd.DataFrame(
        [
            {"service": "checkout", "first_event": _ts(0),  "second_event": _ts(60),
             "first_delta": 1, "second_delta": -1,
             "first_replicas": "1 -> 2", "second_replicas": "2 -> 1", "interval_s": 60.0},
            {"service": "checkout", "first_event": _ts(120), "second_event": _ts(180),
             "first_delta": 2, "second_delta": -2,
             "first_replicas": "1 -> 3", "second_replicas": "3 -> 1", "interval_s": 60.0},
            {"service": "frontend", "first_event": _ts(240), "second_event": _ts(300),
             "first_delta": 1, "second_delta": -1,
             "first_replicas": "1 -> 2", "second_replicas": "2 -> 1", "interval_s": 60.0},
        ]
    ).to_csv(csv, index=False)

    counts = compute_ping_pong_counts(tmp_path)
    counts = counts.set_index("service")["ping_pong_count"].to_dict()
    assert counts == {"checkout": 2, "frontend": 1}


def test_merge_summary_fills_zeros_and_attaches_metadata(tmp_path: Path):
    phantom = pd.DataFrame(
        [{"service": "adservice", "phantom_integral": 10.0, "phantom_max": 2, "phantom_duration_s": 5.0}]
    )
    double = pd.DataFrame([{"service": "checkout", "double_scaling_count": 3}])
    ping_pong = pd.DataFrame([{"service": "checkout", "ping_pong_count": 1}])
    replica = pd.DataFrame([{"service": "adservice", "avg_replica_count": 1.5,
                             "max_replica_count": 3, "total_replica_seconds": 450.0,
                             "scale_operation_count": 4, "replica_count_std": 0.7}])
    p90 = pd.DataFrame([{"service": "checkout", "p90_latency_mean": 250.0}])
    slo = pd.DataFrame([{"service": "checkout", "slo_violation_rate": 0.1}])
    metadata = {"benchmark": "online-boutique", "controller": "vanilla",
                "workload": "step", "rep": 2, "seed": 242}

    summary = merge_summary(phantom, double, ping_pong, replica, p90, slo, metadata)
    summary = summary.set_index("service")

    # adservice has phantom + replica but no double/ping_pong/p90/slo
    assert summary.loc["adservice", "phantom_integral"] == 10.0
    assert summary.loc["adservice", "double_scaling_count"] == 0
    assert summary.loc["adservice", "ping_pong_count"] == 0
    assert summary.loc["adservice", "avg_replica_count"] == 1.5
    assert summary.loc["adservice", "p90_latency_mean"] == 0.0
    # checkout has counts + p90 + slo but no phantom/replica
    assert summary.loc["checkout", "phantom_integral"] == 0
    assert summary.loc["checkout", "double_scaling_count"] == 3
    assert summary.loc["checkout", "ping_pong_count"] == 1
    assert summary.loc["checkout", "p90_latency_mean"] == 250.0
    assert summary.loc["checkout", "slo_violation_rate"] == 0.1
    assert summary.loc["checkout", "avg_replica_count"] == 0
    # metadata propagated
    assert (summary["benchmark"] == "online-boutique").all()
    assert (summary["controller"] == "vanilla").all()
    assert (summary["workload"] == "step").all()
    assert (summary["rep"] == 2).all()
    assert (summary["seed"] == 242).all()


def test_phantom_real_excludes_steady_state_flap(tmp_path: Path):
    """Probe-flap with declared constant must NOT count toward phantom_integral_real.

    Scenario: declared=1 throughout the experiment; ready toggles between 1
    and 0 every 5s due to a flapping liveness probe. There is no scale-up
    event, so phantom_integral_real should be 0. phantom_integral_total
    should reflect the noise (4 flap samples × 1 × 5s = 20).
    """
    csv = tmp_path / "phantom_capacity.csv"
    _write_phantom(
        csv,
        [
            (0,  "flapping", 1, 1),  # ready
            (5,  "flapping", 1, 0),  # flap (delta=1, declared unchanged)
            (10, "flapping", 1, 1),
            (15, "flapping", 1, 0),  # flap
            (20, "flapping", 1, 1),
            (25, "flapping", 1, 0),  # flap
            (30, "flapping", 1, 1),
            (35, "flapping", 1, 0),  # flap
        ],
    )
    metrics = compute_phantom_metrics(csv)
    row = metrics.iloc[0]
    assert row["service"] == "flapping"
    assert row["phantom_integral_real"] == 0.0  # no scale-up events
    assert row["phantom_integral"] == 0.0       # primary metric == real
    assert row["phantom_integral_total"] == 20.0  # 4 flaps × 5s


def test_phantom_real_includes_phantom_within_scaling_window(tmp_path: Path):
    """Phantom occurring within 60s after a declared++ event MUST count.

    Scenario: at t=0 declared=1 ready=1 (steady). At t=5 declared goes to 2,
    but ready stays at 1 (the new pod is warming up). Ready catches up at
    t=30. Then declared stays at 2 for the rest of the run.

    Expected: phantom_integral_real counts the deltas at t=5, 10, 15, 20,
    25 = 5 samples × delta=1 × 5s = 25 pod·s.
    """
    csv = tmp_path / "phantom_capacity.csv"
    _write_phantom(
        csv,
        [
            (0,  "warming", 1, 1),   # before scale-up
            (5,  "warming", 2, 1),   # scale-up event, phantom starts
            (10, "warming", 2, 1),   # warming
            (15, "warming", 2, 1),
            (20, "warming", 2, 1),
            (25, "warming", 2, 1),
            (30, "warming", 2, 2),   # caught up
            (35, "warming", 2, 2),
        ],
    )
    metrics = compute_phantom_metrics(csv)
    row = metrics.iloc[0]
    assert row["service"] == "warming"
    # 5 phantom samples (t=5..25) × delta=1 × 5s = 25 pod·s
    assert row["phantom_integral_real"] == 25.0
    assert row["phantom_integral"] == 25.0
    # No flap, so total == real here
    assert row["phantom_integral_total"] == 25.0


def test_phantom_real_excludes_phantom_outside_60s_window(tmp_path: Path):
    """Phantom that occurs more than 60s after the last scale-up is flap, not real.

    Scenario: declared 1→2 at t=5, ready catches up immediately at t=10.
    Then at t=70 (65s after the scale-up; outside the 60s window) the
    pod's ready flaps to 0 (delta=1) for one sample, then back to 1.

    Expected: phantom_integral_real == 0 (no phantom inside the window
    [5, 65]). phantom_integral_total == 5 (one out-of-window flap sample).
    """
    csv = tmp_path / "phantom_capacity.csv"
    _write_phantom(
        csv,
        [
            (0,  "post-scale", 1, 1),
            (5,  "post-scale", 2, 1),  # scale-up, delta=1 within window
            (10, "post-scale", 2, 2),  # caught up — window starts here for delta=0
            (65, "post-scale", 2, 2),  # still inside window (5..65)
            (70, "post-scale", 2, 1),  # OUT of window — flap
            (75, "post-scale", 2, 2),
        ],
    )
    metrics = compute_phantom_metrics(csv)
    row = metrics.iloc[0]
    # Window: (5, 65]. Sample at t=5 has delta=1 (inside). Sample at t=70
    # has delta=1 but is OUTSIDE the window.
    interval_s = 5.0  # inferred median from 5,5,5,55,5,5; median = 5
    # phantom_real: only t=5 sample counts → delta=1 × interval=5 (the
    # interval used is _infer_interval_s which is the median)
    # but median of diffs in this dataset = 5 (5,5,55,5,5 → median 5)
    # so phantom_real = 1 * 5 = 5 (the t=5 sample)
    # Actually: in_window covers t in [5, 65]. Inside that range: t=5 has
    # delta=1, t=10 delta=0, t=65 delta=0. So sum_delta_in_window = 1.
    # integral_real = 1 * interval(5) = 5.0
    assert row["phantom_integral_real"] == 5.0
    assert row["phantom_integral"] == 5.0
    # Total: t=5 (delta=1) + t=70 (delta=1) = 2 → integral_total = 10
    assert row["phantom_integral_total"] == 10.0
