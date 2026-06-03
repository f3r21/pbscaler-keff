#!/bin/sh
# online-boutique.sh — HPA del baseline KHPA para Online Boutique.
#
# KHPA.py resuelve HPA/<namespace>.sh con namespace='online-boutique'
# (autoscaler/config.yaml). Antes faltaba este archivo: KHPA buscaba
# 'online-boutique.sh', no existía, y subprocess.run(check=True) crasheaba el
# controlador en el Step 9 (run_exit_code=1, sin colección de métricas).
#
# Params del proyecto: benchmarks/microservices-demo/kubernetes-manifests/hpa.yaml
#   minReplicas=1, maxReplicas=5, target CPU=50% (uniforme en los 10 servicios).
# OJO: NO reusar hipster.sh — usa el namespace 'hipster' y max=8/cpu=80 (defaults
# upstream), incompatibles con este experimento.
#
# 'set -e' = fallar fuerte si algún autoscale falla (coherente con check=True en
# KHPA.py). El harness borra los HPA previos (kubectl delete hpa --all) antes de
# cada corrida, así que no debería haber colisión AlreadyExists.
set -e

NS=online-boutique

for svc in adservice cartservice checkoutservice currencyservice emailservice \
           frontend paymentservice productcatalogservice recommendationservice \
           shippingservice; do
    kubectl autoscale deployment "$svc" -n "$NS" --min=1 --max=5 --cpu-percent=50
done
