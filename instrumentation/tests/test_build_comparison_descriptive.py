"""Tests para build_comparison_descriptive.py.

Cubren el refactor 2026-05-28 que generaliza de 2 controllers (vanilla vs
khpa) a N controllers vs baseline configurable.
"""

from __future__ import annotations

import pandas as pd
import pytest

from build_comparison_descriptive import (
    build_rows,
    consistent_sign_pairwise,
    order_controllers,
    to_markdown,
)
from build_comparison_table import aggregate_per_run


def _make_per_run(controllers: list[str], slo_by_ctrl: dict[str, list[float]]) -> pd.DataFrame:
    """Construye un per_run df con (benchmark=online-boutique, workload=step)
    para los controllers dados y la métrica slo_violation_rate.

    slo_by_ctrl: dict controller -> [rep1, rep2, rep3] valores.
    """
    rows = []
    for ctrl in controllers:
        for i, val in enumerate(slo_by_ctrl[ctrl], start=1):
            rows.append({
                "benchmark": "online-boutique",
                "workload": "step",
                "controller": ctrl,
                "rep": i,
                "slo_violation_rate": val,
            })
    return pd.DataFrame(rows)


class TestConsistentSignPairwise:
    def test_baseline_strictly_greater_all_reps(self):
        # vanilla = [0.5, 0.6, 0.7], khpa = [0.1, 0.2, 0.3]
        # min(vanilla)=0.5 > max(khpa)=0.3 -> TRUE
        assert consistent_sign_pairwise([0.5, 0.6, 0.7], [0.1, 0.2, 0.3]) == "TRUE"

    def test_baseline_strictly_less_all_reps(self):
        assert consistent_sign_pairwise([0.1, 0.2, 0.3], [0.5, 0.6, 0.7]) == "TRUE"

    def test_overlapping_ranges_false(self):
        assert consistent_sign_pairwise([0.1, 0.5, 0.9], [0.4, 0.5, 0.6]) == "FALSE"

    def test_all_zero_returns_dash(self):
        assert consistent_sign_pairwise([0, 0, 0], [0, 0, 0]) == "—"

    def test_tie_at_boundary_is_false(self):
        # min(baseline)=0.5 equals max(other)=0.5 -> NOT strict -> FALSE
        assert consistent_sign_pairwise([0.5, 0.6, 0.7], [0.3, 0.4, 0.5]) == "FALSE"


class TestOrderControllers:
    def test_alphabetical_default(self):
        result = order_controllers(["khpa", "vanilla", "keff"], "vanilla", None)
        assert result == ["keff", "khpa"]

    def test_custom_order_respected(self):
        result = order_controllers(
            ["khpa", "vanilla", "keff", "NaiveTemporalGate"],
            baseline="vanilla",
            controller_order=["keff", "NaiveTemporalGate", "khpa"],
        )
        assert result == ["keff", "NaiveTemporalGate", "khpa"]

    def test_custom_order_filters_absent(self):
        result = order_controllers(
            ["khpa", "vanilla"],
            baseline="vanilla",
            controller_order=["keff", "khpa", "missing"],
        )
        assert result == ["khpa"]

    def test_baseline_never_in_output(self):
        result = order_controllers(["vanilla", "khpa", "keff"], "vanilla", None)
        assert "vanilla" not in result


class TestBuildRowsN2:
    """Regression: el caso N=2 (vanilla vs khpa de una campaña previa) debe producir los
    mismos valores que la versión previa."""

    def test_two_controllers_yields_one_row_per_metric(self):
        per_run = _make_per_run(
            controllers=["vanilla", "khpa"],
            slo_by_ctrl={
                "vanilla": [0.12, 0.10, 0.13],
                "khpa": [0.09, 0.09, 0.10],
            },
        )
        rows = build_rows(per_run, metrics=["slo_violation_rate"], baseline="vanilla")
        assert len(rows) == 1
        r = rows[0]
        assert r["baseline"] == "vanilla"
        assert r["controller"] == "khpa"
        assert r["base_med"] == pytest.approx(0.12)
        assert r["other_med"] == pytest.approx(0.09)
        # ratio = 0.09 / 0.12 = 0.75
        assert r["ratio"] == "0.75"
        # vanilla reps = [0.10, 0.12, 0.13], khpa reps = [0.09, 0.09, 0.10]
        # min(vanilla)=0.10 > max(khpa)=0.10? NO (igual) -> FALSE
        assert r["signo"] == "FALSE"


class TestBuildRowsN4:
    """N=4 controllers (vanilla baseline + keff + NaiveTemporalGate + khpa):
    3 filas por (benchmark, workload, metric)."""

    def test_four_controllers_three_rows_per_metric(self):
        per_run = _make_per_run(
            controllers=["vanilla", "keff", "NaiveTemporalGate", "khpa"],
            slo_by_ctrl={
                "vanilla": [0.12, 0.10, 0.13],
                "keff": [0.08, 0.07, 0.09],
                "NaiveTemporalGate": [0.11, 0.11, 0.12],
                "khpa": [0.09, 0.09, 0.10],
            },
        )
        rows = build_rows(
            per_run,
            metrics=["slo_violation_rate"],
            baseline="vanilla",
            controller_order=["keff", "NaiveTemporalGate", "khpa"],
        )
        assert len(rows) == 3
        # Orden respetado
        assert [r["controller"] for r in rows] == ["keff", "NaiveTemporalGate", "khpa"]
        # Baseline replicado en cada fila
        assert all(r["baseline"] == "vanilla" for r in rows)
        assert all(r["base_med"] == pytest.approx(0.12) for r in rows)
        # keff vs vanilla: min(vanilla)=0.10 > max(keff)=0.09 -> TRUE
        assert rows[0]["signo"] == "TRUE"
        # NaiveTemporalGate vs vanilla: ranges overlap (vanilla 0.10-0.13, naive 0.11-0.12) -> FALSE
        assert rows[1]["signo"] == "FALSE"

    def test_baseline_absent_returns_empty(self):
        per_run = _make_per_run(
            controllers=["keff", "khpa"],
            slo_by_ctrl={
                "keff": [0.08, 0.07, 0.09],
                "khpa": [0.09, 0.09, 0.10],
            },
        )
        rows = build_rows(per_run, metrics=["slo_violation_rate"], baseline="vanilla")
        assert rows == []


class TestAggregatePerRunCheckoutScoped:
    """slo_violation_rate y p95 deben tomarse del checkout (Cap_3:238),
    NO promediados entre servicios. cpu sigue siendo suma cluster-wide."""

    def _multi_service_run(self) -> pd.DataFrame:
        # 1 run, 3 servicios: checkout viola el SLO; los backends no.
        common = {"benchmark": "online-boutique", "workload": "step",
                  "controller": "vanilla", "rep": 1}
        return pd.DataFrame([
            {**common, "service": "checkoutservice",
             "slo_violation_rate": 0.30, "p95_latency_mean": 600.0,
             "cpu_usage_integral_seconds": 100.0},
            {**common, "service": "adservice",
             "slo_violation_rate": 0.0, "p95_latency_mean": 5.0,
             "cpu_usage_integral_seconds": 50.0},
            {**common, "service": "shippingservice",
             "slo_violation_rate": 0.0, "p95_latency_mean": 5.0,
             "cpu_usage_integral_seconds": 50.0},
        ])

    def test_slo_and_p95_are_checkout_not_mean(self):
        per_run = aggregate_per_run(self._multi_service_run())
        assert len(per_run) == 1
        r = per_run.iloc[0]
        # checkout-scoped: el checkout (0.30 / 600ms), NO el promedio (0.10 / ~203ms)
        assert r["slo_violation_rate"] == pytest.approx(0.30)
        assert r["p95_latency_mean"] == pytest.approx(600.0)
        # cpu sigue siendo suma cluster-wide (100+50+50)
        assert r["cpu_usage_integral_seconds"] == pytest.approx(200.0)

    def test_missing_checkout_row_yields_nan(self):
        df = self._multi_service_run()
        df = df[df["service"] != "checkoutservice"]
        per_run = aggregate_per_run(df)
        assert pd.isna(per_run.iloc[0]["slo_violation_rate"])
        # cpu sigue sumando los servicios restantes (50+50)
        assert per_run.iloc[0]["cpu_usage_integral_seconds"] == pytest.approx(100.0)


class TestToMarkdownStructure:
    def test_header_includes_baseline_name(self):
        per_run = _make_per_run(
            controllers=["vanilla", "khpa"],
            slo_by_ctrl={"vanilla": [0.1] * 3, "khpa": [0.1] * 3},
        )
        rows = build_rows(per_run, ["slo_violation_rate"], baseline="vanilla")
        md = to_markdown(rows, title="test", baseline="vanilla")
        assert "mediana_vanilla" in md
        assert "Baseline = `vanilla`" in md

    def test_empty_rows_renders_no_rows(self):
        md = to_markdown([], title="empty", baseline="vanilla")
        assert "(no rows)" in md
