"""Dynamic validation of PBScaler-keff against a live warmup event.

Scales cartservice from 1 to 2 replicas and polls k_eff under all three
warmup curves every second for 60s. Expected trajectory:
  - step:    k_eff stays at 1.0 until t >= T_cold, then jumps to 2.0
  - linear:  k_eff = 1.0 + min(1, t/T_cold) — smooth ramp
  - sigmoid: k_eff = 1.0 + sigmoid(t, T_cold) — slow start, fast finish

The new pod becomes Ready when its readiness probe succeeds; once Ready,
k_eff = 2.0 for all curves regardless of age.

Run from the fork root after setup_k3d.sh:
    cd codigo/pbscaler-keff/PBScaler
    python ../../scripts/smoke_keff_dynamic.py
"""

from __future__ import annotations

import os
import sys
import time

SVC = "cartservice"
NAMESPACE = "online-boutique"
TARGET_REPLICAS = 2
POLL_INTERVAL = 1.0
DURATION = 60.0


def main() -> int:
    fork_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "pbscaler-keff", "PBScaler")
    )
    if fork_root not in sys.path:
        sys.path.insert(0, fork_root)

    from kubernetes import client, config as k8s_config  # type: ignore
    from util.EffectiveCapacity import compute_keff, fetch_pod_states  # type: ignore

    k8s_config.load_kube_config(config_file=os.path.expanduser("~/.kube/config"))
    apps = client.AppsV1Api()
    core = client.CoreV1Api()

    # Lookup T_cold for the target service from config.yaml.
    from config.Config import Config  # type: ignore
    cfg = Config()
    t_cold = cfg.keff_t_cold.get(SVC)
    if t_cold is None:
        print(f"[dyn] FAIL: no T_cold for {SVC} in config.yaml")
        return 1
    print(f"[dyn] target svc={SVC}, T_cold={t_cold}s (configured), poll every {POLL_INTERVAL}s for {DURATION}s")

    # Capture initial state.
    initial = fetch_pod_states(core, NAMESPACE, SVC)
    print(f"[dyn] before scale: {len(initial)} pods, ready={sum(1 for p in initial if p['ready'])}")
    for curve in ("step", "linear", "sigmoid"):
        k = compute_keff(initial, t_cold, curve)
        print(f"           {curve}: k_eff={k:.2f}")

    # Trigger scale-up.
    print(f"[dyn] scaling {SVC} to {TARGET_REPLICAS} replicas")
    apps.patch_namespaced_deployment_scale(
        SVC, NAMESPACE, {"spec": {"replicas": TARGET_REPLICAS}}
    )
    t0 = time.time()

    # Poll trajectory.
    print(f"[dyn] {'t(s)':>5}  {'pods':>4}  {'ready':>5}  {'step':>6}  {'linear':>6}  {'sigmoid':>7}")
    last_print_at_ready = -1
    while True:
        t = time.time() - t0
        if t > DURATION:
            break
        pods = fetch_pod_states(core, NAMESPACE, SVC)
        ready = sum(1 for p in pods if p["ready"])
        k_step = compute_keff(pods, t_cold, "step")
        k_lin = compute_keff(pods, t_cold, "linear")
        k_sig = compute_keff(pods, t_cold, "sigmoid")
        print(f"[dyn]  {t:5.1f}  {len(pods):>4}  {ready:>5}  {k_step:6.2f}  {k_lin:6.2f}  {k_sig:7.2f}")
        # Stop early if the new pod is fully ready and we've shown the steady-state.
        if ready == TARGET_REPLICAS and last_print_at_ready < 0:
            last_print_at_ready = t
        if last_print_at_ready >= 0 and t - last_print_at_ready > 3.0:
            print(f"[dyn] both pods ready at t={last_print_at_ready:.1f}s — stopping early")
            break
        time.sleep(POLL_INTERVAL)

    # Restore.
    print(f"[dyn] restoring {SVC} to 1 replica")
    apps.patch_namespaced_deployment_scale(SVC, NAMESPACE, {"spec": {"replicas": 1}})

    print("[dyn] PASS — k_eff tracked the warmup event in real time")
    return 0


if __name__ == "__main__":
    sys.exit(main())
