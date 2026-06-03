#!/usr/bin/env bash
# Driver desatendido de la campaña PRINCIPAL (15 runs).
#
# Corre la matriz principal de la sesión GKE en una sola invocación, pensada
# para lanzarse en background (nohup/screen) sin babysitteo. Es resumable:
# run_sweep.sh salta celdas ya completas, y los re-runs de vanilla escriben a
# dirs con seed fijo (re-ejecutarlos sobreescribe la misma celda, idempotente
# en estructura).
#
# Matriz (15 runs, duración 1800 s):
#   PBScaler-keff      × {step, bursty} × seed {1,2,3}   = 6   (run_sweep)
#   NaiveTemporalGate  × {step, bursty} × seed {1,2,3}   = 6   (run_sweep)
#   PBScaler vanilla   × step           × seed {1,2,3}   = 3   (re-run fresco, D3)
#
# KHPA y vanilla/bursty NO se corren: se toman de una campaña previa en el merge (B3).
#
# Prerrequisitos (sesión GKE, ver plan Fase B):
#   - cluster GKE arriba (setup_gke.sh) con Istio + Prometheus + Online Boutique
#   - branch `completo` del repo consolidado (Niveles 1-3 + anti-SD)
#   - config.yaml con T_cold real ya aplicado (apply_tcold_profile.py)
#   - smoke keff+Istio ya pasó
#
# Uso:
#   nohup bash scripts/run_campaign_principal.sh > /tmp/campaign.log 2>&1 &
#   tail -f /tmp/campaign.log
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DURATION="${DURATION:-1800}"

echo "=============================================================="
echo "  campaña PRINCIPAL — driver desatendido"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ) | duration=${DURATION}s"
echo "=============================================================="

# ── 1. keff + naive × {step, bursty} × N=3 (12 runs, resumable) ──────────────
echo ""
echo "==> [1/2] run_sweep: PBScaler-keff + NaiveTemporalGate × {step,bursty} × seeds 1 2 3"
bash "${SCRIPT_DIR}/run_sweep.sh" \
    --controllers PBScaler-keff NaiveTemporalGate \
    --workloads "step:${DURATION}" "bursty:${DURATION}" \
    --seeds 1 2 3
SWEEP_EXIT=$?
echo "    run_sweep exit: ${SWEEP_EXIT}"

# ── 2. Re-run fresco de vanilla × step × N=3 (3 runs, D3) ────────────────────
# Reemplaza los reps de una campaña previa (1 descartado por crash de pagerank). Se corre
# con el HEAD actual (post-rebase); el diff vanilla es solo robustez, no toca GA.
echo ""
echo "==> [2/2] re-run vanilla × step × seeds 1 2 3 (fresco, D3)"
declare -a VANILLA_OK=()
declare -a VANILLA_FAIL=()
for REP in 1 2 3; do
    echo "  -- vanilla/step/seed${REP}"
    if bash "${SCRIPT_DIR}/run_one_ob.sh" PBScaler step "${REP}" "${DURATION}"; then
        VANILLA_OK+=("seed${REP}")
    else
        VANILLA_FAIL+=("seed${REP}")
        echo "  WARNING: vanilla/step/seed${REP} no completó limpio — continúa"
        pkill -f measure_phantom_capacity.py 2>/dev/null || true
        pkill -f "kubectl port-forward" 2>/dev/null || true
        sleep 5
    fi
done

echo ""
echo "=============================================================="
echo "  campaña PRINCIPAL — fin. $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=============================================================="
echo "  vanilla re-run OK:   ${VANILLA_OK[*]:-(ninguno)}"
echo "  vanilla re-run FAIL: ${VANILLA_FAIL[*]:-(ninguno)}"
echo ""
echo "  Verificá las 15 celdas antes del teardown:"
echo "    find results/sprint-A -name failure_modes_summary.csv | wc -l   # esperado >= 15"
echo "  Luego: bash harness/teardown_gke.sh"
