# Reproducibilidad

Hay dos niveles de validación disponibles en esta fase. El primero corre en cualquier máquina con Python 3.10+ y alcanza para verificar que la lógica determinística del paper funciona. El segundo necesita Docker y k3d para correr contra una K8s API real; ya no son mocks pero todavía no hay tráfico ni métricas vivas, solo integración con el cluster.

## Tests sin cluster

Los tests usan `MockPrometheusServer` (que levanta un servidor HTTP real con respuestas pregrabadas) y `MockKubernetesClient`. El `conftest.py` además stub-ea los módulos `kubernetes` y `schedule` antes de la fase de colección, así que la suite corre fully offline:

```bash
git clone https://github.com/f3r21/pbscaler-keff.git
cd pbscaler-keff/PBScaler
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pymoo scikit-learn networkx schedule
pytest tests/
```

La suite tarda alrededor de cuatro segundos y debería reportar 51 passed. La cobertura por archivo está en `docs/evidence/coverage-summary.md`.

## Smoke local en k3d

El smoke local valida que `fetch_pod_states` extrae correctamente `(ready, creation_ts)` de la K8s API real, que `compute_keff` produce valores razonables sobre pods vivos, y que la trayectoria de las tres curvas de warmup se comporta como predice el paper cuando se dispara un escalado real. Lo que no valida es el ciclo completo de PBScaler (detección de anomalías y resolución vía GA), porque eso requiere métricas de Istio que no levantamos en k3d.

Prerequisitos: Docker Desktop corriendo, `k3d` y `kubectl` en el PATH (`brew install k3d kubectl` en Mac), y el entorno Python del nivel anterior.

```bash
bash harness/setup_k3d.sh                # cluster + Online Boutique, tarda 3-5 min
bash harness/smoke_k3d.sh                # smoke estático contra el cluster vivo
python harness/smoke_keff_dynamic.py     # smoke dinámico: cartservice 1 → 2 réplicas
bash harness/teardown_k3d.sh             # limpieza
```

El smoke estático debería terminar con `[smoke] PASS — integración K8s validada`. El dinámico imprime una trayectoria de aproximadamente 35 segundos: la curva escalón se queda en 1.00 hasta $t = T_{\text{cold}}$ y salta a 2.00, la lineal sube uniforme entre los dos valores, y la sigmoide muestra la forma S esperada — lenta al principio, rápida cerca del medio, saturando al final.

Una nota operativa: los servicios Node.js del Online Boutique (`paymentservice`, `currencyservice`) crashean en arranque cuando no hay credenciales de GCP, porque el profiler de Google Cloud exige `PROJECT_ID`. El `setup_k3d.sh` setea `DISABLE_PROFILER=1` para esquivar el problema, pero si encontrás los pods en `CrashLoopBackOff` corré manualmente:

```bash
kubectl set env deployment/paymentservice -n online-boutique DISABLE_PROFILER=1
kubectl set env deployment/currencyservice -n online-boutique DISABLE_PROFILER=1
```

## Problemas comunes

Si `pytest` reporta `ModuleNotFoundError: util.EffectiveCapacity`, lo más probable es que se esté ejecutando desde el directorio equivocado — el `conftest.py` del fork agrega el project root a `sys.path` solo si pytest se invoca desde `PBScaler/`.

El workload `locustfile_trace_driven.py` requiere la traza Alibaba 2018 (alrededor de 900 MB) que no se incluye en el repo por tamaño. El script `benchmarks/online_boutique/data/prepare_alibaba_trace.py` la descarga y la procesa.
