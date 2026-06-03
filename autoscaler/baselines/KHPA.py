'''
    kubernetes HPA
'''
import os
import subprocess
import time

from config.Config import Config
from util.KubernetesClient import KubernetesClient
import schedule


class KHPA:
    def __init__(self, config: Config):
        self.config = config
        self.k8s_util = KubernetesClient(config)
        self.duration = config.duration

    def start(self):
        # need rights of administrators
        print('启动 KHPA')
        # Repo consolidado: los scripts HPA viven en baselines/HPA/ (antes others/HPA/).
        # Resolver relativo a este archivo, independiente del cwd.
        hpa_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'HPA',
            '%s.sh' % self.config.namespace)
        # subprocess (sin shell) evita inyección vía namespace; check=True falla fuerte
        # si el manifiesto HPA no aplica (no produce una celda KHPA inválida en silencio).
        subprocess.run(['sh', hpa_script], check=True)
        time.sleep(self.duration)
