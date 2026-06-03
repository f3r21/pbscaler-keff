# Reproducción

Los **datos crudos no se incluyen**. Este repo trae el código del autoescalador, el harness y
el pipeline de análisis. Los tests y el análisis corren sin cluster; la corrida experimental
requiere GKE.

## Entorno

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # GA = pymoo; locust se instala aparte para correr en cluster
```

El RF sustituto entrenado se incluye en `autoscaler/simulation/boutique/RandomForestClassify.model`
(necesario al arrancar el controlador).

## Tests (sin cluster)

```bash
cd autoscaler         && python3 -m pytest tests/ -q   # 64 passed (+3 xfailed)
cd ../instrumentation && python3 -m pytest tests/ -q   # 72 passed
```

## Análisis

Con un árbol de resultados `<results>` (4 controladores × {step, bursty} × N=3, ver abajo):

```bash
python3 instrumentation/aggregate_results.py <results> --out <results>/aggregate.csv
python3 instrumentation/build_comparison_descriptive.py --aggregate <results>/aggregate.csv \
    --out-master <results>/tabla.md --baseline vanilla --controller-order vanilla keff naive khpa
python3 instrumentation/build_comparison_plots.py --aggregate <results>/aggregate.csv --out-dir <results>/figs
```

Métricas *checkout-scoped* (SLO 500 ms P90, `Cap_3:238`); reporte N=3, mediana + rango, sin inferencia.

## Correr en GKE (requiere cluster + costo)

```bash
bash harness/setup_gke.sh
python3 harness/profile_cold_start.py --namespace online-boutique --n-samples 30 --output /tmp/tcold.json
python3 harness/apply_tcold_profile.py --profile /tmp/tcold.json --config autoscaler/config.yaml
bash harness/run_sweep.sh --controllers PBScaler-keff NaiveTemporalGate KHPA PBScaler \
     --workloads step:1800 bursty:1800 --seeds 1 2 3
bash harness/teardown_gke.sh
```

Perfilar `T_cold` en el cluster concreto es obligatorio. `run_sweep.sh` es resumible (salta
celdas con resultados). Alternativa con teardown garantizado: `bash harness/run_focal_campaign.sh`.
Patrones de carga (Locust closed-loop, U=200, think-time 1–5 s):

- **step**: 200 usuarios, salto a 600 (3×) a los 600 s.
- **bursty**: 200 baseline + *spikes* a 600 (10 s cada 60 s).

## Controlador

`main.py` lo elige por la env var `PBSCALER_CONTROLLER` ∈ {`PBScaler`, `PBScaler-keff`,
`NaiveTemporalGate`, `KHPA`, …}. La curva de *warmup* se fija en `autoscaler/config.yaml`
(`keff.warmup_curve`; este repo usa `step`).

## Provenance

Autoescalador evaluado = commit `8645bb8` del *fork*
([f3r21/PBScaler](https://github.com/f3r21/PBScaler)). Infraestructura de la campaña:
GKE K8s 1.35.3, Istio 1.24.3, `e2-standard-4` × 3.
