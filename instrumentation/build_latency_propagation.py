"""Tabla suplementaria de PROPAGACIÓN DE LATENCIA para Cap_4 (disclosure honesta).

Compara las cuatro capas de latencia candidatas a "el SLO de 500 ms", que dan
veredictos distintos sobre si k_eff mejora la experiencia:

  1. checkout-Istio   — p95 del servicio checkoutservice (server-side). MÉTRICA
                        PRIMARIA del SLO (Cap_3:238). k_eff gana.
  2. frontend-Istio   — p95 del servicio frontend (server-side, entrada usuario).
  3. /cart/checkout   — p95 client-side de la transacción de checkout (Locust).
  4. aggregate-client — p95 client-side de TODOS los endpoints (Locust).

La métrica primaria es (1); (2)-(4) se reportan para declarar honestamente que
la mejora server-side de k_eff no se propaga del todo al cliente — el cuello real
bajo carga está en el browse-path + la co-carga loadgenerator+Locust.

Uso:
    python build_latency_propagation.py \\
        --results-root results/sprint-A/online-boutique \\
        --aggregate results/sprint-A/aggregate.csv \\
        --out results/sprint-A/latency_propagation.md
"""

from __future__ import annotations

import argparse
import glob
import re
import statistics as st
import sys
from pathlib import Path

import pandas as pd

CTRL = {"PBScaler": "vanilla", "PBScaler-keff": "keff",
        "NaiveTemporalGate": "naive", "KHPA": "khpa"}
CTRL_ORDER = ["vanilla", "keff", "naive", "khpa"]
WL_ORDER = ["step", "bursty"]


def _client_p95(cell: str, name_substr: str | None) -> float:
    """p95 client-side de Locust: una fila por endpoint, o 'Aggregated' si name_substr=None."""
    try:
        d = pd.read_csv(f"{cell}/locust_stats.csv")
    except OSError:
        return float("nan")
    if name_substr is None:
        sel = d[d["Name"] == "Aggregated"]
    else:
        sel = d[d["Name"].astype(str).str.contains(name_substr, case=False, na=False)]
    return float(sel.iloc[0]["95%"]) if len(sel) else float("nan")


def _stat(vals: list[float]) -> tuple[float, float, float]:
    v = [x for x in vals if x == x]
    return (st.median(v), min(v), max(v)) if v else (float("nan"),) * 3


def collect(results_root: str, agg: pd.DataFrame) -> dict:
    """Cada capa se resume independientemente (mediana[mín,máx] sobre N=3); no
    requiere emparejar seed-a-seed entre Istio (del aggregate) y cliente (de las
    celdas Locust). Las capas Istio salen del aggregate por servicio; las cliente
    de los locust_stats.csv de cada celda."""
    rt_of = {label: runtime for runtime, label in CTRL.items()}
    rows: dict = {}
    for wl in WL_ORDER:
        for ctrl in CTRL_ORDER:
            base = agg[(agg.controller == ctrl) & (agg.workload == wl)]
            ck_istio = base[base.service == "checkoutservice"]["p95_latency_mean"].tolist()
            fe_istio = base[base.service == "frontend"]["p95_latency_mean"].tolist()
            ck_client, agg_client = [], []
            for cell in glob.glob(f"{results_root}/{wl}/{rt_of[ctrl]}/seed*"):
                ck_client.append(_client_p95(cell, "/cart/checkout"))
                agg_client.append(_client_p95(cell, None))
            if ck_istio or ck_client:
                rows[(wl, ctrl)] = {"ck_istio": ck_istio, "fe_istio": fe_istio,
                                    "ck_client": ck_client, "agg_client": agg_client}
    return rows


def to_markdown(rows: dict) -> str:
    out = [
        "# Propagación de latencia — cuatro capas (p95, ms, mediana [mín, máx] sobre N=3)",
        "",
        "SLO = 500 ms. La métrica PRIMARIA es **checkout-Istio** (Cap_3:238). Las demás",
        "capas se reportan para declarar honestamente hasta dónde llega la mejora de k_eff.",
        "",
        "| Workload | Controller | checkout-Istio | frontend-Istio | /cart/checkout (cliente) | aggregate (cliente) |",
        "|---|---|---|---|---|---|",
    ]
    for wl in WL_ORDER:
        for c in CTRL_ORDER:
            d = rows.get((wl, c))
            if not d:
                continue
            def cell(key: str) -> str:
                med, lo, hi = _stat(d[key])
                return "—" if med != med else f"{med:.0f} [{lo:.0f}, {hi:.0f}]"
            out.append(f"| {wl} | {c} | {cell('ck_istio')} | {cell('fe_istio')} "
                       f"| {cell('ck_client')} | {cell('agg_client')} |")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", type=Path, required=True)
    ap.add_argument("--aggregate", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if not args.aggregate.exists():
        print(f"aggregate not found: {args.aggregate}", file=sys.stderr)
        return 1
    agg = pd.read_csv(args.aggregate)
    rows = collect(str(args.results_root), agg)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_markdown(rows))
    print(f"wrote {args.out} ({len(rows)} celdas controller×workload)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
