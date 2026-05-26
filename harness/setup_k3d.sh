#!/usr/bin/env bash
# Set up a local k3d cluster with Online Boutique for PBScaler-keff
# integration validation (K8s-side only — no Istio, no Prometheus).
#
# This setup is intentionally minimal. The smoke that runs against it
# validates only the K8s integration path of PBScaler-keff: discovery of
# services, fetch_pod_states, compute_keff. The full anomaly->GA->scale
# loop requires Istio metrics and is validated separately on GKE.
#
# Prerequisites (checked at startup):
#   - Docker Desktop running
#   - k3d, kubectl in PATH
#
# Outputs:
#   - cluster `pbscaler-keff-test` reachable via `kubectl`
#   - 10 OB deployments in namespace `online-boutique` (loadgenerator + hpa
#     are skipped — they conflict with PBScaler-keff's role)
set -euo pipefail

CLUSTER_NAME="pbscaler-keff-test"
APP_NAMESPACE="online-boutique"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFESTS_DIR="${CODE_ROOT}/pbscaler-keff/PBScaler/benchmarks/microservices-demo/kubernetes-manifests"

echo "==> Step 0: prerequisites"
for cmd in docker k3d kubectl; do
    command -v "${cmd}" >/dev/null 2>&1 || { echo "ERROR: '${cmd}' not in PATH" >&2; exit 1; }
done
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon not running (start Docker Desktop)" >&2
    exit 1
fi
[[ -d "${MANIFESTS_DIR}" ]] || { echo "ERROR: OB manifests not at ${MANIFESTS_DIR}" >&2; exit 1; }
echo "    docker, k3d, kubectl — OK"

echo "==> Step 1: k3d cluster ${CLUSTER_NAME}"
if k3d cluster list 2>/dev/null | grep -q "^${CLUSTER_NAME} "; then
    echo "    cluster already exists — skipping creation"
else
    k3d cluster create "${CLUSTER_NAME}" --servers 1 --agents 2 --wait
fi
kubectl config use-context "k3d-${CLUSTER_NAME}" >/dev/null

echo "==> Step 2: namespace ${APP_NAMESPACE}"
kubectl create namespace "${APP_NAMESPACE}" 2>/dev/null || true

echo "==> Step 3: deploy Online Boutique"
# Skip hpa.yaml (PBScaler manages scaling) and loadgenerator.yaml (locust replaces it).
for f in "${MANIFESTS_DIR}"/*.yaml; do
    name="$(basename "${f}")"
    case "${name}" in
        hpa.yaml|loadgenerator.yaml) continue ;;
    esac
    kubectl apply -n "${APP_NAMESPACE}" -f "${f}" >/dev/null
done

echo "    waiting for deployments to become available (timeout 300s)..."
kubectl wait --for=condition=available deployment --all \
    -n "${APP_NAMESPACE}" --timeout=300s

echo ""
echo "=============================================================="
echo "  Setup complete."
echo "=============================================================="
POD_COUNT=$(kubectl get pods -n "${APP_NAMESPACE}" --no-headers 2>/dev/null | wc -l | tr -d ' ')
READY_COUNT=$(kubectl get pods -n "${APP_NAMESPACE}" --no-headers 2>/dev/null | awk '$3=="Running"' | wc -l | tr -d ' ')
echo "  Cluster:  k3d-${CLUSTER_NAME}"
echo "  Pods:     ${READY_COUNT}/${POD_COUNT} Running in ${APP_NAMESPACE}"
echo ""
echo "  Next:    bash ${SCRIPT_DIR}/smoke_k3d.sh"
echo "  Cleanup: bash ${SCRIPT_DIR}/teardown_k3d.sh"
