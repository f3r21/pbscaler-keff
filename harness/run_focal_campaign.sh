#!/usr/bin/env bash
# run_focal_campaign.sh — Campaña FOCAL vanilla+keff (12 runs) con teardown garantizado.
#
# Ablación 2-vías limpia: PBScaler (vanilla) y PBScaler-keff corren JUNTOS en el mismo
# cluster fresco (sin el confound cross-cluster), escribiendo a results/focal-vk-2026-06/
# sin tocar las 24 celdas originales en results/sprint-A/.
#
# Hace, en orden:
#   1. SMOKE-GATE — PBScaler step 180s. Valida EN VIVO el fix del Step 11 (0af1f28):
#      el kill del `python3 main.py` no debe colgar. Si cuelga o el pipeline queda
#      incompleto → ABORTA sin lanzar el sweep pago.
#   2. SWEEP FOCAL — 2 ctrl × {step,bursty} × N=3 = 12 runs → results/focal-vk-2026-06/.
#   3. TEARDOWN — SIEMPRE al salir (trap EXIT), aun si algo falla o se interrumpe.
#      El idle del cluster es la fuga de costo histórica; el trap la cierra.
#
# Precondición (el operador la cumple ANTES de correr esto):
#   - gke.env tiene PROJECT_ID seteado.
#   - El cluster YA está up: `bash harness/setup_gke.sh` corrió y se verificó salud
#     (nodos Ready, pods de online-boutique Running, Prometheus up, Istio inyectando).
#
# Uso:
#   bash harness/run_focal_campaign.sh             # smoke + sweep + teardown
#   bash harness/run_focal_campaign.sh --dry-run   # solo imprime la matriz, NO gasta, NO teardown
#   SKIP_SMOKE=1   bash harness/run_focal_campaign.sh   # salta el smoke (no recomendado)
#   NO_TEARDOWN=1  bash harness/run_focal_campaign.sh   # NO destruye el cluster al salir
#   SMOKE_TIMEOUT=600 bash harness/run_focal_campaign.sh  # s para detectar hang (default 600)
#
# Overnight (laptop despierta): nohup bash harness/run_focal_campaign.sh > /tmp/focal.log 2>&1 &
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Matriz parametrizable por env (defaults = campaña focal, backward-compat). Para el
# regime-match LITE: CAMPAIGN_SUBDIR=regime-lite-2026-06 CAMPAIGN_CONTROLLERS="PBScaler
# PBScaler-keff NaiveTemporalGate KHPA" CAMPAIGN_SEEDS="1 2" bash harness/run_focal_campaign.sh
SUBDIR="${CAMPAIGN_SUBDIR:-focal-vk-2026-06}"
CONTROLLERS="${CAMPAIGN_CONTROLLERS:-PBScaler PBScaler-keff}"
WORKLOADS="${CAMPAIGN_WORKLOADS:-step:1800 bursty:1800}"
SEEDS="${CAMPAIGN_SEEDS:-1 2 3}"
SMOKE_SUBDIR="smoke-throwaway"
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-600}"   # s; un hang en Step 11 supera esto holgadamente (~6 min reales)

# ── teardown garantizado ─────────────────────────────────────────────────────
teardown() {
    local code=$?
    if [[ "${NO_TEARDOWN:-0}" == "1" ]]; then
        echo ""
        echo ">>> NO_TEARDOWN=1 — cluster NO destruido. Recuerda: bash harness/teardown_gke.sh"
        return
    fi
    echo ""
    echo ">>> Teardown del cluster (trap EXIT, código=${code})…"
    echo y | bash "${SCRIPT_DIR}/teardown_gke.sh" \
        || echo "WARN: teardown reportó error — verifica con 'gcloud container clusters list'"
}
trap teardown EXIT

# ── dry-run: solo imprime la matriz, no toca el cluster ──────────────────────
if [[ "${1:-}" == "--dry-run" ]]; then
    trap - EXIT
    RESULTS_SUBDIR="${SUBDIR}" bash "${SCRIPT_DIR}/run_sweep.sh" --dry-run \
        --controllers ${CONTROLLERS} \
        --workloads ${WORKLOADS} \
        --seeds ${SEEDS}
    exit 0
fi

# ── timeout portable (macOS no trae GNU `timeout`) ───────────────────────────
run_with_timeout() {
    local secs="$1"; shift
    "$@" &
    local pid=$!
    local waited=0
    while kill -0 "${pid}" 2>/dev/null; do
        if (( waited >= secs )); then
            echo "TIMEOUT (${secs}s) — matando el smoke (probable hang en Step 11)" >&2
            kill -9 "${pid}" 2>/dev/null || true
            pkill -9 -f "python3 main.py" 2>/dev/null || true
            return 124
        fi
        sleep 5
        waited=$(( waited + 5 ))
    done
    wait "${pid}"
}

# ── 1. smoke-gate del fix 0af1f28 ────────────────────────────────────────────
if [[ "${SKIP_SMOKE:-0}" != "1" ]]; then
    echo ">>> Smoke-gate: PBScaler step 180s (valida el kill del Step 11 / 0af1f28)…"
    SMOKE_CELL="${CODE_ROOT}/results/${SMOKE_SUBDIR}/online-boutique/step/PBScaler/seed8"
    rm -rf "${SMOKE_CELL}"
    export RESULTS_SUBDIR="${SMOKE_SUBDIR}"
    if ! run_with_timeout "${SMOKE_TIMEOUT}" \
            bash "${SCRIPT_DIR}/run_one_ob.sh" PBScaler step 8 180; then
        echo "ABORT: el smoke falló o colgó — NO se lanza el sweep pago. Revisa Step 11." >&2
        exit 1
    fi
    if [[ ! -f "${SMOKE_CELL}/failure_modes_summary.csv" ]]; then
        echo "ABORT: smoke sin failure_modes_summary.csv — pipeline incompleto." >&2
        exit 1
    fi
    if pgrep -f "python3 main.py" >/dev/null; then
        echo "ABORT: quedó un 'python3 main.py' huérfano tras el smoke — el kill no funcionó." >&2
        pkill -9 -f "python3 main.py" 2>/dev/null || true
        exit 1
    fi
    echo ">>> Smoke OK: sin hang, summary generado, sin procesos huérfanos."
else
    echo ">>> SKIP_SMOKE=1 — saltando el smoke-gate (no recomendado)."
fi

# ── 2. sweep (controladores/workloads/seeds parametrizables) ─────────────────
echo ">>> Sweep: [${CONTROLLERS}] × [${WORKLOADS}] × seeds [${SEEDS}] → results/${SUBDIR}/"
export RESULTS_SUBDIR="${SUBDIR}"
bash "${SCRIPT_DIR}/run_sweep.sh" \
    --controllers ${CONTROLLERS} \
    --workloads ${WORKLOADS} \
    --seeds ${SEEDS}
SWEEP_CODE=$?

echo ">>> Sweep terminó (código=${SWEEP_CODE}). El trap EXIT hará el teardown."
exit "${SWEEP_CODE}"
