#!/usr/bin/env bash
# Batch experimental driver: itera el producto cartesiano de controladores,
# patrones de carga, y semillas, invocando run_one_ob.sh por celda.
#
# Las celdas fallidas se loguean y el batch continúa para que un run
# defectuoso no aborte la campaña. Los runs ya completos (metadata.json +
# artefacto de resultados presentes) se saltean para permitir resumir.
#
# Caller responsibilities:
#   - GKE cluster ready (setup_gke.sh from the fork ran)
#   - kubectl, locust, python3 in PATH
#
# Optional flags:
#   --dry-run         print the matrix without executing
#   --controllers ... override controller list (space-separated)
#   --workloads ...   override workload list (space-separated)
#   --seeds ...       override seed list (space-separated)
# -u omitted: bash 3.2 trips on empty array expansions like ${SUMMARY_SKIP[@]}
# even with [[ ${#arr[@]} -gt 0 ]] guards.
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults per Cap_3 sec:diseno_exp.
CONTROLLERS=("PBScaler" "PBScaler-keff" "NaiveTemporalGate" "KHPA")
WORKLOADS=("step:660" "bursty:660")
SEEDS=(1 2 3)
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --controllers) shift; CONTROLLERS=(); while [[ $# -gt 0 && "$1" != --* ]]; do CONTROLLERS+=("$1"); shift; done ;;
        --workloads) shift; WORKLOADS=(); while [[ $# -gt 0 && "$1" != --* ]]; do WORKLOADS+=("$1"); shift; done ;;
        --seeds) shift; SEEDS=(); while [[ $# -gt 0 && "$1" != --* ]]; do SEEDS+=("$1"); shift; done ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *) echo "unknown flag: $1" >&2; exit 1 ;;
    esac
done

TOTAL=$(( ${#CONTROLLERS[@]} * ${#WORKLOADS[@]} * ${#SEEDS[@]} ))
echo "=============================================================="
echo "  Sweep: ${TOTAL} runs (${#CONTROLLERS[@]} ctrl x ${#WORKLOADS[@]} wl x ${#SEEDS[@]} seeds)"
echo "  controllers: ${CONTROLLERS[*]}"
echo "  workloads:   ${WORKLOADS[*]}"
echo "  seeds:       ${SEEDS[*]}"
echo "  dry_run:     ${DRY_RUN}"
echo "=============================================================="

CODE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

declare -a SUMMARY_OK=()
declare -a SUMMARY_FAIL=()
declare -a SUMMARY_SKIP=()

INDEX=0
for ctrl in "${CONTROLLERS[@]}"; do
    for wl_entry in "${WORKLOADS[@]}"; do
        WL="${wl_entry%%:*}"
        DUR="${wl_entry##*:}"
        for seed in "${SEEDS[@]}"; do
            INDEX=$((INDEX + 1))
            CELL="${ctrl}/${WL}/seed${seed}"
            OUT_DIR="${CODE_ROOT}/results/online-boutique/${WL}/${ctrl}/seed${seed}"

            if [[ -f "${OUT_DIR}/metadata.json" && -f "${OUT_DIR}/instances.csv" ]]; then
                echo "==> [${INDEX}/${TOTAL}] SKIP ${CELL} (already complete)"
                SUMMARY_SKIP+=("${CELL}")
                continue
            fi

            echo ""
            echo "##############################################################"
            echo "##  [${INDEX}/${TOTAL}] ${CELL} (duration ${DUR}s)"
            echo "##  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
            echo "##############################################################"

            if [[ ${DRY_RUN} -eq 1 ]]; then
                echo "    (dry-run) would invoke run_one_ob.sh ${ctrl} ${WL} ${seed} ${DUR}"
                SUMMARY_OK+=("${CELL} (dry-run)")
                continue
            fi

            if bash "${SCRIPT_DIR}/run_one_ob.sh" "${ctrl}" "${WL}" "${seed}" "${DUR}"; then
                SUMMARY_OK+=("${CELL}")
            else
                SUMMARY_FAIL+=("${CELL}")
                echo "WARNING: ${CELL} did not complete cleanly — continuing"
                # Defensive cleanup of stale background processes.
                pkill -f measure_phantom_capacity.py 2>/dev/null || true
                pkill -f "kubectl port-forward" 2>/dev/null || true
                sleep 5
            fi
        done
    done
done

echo ""
echo "=============================================================="
echo "  Sweep complete. $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=============================================================="
echo "  OK   (${#SUMMARY_OK[@]}):"
for r in "${SUMMARY_OK[@]}"; do echo "    $r"; done
echo "  SKIP (${#SUMMARY_SKIP[@]}):"
for r in "${SUMMARY_SKIP[@]}"; do echo "    $r"; done
echo "  FAIL (${#SUMMARY_FAIL[@]}):"
for r in "${SUMMARY_FAIL[@]}"; do echo "    $r"; done

# Exit nonzero only if no run succeeded — partial completion is acceptable
# for batch campaigns (analysis can proceed with whatever landed).
[[ ${#SUMMARY_OK[@]} -gt 0 || ${#SUMMARY_SKIP[@]} -gt 0 ]]
