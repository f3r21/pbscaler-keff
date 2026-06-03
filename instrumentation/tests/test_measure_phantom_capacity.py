"""Synthetic tests for measure_phantom_capacity (Cap_3, failure mode 1).

Tests aggregate_by_service against curated kubectl JSON fixtures — does
NOT spawn a real cluster.
"""

from __future__ import annotations

from measure_phantom_capacity import aggregate_by_service


def _pod(app: str, phase: str, container_ready_states: list[bool]) -> dict:
    return {
        "metadata": {"labels": {"app": app}},
        "status": {
            "phase": phase,
            "containerStatuses": [{"ready": r} for r in container_ready_states],
        },
    }


def test_all_ready_yields_zero_delta():
    pods = {
        "items": [
            _pod("frontend", "Running", [True]),
            _pod("frontend", "Running", [True]),
            _pod("frontend", "Running", [True]),
        ]
    }
    rows = aggregate_by_service(pods)
    assert rows == [("frontend", 3, 3)]


def test_pending_pod_counts_declared_but_not_ready():
    pods = {
        "items": [
            _pod("checkout", "Running", [True]),
            _pod("checkout", "Pending", []),  # no containerStatuses yet → not ready
        ]
    }
    rows = aggregate_by_service(pods)
    assert rows == [("checkout", 2, 1)]


def test_failed_pod_excluded_from_declared():
    pods = {
        "items": [
            _pod("ad", "Running", [True]),
            _pod("ad", "Failed", [False]),
        ]
    }
    rows = aggregate_by_service(pods)
    assert rows == [("ad", 1, 1)]


def test_multi_container_pod_only_ready_when_all_ready():
    pods = {
        "items": [
            # main + sidecar — only one ready
            _pod("ts-travel-service", "Running", [True, False]),
            _pod("ts-travel-service", "Running", [True, True]),
        ]
    }
    rows = aggregate_by_service(pods)
    assert rows == [("ts-travel-service", 2, 1)]


def test_unlabeled_pods_grouped_under_placeholder():
    pods = {
        "items": [
            {
                "metadata": {"labels": {}},
                "status": {"phase": "Running", "containerStatuses": [{"ready": True}]},
            },
        ]
    }
    rows = aggregate_by_service(pods)
    assert rows == [("_unlabeled", 1, 1)]


def test_multiple_services_returned_sorted():
    pods = {
        "items": [
            _pod("zservice", "Running", [True]),
            _pod("aservice", "Running", [True]),
            _pod("mservice", "Pending", []),
        ]
    }
    rows = aggregate_by_service(pods)
    services = [r[0] for r in rows]
    assert services == sorted(services)
