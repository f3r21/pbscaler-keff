# Notas de implementación

Este documento mapea el Capítulo 3 (Propuesta) de la tesis con el código fuente y explica las decisiones de diseño que no se ven en el diff. Las referencias del estilo `sec:nivel1`, `eq:keff`, etc. corresponden a etiquetas LaTeX del manuscrito.

## Arquitectura general

El upstream PBScaler ya hace casi todo lo necesario: detecta anomalías, identifica el servicio raíz vía PageRank sobre el grafo de llamadas, optimiza el conteo de réplicas con un algoritmo genético, y aplica el escalado. La intervención del paper de tesis cambia la magnitud que entra al GA (de $k_i$ entero a $k_{\text{eff}}(t)$ fraccional) y agrega un término al fitness que penaliza generar muchas réplicas nuevas en servicios con arranque lento.

Para no romper los baselines existentes (PBScaler vanilla, KHPA, NaiveTemporalGate, SHOWAR, MicroScaler) hicimos un solo cambio en la clase `PBScaler`: agregar un punto de inyección de kwargs al llamar a `GA.set_env`. Todo lo demás vive en una subclase nueva, `PBScalerKeff`, y un módulo nuevo, `util/EffectiveCapacity.py`. Los baselines que existían siguen pasando los mismos 13 tests del upstream sin tocar una línea.

## Etapa 1 — feature vector con $k_{\text{eff}}(t)$

El módulo `util/EffectiveCapacity.py` agrupa las tres funciones de warmup definidas en el Cap. 3 (escalón, lineal, sigmoide) y la agregación `compute_keff` que aplica `eq:keff`: cada pod Ready aporta $1.0$, cada pod aún calentando aporta $f_{\text{curve}}(\Delta t, T_{\text{cold}})$ donde $\Delta t$ es la edad del pod desde su `creation_timestamp`. La sigmoide afín del Cap. 3 satisface $f(0)=0$ y $f(T_{\text{cold}})=1$ por construcción, pero fuera de ese intervalo la función subyacente no está acotada; clampeamos a $[0, 1]$ en el módulo. El bug original (sigmoide reportando 2.01 en lugar de 2.00 cuando $t > T_{\text{cold}}$) se manifestó en el smoke dinámico y está cubierto por dos tests de regresión.

Para alimentar este $k_{\text{eff}}$ al RandomForest sustituto, `util/GA.py:fitness` recibe ahora un parámetro opcional `pod_states_by_svc` vía `set_env`. Cuando está presente, las entradas no-bottleneck del feature vector usan $k_{\text{eff}}(t)$ medido en lugar del conteo nominal `r[svc]`; las entradas bottleneck siguen llevando `action[index]` (el candidato que el GA está evaluando). Si no se pasan los parámetros de keff, `set_env` opera en modo legacy y reproduce exactamente la fitness del upstream — esto es lo que permite que los tests del baseline PBScaler vanilla pasen sin modificación.

## Etapa 2 — ColdStartPenalty en fitness del GA

La fitness original combinaba violación de SLO predicha (R1) y costo de réplicas (R2) con pesos iguales (`LAMBDA = 0.5`). El Cap. 3 (`eq:fitness_new`) agrega un tercer término normalizado que penaliza proponer muchas réplicas nuevas en servicios con $T_{\text{cold}}$ alto:

$$\text{fitness} = \alpha \cdot R_1 + \beta \cdot R_2 + \lambda \cdot \widehat{\text{CSP}}, \quad \alpha + \beta + \lambda = 1$$

con $\alpha = \beta = 0{,}45$ y $\lambda = 0{,}10$ por defecto (Cap_3:161). El `ColdStartPenalty` absoluto sale de `eq:csp` — suma de $\Delta k_i \cdot T_{\text{cold},i}$ contando solo los $\Delta k_i$ positivos — y se normaliza con `csp_max` precomputado en `set_env` per `eq:csp_max`. Renombramos la constante `LAMBDA` original a `OBJECTIVE_BALANCE` para no colisionar semánticamente con el $\lambda$ nuevo del paper.

Una sutileza: el GA del upstream maximiza la fitness (`goal='max'`) y CSP es una métrica de costo (más alta = peor). En lugar de cambiar el sentido de optimización, escribimos el tercer término como $\lambda \cdot (1 - \widehat{\text{CSP}})$, que es matemáticamente equivalente a $-\lambda \cdot \widehat{\text{CSP}}$ módulo una constante y preserva intacta la convención `goal='max'`. Los parámetros $\alpha$, $\beta$, $\lambda$ y la curva de warmup activa viven en un bloque `keff:` nuevo en `config.yaml`; los valores de $T_{\text{cold},i}$ se reutilizan de `temporal_gate.cold_times` para que el baseline `NaiveTemporalGate` y `PBScaler-keff` queden sincronizados sin duplicar configuración.

## Hook de extensión en la clase base

El único cambio en `PBScaler.py` es agregar `_ga_extra_set_env_kwargs(mss)` con default `{}`. La subclase `PBScalerKeff` lo override-a para inyectar `pod_states_by_svc + t_cold_by_svc + warmup_curve` al GA. Si se invoca el controlador vanilla, el hook devuelve dict vacío y `set_env` opera idéntico al upstream.

## Operación

El controlador activo se selecciona vía la variable de entorno `PBSCALER_CONTROLLER` (default `PBScaler`). Esto resuelve la deuda que el CLAUDE.md original del fork documentaba como issue conocido — "the active controller is hardcoded; switching baselines requires editing the source".

El script `scripts/profile_cold_start.py` mide $T_{\text{cold},i}$ por servicio en el cluster siguiendo el procedimiento del Cap. 3 (`sec:obtain_tcold`): escala cada deployment en uno N veces, registra el delta entre `creation_timestamp` y la primera transición a `Ready=True`, y reporta el P95 muestral. El umbral $N \geq 30$ proviene de la regla de Hahn–Meeker (Cap_3:77); el script avisa por log si el usuario pide menos. La salida es JSON con un dict `t_cold_p95_seconds` listo para pegar en `config.yaml temporal_gate.cold_times`. En el smoke local con k3d observamos que el $T_{\text{cold}}$ real de cartservice (Ready=True a $t = 35$ s) excede al configurado por defecto (15 s) por más de 2×, lo que confirma que medir antes de los experimentos no es opcional.

`harness/run_sweep.sh` itera el producto cartesiano de controladores, patrones y semillas, y produce los runs principales del experimento. Cada celda se almacena en `results/online-boutique/<workload>/<controller>/seed<rep>/` con su `metadata.json`, `instances.csv` y `latency.csv`. El sweep es resumible: celdas con `metadata.json + instances.csv` presentes se saltean.
