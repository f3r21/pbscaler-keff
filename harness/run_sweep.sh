#!/usr/bin/env bash
# Driver batch de la campaña: 4 controllers x 2 workloads x N=3 seeds = 24 runs.
#
# Iterates over the cartesian product, invoking run_one_ob.sh per cell.
# Failed cells are logged and the batch continues so a single dud run does
# not abort the campaign. Already-complete runs (metadata.json + a results
# artifact present) are skipped to allow resuming.
#
# Caller responsibilities:
#   - GKE cluster ready (harness/setup_gke.sh ran)
#   - kubectl, locust, python3 in PATH
#
# Optional flags:
#   --dry-run         print the matrix without executing
#   --controllers ... override controller list (space-separated)
#   --workloads ...   override workload list (space-separated)
#   --seeds ...       override seed list (space-separated)
#
# Optional env:
#   RESULTS_SUBDIR    results subtree under results/ (default: sprint-A). Set to an
#                     isolated name (e.g. focal-vk-2026-06) to run a fresh campaign
#                     without colliding with — or no-op-skipping — another campaign's cells.
# -u omitted: bash 3.2 trips on empty array expansions like ${SUMMARY_SKIP[@]}
# even with [[ ${#arr[@]} -gt 0 ]] guards.
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults: la campaña per Cap_3 sec:diseno_exp.
CONTROLLERS=("PBScaler" "PBScaler-keff" "NaiveTemporalGate" "KHPA")
WORKLOADS=("step:1800" "bursty:1800")
SEEDS=(1 2 3)
DRY_RUN=0
# RESULTS_SUBDIR debe coincidir con el de run_one_ob.sh: el chequeo de resumibilidad
# de abajo consulta el mismo subárbol al que run_one_ob.sh escribe. Default sprint-A.
# Se define antes del banner para que éste muestre el subtree real.
RESULTS_SUBDIR="${RESULTS_SUBDIR:-sprint-A}"

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
echo "  campaña sweep: ${TOTAL} runs (${#CONTROLLERS[@]} ctrl x ${#WORKLOADS[@]} wl x ${#SEEDS[@]} seeds)"
echo "  controllers: ${CONTROLLERS[*]}"
echo "  workloads:   ${WORKLOADS[*]}"
echo "  seeds:       ${SEEDS[*]}"
echo "  results dir: results/${RESULTS_SUBDIR}/"
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
            OUT_DIR="${CODE_ROOT}/results/${RESULTS_SUBDIR}/online-boutique/${WL}/${ctrl}/seed${seed}"

            # Una celda cuenta como completa SOLO si tiene los tres artefactos:
            # metadata.json + instances.csv + failure_modes_summary.csv (el
            # producto de la derivación). Antes el chequeo omitía el summary, así
            # que una celda con la derivación crasheada se salteaba al reanudar y
            # quedaba sin failure_modes_summary para siempre. Exigir el summary
            # fuerza el re-run de celdas a medio derivar.
            if [[ -f "${OUT_DIR}/metadata.json" && -f "${OUT_DIR}/instances.csv" \
                  && -f "${OUT_DIR}/failure_modes_summary.csv" ]]; then
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
