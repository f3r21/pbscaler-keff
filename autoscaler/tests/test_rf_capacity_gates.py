"""Acceptance gates for the retrained capacity-aware RF surrogate (Fase 2).

These tests encode the BAR that the retrained RandomForest must clear for keff's
Nivel 1 to be functional (Cap_3 sec:nivel1). They are written TDD-red: against the
inherited model they FAIL (it is replica-blind — verified 2026-06-02: predict_proba
is flat for replicas 1..8, corr(slo_reward, replicas)=0.007, the k_eff signal never
crosses the decision boundary), so they are marked ``xfail`` to keep the suite green
until the model is retrained. When Fase 2 retrains on a controlled replica-sweep
dataset and adopts the capacity-aware feature layout, these should ``xpass``.

Gates (Cap_3 sec:nivel1 + the regime finding, documentacion/scoping-rf-reentrenamiento-2026-06-02.md):
  1. MONOTONICITY: at fixed qps, P(SLO satisfied) must be non-decreasing as a
     service's effective capacity rises. The inherited model is non-monotonic.
  2. K_EFF SENSITIVITY: holding the nominal replica count fixed, varying the
     effective capacity k_eff (warming pods count fractionally, Cap_3 eq:keff)
     must move P(SLO satisfied) by more than NOISE_FLOOR. Today it moves ~0.
  3. NO-LEAKAGE SPLIT (training-pipeline gate, Fase 2): the train/test split must
     be grouped by run/seed, not by row — the synthetic ~11x row expansion leaks
     near-duplicate rows across the split with the current row-level split.

TARGET FEATURE LAYOUT (single source of truth for Fase 2; GA.fitness and
RandomForestClassify.data_loader must both adopt it): per service, the triplet is
[effective_capacity, qps, nominal_count] — i.e. k_eff replaces the dead `idx` slot
(feature_importance 0.0) while nominal count is preserved. Shape stays 30 (10x3) so
the .model remains drop-in.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

# Service order is the contract between training (RandomForestClassify.svcs) and
# inference (GA.fitness over self.svcs). Keep in sync.
SVCS = [
    "adservice", "cartservice", "checkoutservice", "currencyservice",
    "emailservice", "frontend", "paymentservice", "productcatalogservice",
    "recommendationservice", "shippingservice",
]
N_SVCS = len(SVCS)
BOTTLENECK = "checkoutservice"  # the endpoint that defines the 500ms SLO

# Slots per service in the TARGET layout.
EFF_CAP, QPS, COUNT = 0, 1, 2
NOISE_FLOOR = 0.05  # min |delta P(SLO_OK)| that counts as "the model uses k_eff"

_MODEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "simulation" / "boutique" / "RandomForestClassify.model"
)


def _load_model():
    import joblib
    if not _MODEL_PATH.exists():
        pytest.skip(f"surrogate model not found at {_MODEL_PATH}")
    return joblib.load(_MODEL_PATH)


def _p_slo_ok(model, x: np.ndarray) -> float:
    """P(SLO satisfied) for one feature vector, via predict_proba.

    Fase 2 also switches GA.py:145 from .predict() (binary) to predict_proba so
    R1 is continuous (Cap_3:140). These gates assume that continuous reading.
    """
    proba = model.predict_proba(x.reshape(1, -1))[0]
    classes = list(getattr(model, "classes_", [0.0, 1.0]))
    ok_idx = classes.index(1.0) if 1.0 in classes else len(classes) - 1
    return float(proba[ok_idx])


def _vector(eff_caps: dict[str, float], counts: dict[str, int], qps: float) -> np.ndarray:
    """Build a 30-feature vector in the TARGET layout [eff_cap, qps, count] per svc."""
    x = np.zeros(N_SVCS * 3, dtype=float)
    for i, svc in enumerate(SVCS):
        base = i * 3
        x[base + EFF_CAP] = eff_caps.get(svc, 1.0)
        x[base + QPS] = qps
        x[base + COUNT] = counts.get(svc, 1)
    return x


# High-load qps on the bottleneck so replicas/capacity actually matter.
_HIGH_QPS = 600.0


@pytest.mark.xfail(reason="inherited RF is replica-blind; gate passes after Fase 2 retrain")
def test_monotonic_in_capacity():
    """P(SLO_OK) must not DECREASE as the bottleneck gains real capacity."""
    model = _load_model()
    probs = []
    for k in range(1, 9):  # 1..8 replicas, fully ready (eff_cap == count)
        x = _vector({BOTTLENECK: float(k)}, {BOTTLENECK: k}, _HIGH_QPS)
        probs.append(_p_slo_ok(model, x))
    # Allow tiny numerical wobble but no real reversal.
    for a, b in zip(probs, probs[1:]):
        assert b >= a - 1e-6, f"P(SLO_OK) decreased as capacity rose: {probs}"
    # And the effect must be material end-to-end, not flat.
    assert probs[-1] - probs[0] > NOISE_FLOOR, f"capacity barely moves P(SLO_OK): {probs}"


@pytest.mark.xfail(reason="inherited RF ignores the k_eff slot; gate passes after Fase 2 retrain")
def test_sensitive_to_keff():
    """At a FIXED nominal count, lower effective capacity (pods still warming)
    must lower P(SLO_OK) — that is exactly what keff models (Cap_3 eq:keff)."""
    model = _load_model()
    count = 4
    p_warming = _p_slo_ok(model, _vector({BOTTLENECK: 1.0}, {BOTTLENECK: count}, _HIGH_QPS))
    p_warm = _p_slo_ok(model, _vector({BOTTLENECK: float(count)}, {BOTTLENECK: count}, _HIGH_QPS))
    assert p_warm - p_warming > NOISE_FLOOR, (
        f"k_eff does not move the prediction: warming={p_warming:.3f} vs warm={p_warm:.3f}"
    )


@pytest.mark.xfail(reason="grouped-by-run/seed split lands with the Fase 2 training rewrite")
def test_training_split_is_grouped_no_leakage():
    """The retrain must split train/test by run/seed (group), not by row, so the
    synthetic row-expansion does not leak near-duplicates across the split.

    Encoded as a gate now; the grouped split is implemented in Fase 2 alongside the
    new replica-sweep dataset. Until then this asserts the helper exists.
    """
    pytest.importorskip("sklearn")
    from sklearn.model_selection import GroupShuffleSplit  # noqa: F401
    # Fase 2: RandomForestClassify must expose a grouped split keyed on run/seed.
    import importlib
    mod = importlib.import_module("simulation.RandomForestClassify")
    assert hasattr(mod, "grouped_train_test_split"), (
        "Fase 2: add grouped_train_test_split(X, y, groups) to RandomForestClassify"
    )
