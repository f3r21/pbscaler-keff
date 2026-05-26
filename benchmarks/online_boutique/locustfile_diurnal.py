"""Diurnal workload for Online Boutique (Cap_3 sec:patrones de carga).

Shape: 4 hours total, compressed 6× from a 24-hour diurnal pattern.
Six full sinusoidal cycles, period = 40 minutes (2400 s).

    users(t) = midpoint + amplitude * sin(2π * t / period)
        peak    = 2.5 N
        valley  = 0.3 N
        midpoint  = 1.4 N
        amplitude = 1.1 N

Cap_3 sec:patrones de carga specifies 4 h fiel.

Deterministic — no RNG in the shape.
"""

import math
import random
from locust import LoadTestShape

from _common import BASE_USERS, BoutiqueUser, get_seed


class DiurnalUser(BoutiqueUser):
    pass


_seed = get_seed()
if _seed is not None:
    random.seed(_seed)


class DiurnalShape(LoadTestShape):
    """Sinusoidal load: 6 cycles × 40 min = 4 h fiel."""

    TOTAL_S = 14400           # 4 hours fiel a Cap_3
    PERIOD_S = 2400           # 40 min per cycle (6 cycles in 4 h)
    PEAK_USERS = int(BASE_USERS * 2.5)   # 500
    VALLEY_USERS = int(BASE_USERS * 0.3)  # 60
    SPAWN_RATE = 10

    def tick(self):
        t = self.get_run_time()
        if t >= self.TOTAL_S:
            return None
        midpoint = (self.PEAK_USERS + self.VALLEY_USERS) / 2.0
        amplitude = (self.PEAK_USERS - self.VALLEY_USERS) / 2.0
        users = midpoint + amplitude * math.sin(2.0 * math.pi * t / self.PERIOD_S)
        return max(1, int(round(users))), self.SPAWN_RATE
