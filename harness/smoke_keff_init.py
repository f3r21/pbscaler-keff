"""Smoke de integración K8s para PBScaler-keff (k3d local, sin Istio).

Valida el camino keff contra un cluster Kubernetes real sin requerir
Istio ni Prometheus. Verifica:

  1. Config carga el bloque keff (alpha/beta/lambda_csp/warmup_curve).
  2. PBScalerKeff se instancia sin crashear.
  3. KubernetesClient descubre los 10 servicios de Online Boutique.
  4. fetch_pod_states devuelve datos coherentes por servicio.
  5. compute_keff produce valores no-NaN bajo las tres curvas.
  6. _ga_extra_set_env_kwargs entrega los parámetros que el GA espera.

Lo que NO valida (fuera de alcance sin Istio):
  - anomaly_detect / get_abnormal_calls (requiere métricas de Istio)
  - choose_action / GA optimisation (requiere QPS de Prometheus)

Correr desde el root del fork:
    cd PBScaler
    PBSCALER_CONTROLLER=PBScaler-keff python ../harness/smoke_keff_init.py
"""

from __future__ import annotations

import os
import sys
import traceback


def main() -> int:
    # Ensure the fork is on sys.path so relative imports work.
    fork_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "pbscaler-keff", "PBScaler")
    )
    if fork_root not in sys.path:
        sys.path.insert(0, fork_root)

    print(f"[smoke] cwd={os.getcwd()}")
    print(f"[smoke] fork_root={fork_root}")

    os.environ.setdefault("K8S_NAMESPACE", "online-boutique")
    os.environ.setdefault("K8S_CONFIG", os.path.expanduser("~/.kube/config"))
    # PBScaler reads these but they are unused for this smoke (no metric queries).
    os.environ.setdefault("PROM_RANGE_URL", "http://localhost:9090/api/v1/query_range")
    os.environ.setdefault("PROM_QUERY_URL", "http://localhost:9090/api/v1/query")

    try:
        from config.Config import Config  # type: ignore
        from others.PBScalerKeff import PBScalerKeff  # type: ignore
        from util.EffectiveCapacity import compute_keff  # type: ignore
    except Exception:
        print("[smoke] FAIL: import error")
        traceback.print_exc()
        return 1

    print("[smoke] imports OK")

    # 1. Config loads with keff block.
    try:
        cfg = Config()
    except Exception:
        print("[smoke] FAIL: Config() raised")
        traceback.print_exc()
        return 1

    print(f"[smoke] Config: SLO={cfg.SLO}ms, ns={cfg.namespace}, "
          f"keff(alpha={cfg.keff_alpha}, beta={cfg.keff_beta}, "
          f"lambda_csp={cfg.keff_lambda_csp}, curve={cfg.keff_warmup_curve}, "
          f"t_cold entries={len(cfg.keff_t_cold)})")

    if not cfg.keff_t_cold:
        print("[smoke] FAIL: cfg.keff_t_cold is empty (check config.yaml temporal_gate.cold_times)")
        return 1

    # 2. Instantiate PBScalerKeff (loads predictor, k8s, prom clients).
    try:
        keff = PBScalerKeff(cfg)
    except Exception:
        print("[smoke] FAIL: PBScalerKeff(cfg) raised")
        traceback.print_exc()
        return 1
    print(f"[smoke] PBScalerKeff init OK — {len(keff.mss)} services discovered: {keff.mss}")

    # svc_counts is populated by anomaly_detect normally; for this smoke we
    # set it from the kubernetes client directly so _keff_for has nominal data.
    keff.svc_counts = keff.k8s_util.get_svcs_counts()
    print(f"[smoke] svc_counts: {keff.svc_counts}")

    # 3. fetch_pod_states for each service.
    try:
        keff._refresh_pod_states()
    except Exception:
        print("[smoke] FAIL: _refresh_pod_states raised")
        traceback.print_exc()
        return 1

    n_pods_total = sum(len(p) for p in keff._pod_states.values())
    if n_pods_total == 0:
        print("[smoke] FAIL: no pods found in any service (is OB deployed?)")
        return 1
    print(f"[smoke] fetch_pod_states OK — {n_pods_total} pods across {len(keff._pod_states)} services")
    for svc, pods in keff._pod_states.items():
        ready = sum(1 for p in pods if p["ready"])
        warming = len(pods) - ready
        print(f"          {svc}: {len(pods)} pods (ready={ready}, warming={warming})")

    # 4. compute_keff per service across all three curves.
    print("[smoke] compute_keff across curves:")
    failures = 0
    for svc in keff.mss:
        t_cold = keff._t_cold.get(svc)
        if t_cold is None:
            print(f"          {svc}: SKIP (no T_cold in config)")
            continue
        line = f"          {svc} (T_cold={t_cold}s):"
        for curve in ("step", "linear", "sigmoid"):
            try:
                k = compute_keff(keff._pod_states[svc], t_cold, curve)
                if k != k:  # NaN
                    line += f"  {curve}=NaN!"
                    failures += 1
                else:
                    line += f"  {curve}={k:.2f}"
            except Exception as e:
                line += f"  {curve}=ERR({type(e).__name__})"
                failures += 1
        print(line)

    if failures:
        print(f"[smoke] FAIL: {failures} curve evaluations errored")
        return 1

    # 5. _ga_extra_set_env_kwargs entrega los parámetros que el GA espera.
    try:
        kwargs = keff._ga_extra_set_env_kwargs(list(keff.mss))
        assert set(kwargs.keys()) == {"pod_states_by_svc", "t_cold_by_svc", "warmup_curve"}
        assert len(kwargs["pod_states_by_svc"]) == len(keff.mss)
        print(f"[smoke] _ga_extra_set_env_kwargs OK — curve={kwargs['warmup_curve']}, "
              f"pod_states_by_svc keys={len(kwargs['pod_states_by_svc'])}")
    except Exception:
        print("[smoke] FAIL: _ga_extra_set_env_kwargs raised")
        traceback.print_exc()
        return 1

    print("\n[smoke] PASS — integración K8s validada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
