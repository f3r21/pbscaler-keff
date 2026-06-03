"""
conftest.py — pytest fixtures and import stubs for PBScaler tests.

Several PBScaler dependencies are heavy or cluster-specific and are not
available in a local dev environment:
  - ``kubernetes``  requires a live kubeconfig
  - ``schedule``    optional runtime dependency not in all venvs

We stub these here before any test module is collected so the suite
runs fully offline without these packages installed.
"""

import os
import sys
import types
from unittest.mock import MagicMock

# Los scripts de harness/ (p.ej. profile_cold_start.py) viven fuera del paquete
# del autoescalador; los hacemos importables para los tests que los cubren.
_HARNESS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "harness"))
if _HARNESS not in sys.path:
    sys.path.insert(0, _HARNESS)


def _stub_module(name: str, attrs: dict = None):
    """Create and register a minimal stub module."""
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(mod, k, v)
    sys.modules[name] = mod


def _stub_kubernetes():
    if "kubernetes" in sys.modules:
        return

    kubernetes = types.ModuleType("kubernetes")
    client_mod = types.ModuleType("kubernetes.client")
    config_mod = types.ModuleType("kubernetes.config")

    client_mod.CoreV1Api = MagicMock
    client_mod.AppsV1Api = MagicMock
    config_mod.kube_config = MagicMock()
    config_mod.kube_config.load_kube_config = MagicMock()

    kubernetes.client = client_mod
    kubernetes.config = config_mod

    sys.modules["kubernetes"] = kubernetes
    sys.modules["kubernetes.client"] = client_mod
    sys.modules["kubernetes.config"] = config_mod


def _stub_schedule():
    """Stub the ``schedule`` module used by PBScaler.start()."""
    _stub_module("schedule", {
        "every": MagicMock(return_value=MagicMock()),
        "clear": MagicMock(),
        "run_pending": MagicMock(),
    })


_stub_kubernetes()
_stub_schedule()
