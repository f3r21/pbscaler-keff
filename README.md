# PBScaler-keff

Código de la tesis "Extensión consciente del arranque en frío para PBScaler mediante capacidad efectiva $k_{\text{eff}}(t)$" (Proyecto Final de Carrera, UCSP, 2026). Asesor: Alvaro Henry Mamani Aliaga.

La idea central es simple. PBScaler trata $k_i = 5$ réplicas con tres todavía en warmup igual que cinco réplicas operativas, y eso lo lleva a malas decisiones bajo arranque en frío: agrega capacidad redundante a un servicio cuyas réplicas previas aún no terminaron de calentar (el patrón de "phantom capacity" que el Cap. 3 documenta). Lo que hacemos es reemplazar ese conteo entero por una capacidad efectiva $k_{\text{eff}}(t)$ que pondera fraccionalmente las réplicas en warmup, y propagamos esa señal a los puntos de decisión del controlador donde antes vivía $k_i$.

## Estado

El proyecto está en progreso. Esta versión cubre las dos primeras etapas de integración descritas en el Capítulo 3: la sustitución de $k_i$ por $k_{\text{eff}}(t)$ en el feature vector del clasificador RF sustituto, y el agregado del término ColdStartPenalty en la fitness del algoritmo genético. La modificación al ranking topológico queda para una fase posterior del trabajo.

Los 51 tests unitarios y de integración del fork pasan en limpio. Validamos además la integración contra un cluster Kubernetes real con un smoke local en k3d, que incluye una trayectoria dinámica de warmup donde se ven las tres curvas trackeando un evento de escalado en tiempo real. Ese smoke nos sirvió de paso para encontrar un bug en la sigmoide afín — la fórmula no clampea cuando $t > T_{\text{cold}}$, así que crecía marginalmente por encima de 1.0. Corregido en código con dos tests de regresión.

El RandomForest sustituto que usa el GA sigue siendo el del upstream, entrenado sobre conteos enteros de réplicas. El reentrenamiento sobre features con capacidad fraccional es trabajo posterior; la validación del camino keff en esta fase es independiente de la calidad de predicción del modelo (los tests cubren la lógica determinística, y el smoke real cubre la integración con la K8s API).

## Mapeo con el Capítulo 3

| Etapa | Sección Cap. 3 | Archivos |
|---|---|---|
| 1 — feature vector con $k_{\text{eff}}(t)$ | sec:nivel1 | `PBScaler/util/EffectiveCapacity.py`, `PBScaler/util/GA.py` |
| 2 — ColdStartPenalty en fitness del GA | sec:nivel2 | `PBScaler/util/GA.py`, `PBScaler/config.yaml` |

El upstream PBScaler está vendored en `PBScaler/` a partir del commit `64a1e73` de WHU-AISE/PBScaler. Las extensiones se aplican mediante un módulo nuevo, una subclase nueva, y un hook pequeño en la clase base. La motivación de cada cambio y el detalle código-por-código están en `docs/IMPLEMENTATION_NOTES.md`.

## Cómo probarlo sin cluster

Los tests usan mocks de Prometheus y de la K8s API, así que corren en cualquier máquina con Python 3.10+:

```bash
cd PBScaler
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pymoo scikit-learn networkx schedule
pytest tests/
```

Para el smoke contra un cluster Kubernetes real, `docs/REPRODUCING.md` detalla cómo levantar un k3d local.

## Cita al upstream

> S. Xie et al., "PBScaler: A Bottleneck-Aware Autoscaling Framework for Microservice-Based Applications," _IEEE Transactions on Services Computing_, 2024. [doi:10.1109/TSC.2024.3354060](https://doi.org/10.1109/TSC.2024.3354060)

El benchmark Online Boutique proviene de [GoogleCloudPlatform/microservices-demo](https://github.com/GoogleCloudPlatform/microservices-demo). El benchmark Train Ticket no se incluye en esta fase del proyecto.
