#!/usr/bin/env bash
# Delete the local k3d cluster created by setup_k3d.sh.
set -euo pipefail

CLUSTER_NAME="pbscaler-keff-test"

if k3d cluster list 2>/dev/null | grep -q "^${CLUSTER_NAME} "; then
    k3d cluster delete "${CLUSTER_NAME}"
    echo "Deleted cluster ${CLUSTER_NAME}"
else
    echo "Cluster ${CLUSTER_NAME} not found — nothing to do"
fi
