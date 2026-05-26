#!/usr/bin/env bash
# Run the K8s-integration smoke for PBScaler-keff against the local k3d
# cluster set up by setup_k3d.sh. Validates only the keff hot path against
# real K8s API objects; the full PBScaler decision loop is validated on
# GKE where Istio metrics are available.
set -euo pipefail

CLUSTER_NAME="pbscaler-keff-test"
APP_NAMESPACE="online-boutique"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FORK_DIR="${CODE_ROOT}/pbscaler-keff/PBScaler"

# Sanity: cluster reachable.
if ! kubectl config current-context | grep -q "^k3d-${CLUSTER_NAME}$"; then
    echo "WARN: kubectl context is not k3d-${CLUSTER_NAME}; switching"
    kubectl config use-context "k3d-${CLUSTER_NAME}"
fi

if ! kubectl get ns "${APP_NAMESPACE}" >/dev/null 2>&1; then
    echo "ERROR: namespace ${APP_NAMESPACE} not found — run setup_k3d.sh first" >&2
    exit 1
fi

# Pre-flight: print pod counts for visibility.
echo "==> pod overview in ${APP_NAMESPACE}:"
kubectl get pods -n "${APP_NAMESPACE}" -o wide 2>/dev/null \
    | awk 'NR==1 || $3=="Running" {print "    " $0}'

# Run the Python smoke from the fork root so its relative imports work.
echo ""
echo "==> running smoke_keff_init.py"
cd "${FORK_DIR}"
PBSCALER_CONTROLLER=PBScaler-keff python3 "${SCRIPT_DIR}/smoke_keff_init.py"
SMOKE_EXIT=$?

echo ""
if [[ ${SMOKE_EXIT} -eq 0 ]]; then
    echo "=============================================================="
    echo "  Smoke PASS — K8s integration of PBScaler-keff is healthy."
    echo "  Next: provision GKE for the full anomaly->GA->scale validation."
    echo "=============================================================="
else
    echo "=============================================================="
    echo "  Smoke FAIL (exit ${SMOKE_EXIT}) — see error above."
    echo "  Common causes: pods not Running, kubeconfig mismatch, import error."
    echo "=============================================================="
fi
exit ${SMOKE_EXIT}
