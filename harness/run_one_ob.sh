#!/usr/bin/env bash
# Run one Online Boutique experiment under a chosen controller and stash
# outputs under results/online-boutique/<workload>/<controller>/seed<rep>/.
#
# Parametrised wrapper: accepts the controller name as the first argument
# so the batch driver can vary it without source edits.
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
FORK_DIR="${CODE_ROOT}/pbscaler-keff/PBScaler"
BENCH_DIR="${CODE_ROOT}/benchmarks"

LOCUSTFILE_ABS="${BENCH_DIR}/online_boutique/locustfile_${WORKLOAD}.py"
PHANTOM_SCRIPT="${CODE_ROOT}/instrumentation/measure_phantom_capacity.py"
OUT_DIR="${CODE_ROOT}/results/online-boutique/${WORKLOAD}/${CONTROLLER}/seed${REP}"

if [[ ! -f "${LOCUSTFILE_ABS}" ]]; then
    echo "ERROR: locustfile not found: ${LOCUSTFILE_ABS}" >&2
    exit 1
fi

# KHPA uses K8s HPA objects; the other three controllers use PBScaler's
# in-process loop, all reached via main.py with PBSCALER_CONTROLLER.
case "${CONTROLLER}" in
    PBScaler|PBScaler-keff|NaiveTemporalGate|KHPA) ;;
    *)
        echo "ERROR: unknown controller '${CONTROLLER}'" >&2
        echo "expected: PBScaler | PBScaler-keff | NaiveTemporalGate | KHPA" >&2
        exit 1
        ;;
esac

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

# Drive the existing run_pbscaler_baseline.sh in the fork. PBSCALER_CONTROLLER
# selects the controller in main.py; LOCUSTFILE/LOCUST_RUN_TIME/LOCUST_SEED
# parametrise the load generator.
RUN_EXIT=0
PBSCALER_CONTROLLER="${CONTROLLER}" \
LOCUSTFILE="${LOCUSTFILE_ABS}" \
LOCUST_SEED="${SEED}" \
LOCUST_RUN_TIME="${DURATION}s" \
    bash "${FORK_DIR}/scripts/run_pbscaler_baseline.sh" || RUN_EXIT=$?
if [[ ${RUN_EXIT} -ne 0 ]]; then
    echo "WARNING: run_pbscaler_baseline.sh exited ${RUN_EXIT} — recovering data products" >&2
fi

if [[ -n "${PHANTOM_PID}" ]]; then
    kill "${PHANTOM_PID}" 2>/dev/null || true
    wait "${PHANTOM_PID}" 2>/dev/null || true
fi

# Move fork's per-run output into the destination tree.
SRC="${FORK_DIR}/results/pbscaler_baseline"
if [[ -d "${SRC}" ]]; then
    mv "${SRC}"/* "${OUT_DIR}/" 2>/dev/null || true
    rmdir "${SRC}" 2>/dev/null || true
fi

GIT_SHA="$(git -C "${FORK_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
cat > "${OUT_DIR}/metadata.json" <<EOF
{
  "benchmark": "online-boutique",
  "workload": "${WORKLOAD}",
  "controller": "${CONTROLLER}",
  "rep": ${REP},
  "seed": ${SEED},
  "duration_s": ${DURATION},
  "git_sha": "${GIT_SHA}",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "slo_ms": 500,
  "run_exit_code": ${RUN_EXIT}
}
EOF

echo ""
echo "==> Run ${REP} of ${CONTROLLER} on ${WORKLOAD} complete."
echo "    out_dir=${OUT_DIR}"
echo "    files:"
ls -1 "${OUT_DIR}" | sed 's/^/      /'
