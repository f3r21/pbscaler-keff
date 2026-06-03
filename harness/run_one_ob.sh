#!/usr/bin/env bash
# Run one Online Boutique experiment under a chosen controller and stash
# outputs under results/sprint-A/online-boutique/<workload>/<controller>/seed<rep>/.
#
# Differs from pbscaler-keff/PBScaler/scripts/run_one_ob_run.sh (el script viejo del fork)
# in two ways: it accepts a controller name as the first argument, and it
# writes to the sprint-A directory tree expected by the analysis pipeline.
#
# Caller responsibilities:
#   - GKE cluster `pbscaler-experiment` is already up (setup_gke.sh ran)
#   - kubectl + locust + python3 in PATH
#
# Args:
#   $1 = controller (PBScaler | PBScaler-keff | NaiveTemporalGate | KHPA)
#   $2 = workload   (step | bursty)
#   $3 = rep        (1 | 2 | 3)
#   $4 = duration   (seconds)
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 <controller> <workload> <rep> <duration_s>" >&2
    exit 1
fi

CONTROLLER="$1"
WORKLOAD="$2"
REP="$3"
DURATION="$4"
SEED=$((42 + REP * 100))

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Repo consolidado: el runner baseline vive al lado, en harness/ (antes era el del fork).
BASELINE_RUNNER="${SCRIPT_DIR}/run_pbscaler_baseline.sh"
BENCH_DIR="${CODE_ROOT}/benchmarks"

LOCUSTFILE_ABS="${BENCH_DIR}/online_boutique/locustfile_${WORKLOAD}.py"
PHANTOM_SCRIPT="${CODE_ROOT}/instrumentation/measure_phantom_capacity.py"
# RESULTS_SUBDIR aísla campañas en subárboles distintos (default: sprint-A, los 24
# originales). Una campaña fresca (p.ej. focal-vk-2026-06) escribe a results/<subdir>/
# sin colisionar ni hacer no-op contra celdas ya completas de otra campaña.
RESULTS_SUBDIR="${RESULTS_SUBDIR:-sprint-A}"
OUT_DIR="${CODE_ROOT}/results/${RESULTS_SUBDIR}/online-boutique/${WORKLOAD}/${CONTROLLER}/seed${REP}"

if [[ ! -f "${LOCUSTFILE_ABS}" ]]; then
    echo "ERROR: locustfile not found: ${LOCUSTFILE_ABS}" >&2
    exit 1
fi

# KHPA uses K8s HPA objects; the other three controllers use PBScaler's
# in-process loop, all reached via main.py with PBSCALER_CONTROLLER.
case "${CONTROLLER}" in
    PBScaler)          CONTROLLER_LABEL="vanilla" ;;
    PBScaler-keff)     CONTROLLER_LABEL="keff" ;;
    NaiveTemporalGate) CONTROLLER_LABEL="naive" ;;
    KHPA)              CONTROLLER_LABEL="khpa" ;;
    *)
        echo "ERROR: unknown controller '${CONTROLLER}'" >&2
        echo "expected: PBScaler | PBScaler-keff | NaiveTemporalGate | KHPA" >&2
        exit 1
        ;;
esac
# CONTROLLER_LABEL is the canonical analysis vocabulary (vanilla/keff/naive/khpa)
# written to metadata.json's controller field and consumed by
# compute_failure_mode_summary.py → aggregate_results.py. PBSCALER_CONTROLLER
# keeps the runtime name for main.py.

mkdir -p "${OUT_DIR}"
echo "==> run_one_ob: controller=${CONTROLLER} workload=${WORKLOAD} rep=${REP} duration=${DURATION}s seed=${SEED}"
echo "    out_dir=${OUT_DIR}"

# Phantom capacity instrumentation, runs slightly past the load window.
PHANTOM_DURATION=$((DURATION + 120))
PHANTOM_PID=""
if [[ -f "${PHANTOM_SCRIPT}" ]]; then
    python3 "${PHANTOM_SCRIPT}" \
        --namespace online-boutique \
        --duration "${PHANTOM_DURATION}" \
        --interval 5 \
        --out "${OUT_DIR}/phantom_capacity.csv" &
    PHANTOM_PID=$!
    echo "    phantom_capacity PID: ${PHANTOM_PID} (${PHANTOM_DURATION}s)"
else
    echo "    (skipping phantom_capacity — script not found)"
fi

# Watchdog: locust 2.43 sometimes hangs after LoadShape returns None.
WATCHDOG_AFTER=$((DURATION + 120))
(
    sleep "${WATCHDOG_AFTER}"
    if pgrep -f "locust.*locustfile_${WORKLOAD}" >/dev/null; then
        echo "[watchdog] killing locust after ${WATCHDOG_AFTER}s grace" >&2
        pkill -f "locust.*locustfile_${WORKLOAD}" 2>/dev/null || true
    fi
) &
WATCHDOG_PID=$!

# Drive the local run_pbscaler_baseline.sh (harness/). PBSCALER_CONTROLLER
# selects the controller in main.py; LOCUSTFILE/LOCUST_RUN_TIME/LOCUST_SEED
# parametrise the load generator.
RUN_EXIT=0
PBSCALER_CONTROLLER="${CONTROLLER}" \
LOCUSTFILE="${LOCUSTFILE_ABS}" \
LOCUST_SEED="${SEED}" \
LOCUST_RUN_TIME="${DURATION}s" \
    bash "${BASELINE_RUNNER}" || RUN_EXIT=$?
if [[ ${RUN_EXIT} -ne 0 ]]; then
    echo "WARNING: run_pbscaler_baseline.sh exited ${RUN_EXIT} — recovering data products" >&2
fi

if [[ -n "${PHANTOM_PID}" ]]; then
    kill "${PHANTOM_PID}" 2>/dev/null || true
    wait "${PHANTOM_PID}" 2>/dev/null || true
fi

# Move the runner's per-run output into the sprint-A tree.
SRC="${CODE_ROOT}/results/pbscaler_baseline"
if [[ -d "${SRC}" ]]; then
    mv "${SRC}"/* "${OUT_DIR}/" 2>/dev/null || true
    rmdir "${SRC}" 2>/dev/null || true
fi

GIT_SHA="$(git -C "${CODE_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)"
# Write metadata BEFORE deriving failure modes — compute_failure_mode_summary.py
# reads controller/benchmark/workload/rep/seed from this file. controller uses
# the canonical label; controller_runtime keeps the PBSCALER_CONTROLLER name.
cat > "${OUT_DIR}/metadata.json" <<EOF
{
  "benchmark": "online-boutique",
  "workload": "${WORKLOAD}",
  "controller": "${CONTROLLER_LABEL}",
  "controller_runtime": "${CONTROLLER}",
  "rep": ${REP},
  "seed": ${SEED},
  "duration_s": ${DURATION},
  "git_sha": "${GIT_SHA}",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "slo_ms": 500,
  "run_exit_code": ${RUN_EXIT}
}
EOF

# ── Derivación failure-mode in-situ (C1) ─────────────────────────────────────
# aggregate_results.py consume failure_modes_summary.csv; este paso lo genera
# por celda. detect_* producen double_scaling.csv + instances.ping_pong.csv;
# compute_failure_mode_summary los une con las métricas + metadata.json.
INSTR_DIR="${CODE_ROOT}/instrumentation"
if [[ -f "${OUT_DIR}/instances.csv" && -f "${OUT_DIR}/phantom_capacity.csv" ]]; then
    python3 "${INSTR_DIR}/detect_double_scaling.py" \
        "${OUT_DIR}/instances.csv" "${OUT_DIR}/phantom_capacity.csv" \
        --out "${OUT_DIR}/double_scaling.csv" || echo "WARN: detect_double_scaling falló" >&2
    python3 "${INSTR_DIR}/detect_ping_pong.py" \
        "${OUT_DIR}/instances.csv" || echo "WARN: detect_ping_pong falló" >&2
    python3 "${INSTR_DIR}/compute_failure_mode_summary.py" \
        "${OUT_DIR}" --out "${OUT_DIR}/failure_modes_summary.csv" \
        || echo "WARN: compute_failure_mode_summary falló" >&2
    if [[ -f "${OUT_DIR}/failure_modes_summary.csv" ]]; then
        echo "    failure_modes_summary.csv OK ($(wc -l < "${OUT_DIR}/failure_modes_summary.csv" | tr -d ' ') líneas)"
    else
        echo "ERROR: failure_modes_summary.csv NO generado para ${OUT_DIR}" >&2
    fi
else
    echo "ERROR: faltan instances.csv/phantom_capacity.csv — no se puede derivar failure modes" >&2
fi

echo ""
echo "==> Run ${REP} of ${CONTROLLER} on ${WORKLOAD} complete."
echo "    out_dir=${OUT_DIR}"
echo "    files:"
ls -1 "${OUT_DIR}" | sed 's/^/      /'
