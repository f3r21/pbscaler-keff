"""Synthetic tests for aggregate_results.classify_runtime + cross-run merge."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from aggregate_results import classify_runtime, find_summary_files


@pytest.mark.parametrize(
    "service,expected",
    [
        ("adservice", "java"),                  # Online Boutique Java service
        ("frontend", "no-java"),                # Online Boutique Go service
        ("cartservice", "no-java"),             # Online Boutique C#
        ("ts-travel-service", "java"),          # Train Ticket Java
        ("ts-order-service", "java"),
        ("ts-avatar-service", "no-java"),       # Train Ticket Python (exception)
        ("ts-voucher-service", "no-java"),      # Train Ticket Python
        ("checkoutservice", "no-java"),         # OB Go
        ("currencyservice", "no-java"),         # OB Node
    ],
)
def test_classify_runtime(service: str, expected: str):
    assert classify_runtime(service) == expected


def _write_summary(path: Path, benchmark: str, workload: str, rep: int, services: list[tuple[str, float, int, float, int, int]]) -> None:
    rows = []
    for svc, p_int, p_max, p_dur, ds, pp in services:
        rows.append(
            {
                "benchmark": benchmark,
                "workload": workload,
                "rep": rep,
                "seed": 42 + 100 * rep,
                "service": svc,
                "phantom_integral": p_int,
                "phantom_max": p_max,
                "phantom_duration_s": p_dur,
                "double_scaling_count": ds,
                "ping_pong_count": pp,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_find_and_concat_runs(tmp_path: Path):
    # Layout: <root>/<benchmark>/<workload>/run<N>/failure_modes_summary.csv
    ob_step_run1 = tmp_path / "online-boutique" / "step" / "run1"
    ob_step_run1.mkdir(parents=True)
    _write_summary(
        ob_step_run1 / "failure_modes_summary.csv",
        benchmark="online-boutique",
        workload="step",
        rep=1,
        services=[("adservice", 100.0, 3, 50.0, 2, 1)],
    )

    tt_step_run1 = tmp_path / "train-ticket" / "step" / "run1"
    tt_step_run1.mkdir(parents=True)
    _write_summary(
        tt_step_run1 / "failure_modes_summary.csv",
        benchmark="train-ticket",
        workload="step",
        rep=1,
        services=[("ts-travel-service", 800.0, 9, 400.0, 5, 0)],
    )

    paths = find_summary_files(tmp_path)
    assert len(paths) == 2

    # Run the CLI to verify end-to-end
    out = tmp_path / "aggregate.csv"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent.parent / "aggregate_results.py"),
            str(tmp_path),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    df = pd.read_csv(out)
    assert "runtime" in df.columns
    assert set(df["service"]) == {"adservice", "ts-travel-service"}
    assert df.set_index("service")["runtime"]["adservice"] == "java"
    assert df.set_index("service")["runtime"]["ts-travel-service"] == "java"
