# Contribución vs. PBScaler original

Qué es contribución de esta tesis (el modelo $k_{\text{eff}}(t)$ y su integración) frente al
código heredado de **PBScaler** (WHU-AISE, IEEE TSC 2024, MIT). La integración son **cuatro
puntos** del pipeline, formalizados en `Cap_3` (`sec:nivel1`–`sec:nivel3`, `sec:scaledown`); se
implementó como archivos nuevos + una subclase con *hooks*, dejando el algoritmo base intacto.

## Los cuatro puntos → archivos

| Punto | Qué hace | Dónde |
|---|---|---|
| **Núcleo** | $k_{\text{eff}}(t)$: curvas de *warmup*, `compute_keff()`, `fetch_pod_states()` | `autoscaler/keff/EffectiveCapacity.py` ★ |
| **Nivel 1** | $k_{\text{eff},i}(t)$ en lugar de $k_i$ en el vector del RF | `autoscaler/util/GA.py` (fitness) + `keff/EffectiveCapacity.py` |
| **Nivel 2** | `ColdStartPenalty` en el *fitness* del GA (α=β=0.45, λ=0.10) | `autoscaler/util/GA.py` |
| **Nivel 3** | Pondera TopoRank por $k_i/k_{\text{eff},i}(t)$ (M=10, ε=0.1) | `autoscaler/keff/PBScalerKeff.py` (`cal_topology_potential`) ★ |
| **Anti-*scale-down*** | Bloquea *scale-down* durante el *warmup* ($k_{\text{eff}}-1 \geq$ min\_pod) | `autoscaler/keff/PBScalerKeff.py` (`_filter_waste_candidates`) ★ |

`PBScalerKeff` hereda de `PBScaler` y hace los Niveles 3 + anti-SD vía *hooks*; los Niveles 1+2
viven en el *fitness* del GA.

## Inventario

- **Nuevo (★):** `autoscaler/keff/`, `baselines/NaiveTemporalGate.py` y sus tests, todo
  `harness/` e `instrumentation/`, los `locustfile_*.py`, el bloque `keff` de `config.yaml`.
- **Upstream modificado:** `util/GA.py` (motor geatpy→pymoo + fitness keff), `config/Config.py`
  (bloque keff), `main.py` (selección por env var), `PBScaler.py` (*hooks* + logging, ~90% no invasivo).
- **Heredado sin cambios (MIT):** núcleo de `PBScaler.py` (anomalías, PageRank/TopoRank, GA),
  `util/`, `monitor/`, `simulation/`, baselines `HPA/KHPA/MicroScaler/Showar`.

## Provenance y licencia

El `diff` exacto contra upstream está en el commit `8645bb8` del *fork*
([f3r21/PBScaler](https://github.com/f3r21/PBScaler)): `git diff upstream/main...8645bb8`.
PBScaler original: **WHU-AISE**, **MIT** (ver `LICENSE`); el copyright se preserva y las
modificaciones se documentan aquí.
