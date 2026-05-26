"""Continuously sample pod readiness in a Kubernetes namespace.

Phantom capacity is the gap between *declared* replicas (what the autoscaler
believes is running) and *Ready* replicas (what is actually serving traffic).
This script polls `kubectl get pods -o json` every `--interval` seconds and
writes one row per (timestamp, service) tuple, where:

    declared = pods whose `status.phase != Failed`
    ready    = pods whose `status.containerStatuses[*].ready == True` for ALL
               containers in the pod
    delta    = declared - ready   (≥ 0 always, > 0 means phantom capacity)

Service identity comes from the pod label `app` (the Online Boutique and
Train Ticket manifests both use it). Pods without an `app` label are grouped
under "_unlabeled".

Output CSV columns:
    timestamp,service,declared,ready,delta

The CSV is flushed after every poll so a SIGINT or run-over does not lose
samples. Designed to run in parallel with PBScaler during Fase G.

Usage:
    python measure_phantom_capacity.py \\
        --namespace online-boutique \\
        --duration 1860 \\
        --interval 5 \\
        --out /path/to/phantom_capacity.csv

Cap_3 sec:nivel1 — `k_eff_i(t)` is defined precisely so that this delta
becomes the autoscaler's effective-capacity adjustment.
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

DEFAULT_INTERVAL_S: Final[int] = 5
KUBECTL_BIN: Final[str] = "kubectl"


def fetch_pods(namespace: str) -> dict:
    """Return parsed `kubectl get pods -o json` output for the namespace."""
    result = subprocess.run(
        [KUBECTL_BIN, "get", "pods", "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


def aggregate_by_service(pods_json: dict) -> list[tuple[str, int, int]]:
    """Return list of (service, declared, ready) tuples.

    declared: pods with phase != Failed (so Pending, Running, Succeeded all count)
    ready:    pods where every containerStatuses[*].ready is True
    """
    declared: dict[str, int] = defaultdict(int)
    ready: dict[str, int] = defaultdict(int)

    for pod in pods_json.get("items", []):
        labels = pod.get("metadata", {}).get("labels", {}) or {}
        service = labels.get("app") or "_unlabeled"
        status = pod.get("status", {})
        phase = status.get("phase", "")
        if phase == "Failed":
            continue
        declared[service] += 1
        container_statuses = status.get("containerStatuses") or []
        if container_statuses and all(cs.get("ready", False) for cs in container_statuses):
            ready[service] += 1

    services = sorted(set(declared) | set(ready))
    return [(svc, declared[svc], ready[svc]) for svc in services]


def write_header_if_needed(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "service", "declared", "ready", "delta"])


def append_rows(path: Path, rows: list[tuple[str, str, int, int, int]]) -> None:
    with path.open("a", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)


def run_loop(namespace: str, duration_s: int, interval_s: int, out_path: Path) -> int:
    write_header_if_needed(out_path)
    deadline = time.monotonic() + duration_s

    stop = {"flag": False}

    def _handle_signal(signum: int, _frame) -> None:
        stop["flag"] = True
        print(f"[phantom_capacity] received signal {signum}, stopping", file=sys.stderr)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    samples_written = 0
    next_tick = time.monotonic()
    while not stop["flag"] and time.monotonic() < deadline:
        ts_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            pods_json = fetch_pods(namespace)
        except subprocess.CalledProcessError as exc:
            print(
                f"[phantom_capacity] kubectl error: {exc.stderr or exc}",
                file=sys.stderr,
            )
        except subprocess.TimeoutExpired:
            print("[phantom_capacity] kubectl timeout", file=sys.stderr)
        except json.JSONDecodeError as exc:
            print(f"[phantom_capacity] kubectl returned non-JSON: {exc}", file=sys.stderr)
        else:
            agg = aggregate_by_service(pods_json)
            rows = [
                (ts_iso, svc, declared, ready, declared - ready)
                for svc, declared, ready in agg
            ]
            append_rows(out_path, rows)
            samples_written += len(rows)

        next_tick += interval_s
        sleep_for = next_tick - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            # We fell behind — re-anchor so we don't burst-poll.
            next_tick = time.monotonic()

    print(
        f"[phantom_capacity] done — wrote {samples_written} rows to {out_path}",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--namespace", required=True, help="Kubernetes namespace to watch")
    ap.add_argument("--duration", type=int, required=True, help="how many seconds to sample")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S,
                    help=f"poll interval in seconds (default {DEFAULT_INTERVAL_S})")
    ap.add_argument("--out", type=Path, required=True, help="output CSV path")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    return run_loop(args.namespace, args.duration, args.interval, args.out)


if __name__ == "__main__":
    sys.exit(main())
