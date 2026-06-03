"""Tests para el manejo de layouts y normalización de controller en aggregate_results.

Refactor 2026-05-28 (C3): aggregate_results debe leer tanto el layout antiguo
(`<bench>[-khpa]/<workload>/run<N>/`) como el canónico
(`<bench>/<workload>/<controller>/seed<N>/`), normalizar nombres de controller
runtime a la vocabulary canónica, y excluir árboles `_archive`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aggregate_results import (
    normalize_controller,
    infer_controller_from_path,
    resolve_controller,
    find_summary_files,
    _is_excluded,
)

HDR_COLS = ["benchmark", "controller", "workload", "rep", "seed", "service",
            "phantom_integral", "slo_violation_rate"]


def _write_summary(d: Path, controller: str, workload: str, rep: int) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    f = d / "failure_modes_summary.csv"
    pd.DataFrame([{
        "benchmark": "online-boutique", "controller": controller,
        "workload": workload, "rep": rep, "seed": 42 + rep * 100,
        "service": "frontend", "phantom_integral": 1.0, "slo_violation_rate": 0.08,
    }])[HDR_COLS].to_csv(f, index=False)
    return f


class TestNormalizeController:
    @pytest.mark.parametrize("raw,expected", [
        ("vanilla", "vanilla"), ("PBScaler", "vanilla"), ("pbscaler", "vanilla"),
        ("keff", "keff"), ("PBScaler-keff", "keff"), ("PBSCALER-KEFF", "keff"),
        ("naive", "naive"), ("NaiveTemporalGate", "naive"),
        ("khpa", "khpa"), ("KHPA", "khpa"),
    ])
    def test_known_names_map(self, raw, expected):
        assert normalize_controller(raw) == expected

    def test_unknown_returns_none(self):
        assert normalize_controller("step") is None
        assert normalize_controller("SHOWAR") is None
        assert normalize_controller(None) is None


class TestInferFromPath:
    def test_sprint1_khpa_suffix(self):
        p = Path("results/sprint-1/online-boutique-khpa/step/run1/failure_modes_summary.csv")
        assert infer_controller_from_path(p) == "khpa"

    def test_sprint1_vanilla_default(self):
        p = Path("results/sprint-1/online-boutique/step/run1/failure_modes_summary.csv")
        assert infer_controller_from_path(p) == "vanilla"

    def test_sprintA_controller_segment(self):
        p = Path("results/sprint-A/online-boutique/step/PBScaler-keff/seed1/failure_modes_summary.csv")
        assert infer_controller_from_path(p) == "keff"

    def test_sprintA_naive_segment(self):
        p = Path("results/sprint-A/online-boutique/bursty/NaiveTemporalGate/seed2/failure_modes_summary.csv")
        assert infer_controller_from_path(p) == "naive"


class TestResolveController:
    def test_prefers_csv_column(self):
        df = pd.DataFrame([{"controller": "PBScaler-keff"}])
        # path says vanilla, column says keff -> column wins
        p = Path("results/sprint-A/online-boutique/step/X/seed1/failure_modes_summary.csv")
        assert resolve_controller(df, p) == "keff"

    def test_falls_back_when_column_invalid(self):
        # column has a workload value (schema drift) -> fall back to path
        df = pd.DataFrame([{"controller": "step"}])
        p = Path("results/sprint-1/online-boutique-khpa/step/run1/failure_modes_summary.csv")
        assert resolve_controller(df, p) == "khpa"

    def test_falls_back_when_no_column(self):
        df = pd.DataFrame([{"service": "frontend"}])
        p = Path("results/sprint-1/online-boutique/step/run1/failure_modes_summary.csv")
        assert resolve_controller(df, p) == "vanilla"


class TestExclusion:
    def test_archive_excluded(self):
        root = Path("/r")
        assert _is_excluded(root / "_archive" / "x" / "failure_modes_summary.csv", root)
        assert _is_excluded(root / "_archived_sprint1_old" / "x" / "f.csv", root)

    def test_normal_not_excluded(self):
        root = Path("/r")
        assert not _is_excluded(root / "online-boutique" / "step" / "run1" / "f.csv", root)


class TestFindSummaryFiles:
    def test_finds_both_layouts_excludes_archive(self, tmp_path):
        # layout antiguo
        _write_summary(tmp_path / "online-boutique" / "step" / "run1", "vanilla", "step", 1)
        _write_summary(tmp_path / "online-boutique-khpa" / "step" / "run1", "khpa", "step", 1)
        # layout canónico
        _write_summary(tmp_path / "online-boutique" / "step" / "PBScaler-keff" / "seed1", "PBScaler-keff", "step", 1)
        _write_summary(tmp_path / "online-boutique" / "bursty" / "NaiveTemporalGate" / "seed2", "NaiveTemporalGate", "bursty", 2)
        # Archived — must be skipped
        _write_summary(tmp_path / "_archive" / "online-boutique" / "step" / "run9", "vanilla", "step", 9)

        found = find_summary_files(tmp_path)
        assert len(found) == 4, f"expected 4, got {len(found)}: {found}"
        assert all("_archive" not in p.parts for p in found)
