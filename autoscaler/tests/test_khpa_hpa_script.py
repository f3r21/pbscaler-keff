"""Tests para el script HPA del baseline KHPA (regresión del bug 2026-06-03).

KHPA.py resuelve ``baselines/HPA/<namespace>.sh`` con ``namespace`` =
``config.yaml: kubernetes.namespace`` y lo corre con ``subprocess.run(check=True)``.
Faltaba ``online-boutique.sh`` → KHPA crasheaba en el Step 9 (run_exit_code=1) y
perdía sus celdas en el sweep (visto en results/regime-lite-2026-06/).

Estos tests son offline (no tocan cluster): verifican que el script para el
namespace configurado EXISTE, es sintácticamente válido (``sh -n``) y aplica el
HPA del proyecto (10 servicios OB, min=1/max=5/cpu=50) en el namespace correcto.

Run with:
    pytest tests/test_khpa_hpa_script.py -v
"""

import os
import subprocess
import unittest

import yaml

_AUTOSCALER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG = os.path.join(_AUTOSCALER, "config.yaml")
_HPA_DIR = os.path.join(_AUTOSCALER, "baselines", "HPA")

# Los 10 servicios de Online Boutique con HPA en
# benchmarks/microservices-demo/kubernetes-manifests/hpa.yaml.
_OB_SERVICES = {
    "adservice", "cartservice", "checkoutservice", "currencyservice",
    "emailservice", "frontend", "paymentservice", "productcatalogservice",
    "recommendationservice", "shippingservice",
}


def _configured_namespace() -> str:
    with open(_CONFIG) as fh:
        cfg = yaml.safe_load(fh)
    return cfg["kubernetes"]["namespace"]


def _hpa_script_path() -> str:
    """Misma resolución que KHPA.start(): HPA/<namespace>.sh."""
    return os.path.join(_HPA_DIR, "%s.sh" % _configured_namespace())


class TestKhpaHpaScript(unittest.TestCase):
    def test_script_for_configured_namespace_exists(self):
        """El bug original: el script del namespace configurado no existía."""
        path = _hpa_script_path()
        self.assertTrue(
            os.path.isfile(path),
            "Falta %s — KHPA.py lo correría con check=True y crashearía "
            "(run_exit_code=1, sin colección). Crear el HPA del namespace." % path,
        )

    def test_script_is_syntactically_valid(self):
        """`sh -n` valida la sintaxis sin ejecutar kubectl."""
        path = _hpa_script_path()
        proc = subprocess.run(
            ["sh", "-n", path], capture_output=True, text=True
        )
        self.assertEqual(
            proc.returncode, 0, "sh -n falló:\n%s" % proc.stderr
        )

    def test_autoscales_all_ob_services_with_project_params(self):
        """Cubre los 10 servicios OB con el namespace y params del proyecto."""
        path = _hpa_script_path()
        with open(path) as fh:
            body = fh.read()

        ns = _configured_namespace()
        for svc in _OB_SERVICES:
            self.assertIn(
                svc, body, "El HPA no menciona el servicio %s" % svc
            )
        # Namespace y params del proyecto (hpa.yaml: min=1, max=5, cpu=50).
        # El script fija NS=<namespace> y pasa -n "$NS" a cada autoscale.
        self.assertIn("NS=%s" % ns, body)
        self.assertIn('-n "$NS"', body)
        self.assertIn("--max=5", body)
        self.assertIn("--cpu-percent=50", body)
        # No debe arrastrar los defaults upstream de hipster.sh (max=8/cpu=80).
        self.assertNotIn("--max=8", body)
        self.assertNotIn("--cpu-percent=80", body)


if __name__ == "__main__":
    unittest.main()
