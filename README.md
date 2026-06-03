# PBScaler-$k_{\text{eff}}$

Autoescalado consciente del **arranque en frío** (*cold-start*) para microservicios en
Kubernetes. Extiende [PBScaler](https://github.com/WHU-AISE/PBScaler) (WHU-AISE, IEEE TSC 2024)
con una **función de capacidad efectiva** $k_{\text{eff}}(t)$ que pondera fraccionalmente las
réplicas durante su *warmup*, para no contarlas como capacidad antes de que sirvan tráfico.

Tesis UCSP. Contribución propia vs. heredado: ver [CONTRIBUTIONS.md](CONTRIBUTIONS.md).

## Integración de $k_{\text{eff}}(t)$

1. **Nivel 1** — $k_{\text{eff}}$ en el vector de características del RF sustituto.
2. **Nivel 2** — penalización por arranque en frío en el *fitness* del GA.
3. **Nivel 3** — ponderación del potencial de TopoRank por la fracción de capacidad efectiva.
4. **Anti-*scale-down*** — bloqueo de reducciones prematuras durante el *warmup*.

## Estructura

```
autoscaler/      controlador: PBScaler.py (núcleo upstream + hooks), util/ GA (pymoo), keff/ ★, baselines/, tests/
harness/         experimentos GKE (setup/teardown, runners, profiling de T_cold)
instrumentation/ análisis (agregación, tablas, figuras)
benchmarks/      Online Boutique (manifests + locustfiles)
```

## Uso

```bash
pip install -r requirements.txt
cd autoscaler         && python3 -m pytest tests/ -q   # 64 passed
cd ../instrumentation && python3 -m pytest tests/ -q   # 72 passed
```

Reproducción en cluster y pipeline de análisis: [REPRODUCE.md](REPRODUCE.md). Los **datos
crudos no se incluyen**; los resultados y su discusión están en el manuscrito (`Cap_4`).

## Licencia

Trabajo derivado de **PBScaler** (WHU-AISE), licencia **MIT** (ver `LICENSE` y `NOTICE`).
