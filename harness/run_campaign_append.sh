#!/usr/bin/env bash
# Append batch (+9) para COMPLETAR la matriz 24-cell fresca en ESTE cluster
# (K8s 1.35), sin nada prestado de una campaña previa — decisión cross-cluster.
#
# Faltan respecto al campaign principal de 15:
#   PBScaler vanilla × bursty × N=3   (3)  — path conocido-bueno (= vanilla/step)
#   KHPA × {step, bursty} × N=3       (6)  — vía main.py KHPA (wrap de HPA),
#                                            GATEADO por un smoke throwaway primero
#
# Se lanza DESPUÉS de que los 15 terminen (un controlador a la vez en el cluster).
# Resumable: run_sweep salta celdas completas (metadata+instances+failure_modes).
#
# Uso:
#   nohup bash scripts/run_campaign_append.sh > /tmp/append_a.log 2>&1 &
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DURATION="${DURATION:-1800}"

echo "=============================================================="
echo "  campaña APPEND (+9) — completar matriz 24-cell"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ) | duration=${DURATION}s"
echo "=============================================================="

# ── [1/3] vanilla × bursty × N=3 (path conocido-bueno) ───────────────────────
echo ""
echo "==> [1/3] vanilla/bursty × seeds 1 2 3"
bash "${SCRIPT_DIR}/run_sweep.sh" \
    --controllers PBScaler --workloads "bursty:${DURATION}" --seeds 1 2 3

# ── [2/3] KHPA smoke gate (throwaway 180s, seed 8) ───────────────────────────
echo ""
echo "==> [2/3] KHPA smoke gate (validar wrap de HPA produce summary con controller=khpa)"
SMOKE="${CODE_ROOT}/results/sprint-A/online-boutique/step/KHPA/seed8"
rm -rf "${SMOKE}"
KHPA_OK=0
if bash "${SCRIPT_DIR}/run_one_ob.sh" KHPA step 8 180; then
    if [[ -f "${SMOKE}/failure_modes_summary.csv" ]] \
       && grep -q "khpa" "${SMOKE}/failure_modes_summary.csv"; then
        echo "    KHPA smoke OK (failure_modes_summary.csv con controller=khpa)"
        KHPA_OK=1
    else
        echo "    KHPA smoke: corrió pero SIN summary/controller=khpa — REVISAR antes de los 6" >&2
    fi
else
    echo "    KHPA smoke FALLÓ — NO se corren los 6 khpa (depurar manualmente)" >&2
fi
rm -rf "${SMOKE}"
# limpiar cualquier HPA dejado por el smoke antes de los runs reales
kubectl delete hpa --all -n online-boutique 2>/dev/null || true

# ── [3/3] KHPA × {step, bursty} × N=3 (solo si el smoke pasó) ─────────────────
echo ""
if [[ "${KHPA_OK}" = "1" ]]; then
    echo "==> [3/3] KHPA step+bursty × seeds 1 2 3"
    bash "${SCRIPT_DIR}/run_sweep.sh" \
        --controllers KHPA --workloads "step:${DURATION}" "bursty:${DURATION}" --seeds 1 2 3
else
    echo "==> [3/3] KHPA SALTADO (smoke no validó) — revisar autoscaler/baselines/KHPA.py + run_one_ob KHPA"
fi

echo ""
echo "=============================================================="
echo "  APPEND fin. $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  celdas con failure_modes_summary.csv (esperado 24):"
find "${CODE_ROOT}/results/sprint-A" -name failure_modes_summary.csv 2>/dev/null | wc -l
echo "=============================================================="
touch /tmp/append_a.DONE
